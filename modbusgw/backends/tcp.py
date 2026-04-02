"""TCP Modbus backend implementation."""
from __future__ import annotations

import asyncio
import contextlib
import ssl
from dataclasses import dataclass

from .base import BackendBase
from ..config.models import TcpBackendConfig
from ..core.messages import (
    ModbusExceptionResponse,
    ModbusResponse,
    RoutedRequest,
    RoutedResponse,
    ResponseContext,
)


@dataclass(eq=False)
class TcpConnection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    def __hash__(self) -> int:
        return id(self)

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


class TcpModbusBackend(BackendBase):
    """Proxy Modbus/TCP requests to upstream devices over TCP or TLS."""

    def __init__(self, config: TcpBackendConfig) -> None:
        self.config = config
        self.name = config.id
        self._available: asyncio.Queue[TcpConnection] = asyncio.Queue()
        self._connections: set[TcpConnection] = set()
        self._total_connections = 0
        self._lock = asyncio.Lock()
        self._tx_counter = 0
        self._response_timeout = config.connect_timeout

    async def submit(self, routed_request: RoutedRequest) -> RoutedResponse:  # type: ignore[override]
        conn = await self._acquire_connection()
        try:
            response = await self._exchange(conn, routed_request)
        except Exception as exc:
            await self._retire_connection(conn)
            raise RuntimeError('TCP backend exchange failed') from exc
        else:
            await self._release_connection(conn)
            return response

    async def close(self) -> None:
        while not self._available.empty():
            conn = await self._available.get()
            self._connections.discard(conn)
            await conn.close()
        for conn in list(self._connections):
            await conn.close()
        self._connections.clear()
        self._total_connections = 0

    async def _exchange(self, conn: TcpConnection, routed_request: RoutedRequest) -> RoutedResponse:
        transaction_id = self._next_transaction_id()
        adu = routed_request.pdu.to_adu()
        unit_id = adu[0]
        pdu_payload = adu[1:]
        length = len(pdu_payload) + 1
        frame = self._build_mbap(transaction_id, length) + bytes([unit_id]) + pdu_payload
        conn.writer.write(frame)
        await conn.writer.drain()
        header = await asyncio.wait_for(conn.reader.readexactly(6), timeout=self._response_timeout)
        response_transaction = int.from_bytes(header[0:2], 'big')
        if response_transaction != transaction_id:
            raise RuntimeError('Mismatched Modbus/TCP transaction id')
        length = int.from_bytes(header[4:6], 'big')
        if length < 1:
            raise RuntimeError('Malformed Modbus/TCP response length')
        unit_bytes = await asyncio.wait_for(conn.reader.readexactly(1), timeout=self._response_timeout)
        payload = await asyncio.wait_for(conn.reader.readexactly(length - 1), timeout=self._response_timeout)
        response = ModbusResponse.from_adu(unit_bytes + payload)
        if isinstance(response, ModbusExceptionResponse):
            response_obj = RoutedResponse(
                context=ResponseContext(
                    frontend=routed_request.context.frontend,
                    backend=self.name,
                    request_id=routed_request.context.request_id,
                ),
                request=routed_request.pdu,
                response=None,
                exception=response,
            )
        else:
            response_obj = RoutedResponse(
                context=ResponseContext(
                    frontend=routed_request.context.frontend,
                    backend=self.name,
                    request_id=routed_request.context.request_id,
                ),
                request=routed_request.pdu,
                response=response,
                exception=None,
            )
        return response_obj

    async def _acquire_connection(self) -> TcpConnection:
        try:
            return self._available.get_nowait()
        except asyncio.QueueEmpty:
            pass
        async with self._lock:
            if self._total_connections < self.config.pool_size:
                conn = await self._create_connection()
                self._connections.add(conn)
                self._total_connections += 1
                return conn
        return await self._available.get()

    async def _release_connection(self, conn: TcpConnection) -> None:
        await self._available.put(conn)

    async def _retire_connection(self, conn: TcpConnection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)
            self._total_connections -= 1
        await conn.close()

    async def _create_connection(self) -> TcpConnection:
        ssl_context = self._build_ssl_context() if self.config.use_tls else None
        connect = asyncio.open_connection(
            self.config.host,
            self.config.port,
            ssl=ssl_context,
            server_hostname=self.config.host if ssl_context and ssl_context.check_hostname else None,
        )
        reader, writer = await asyncio.wait_for(connect, timeout=self.config.connect_timeout)
        return TcpConnection(reader=reader, writer=writer)

    def _build_ssl_context(self) -> ssl.SSLContext:
        if self.config.tls is None:
            return ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        tls = self.config.tls
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if tls.ca_file:
            ctx.load_verify_locations(str(tls.ca_file))
        if not tls.verify_server_cert:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if tls.cert_file and tls.key_file:
            ctx.load_cert_chain(certfile=str(tls.cert_file), keyfile=str(tls.key_file))
        return ctx

    def _next_transaction_id(self) -> int:
        self._tx_counter = (self._tx_counter + 1) % 0x10000
        if self._tx_counter == 0:
            self._tx_counter = 1
        return self._tx_counter

    @staticmethod
    def _build_mbap(transaction_id: int, adu_length: int) -> bytes:
        return (
            transaction_id.to_bytes(2, 'big')
            + b'\x00\x00'
            + adu_length.to_bytes(2, 'big')
        )
