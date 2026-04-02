"""Unix domain socket Modbus/TCP frontend."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from .base import FrontendBase
from ..config.models import UnixModbusTcpConfig
from ..core.bus import GatewayBus
from ..core.messages import ModbusRequest, RequestContext, RoutedRequest, RoutedResponse

logger = logging.getLogger(__name__)


@dataclass
class PendingRequest:
    request_id: str
    writer: asyncio.StreamWriter
    transaction_id: int
    protocol_id: int
    unit_id: int
    client_id: str


class UnixModbusTCPFrontend(FrontendBase):
    """Expose a Unix socket speaking Modbus/TCP frames."""

    def __init__(self, config: UnixModbusTcpConfig, bus: GatewayBus) -> None:
        self.config = config
        self.bus = bus
        self.name = config.id
        self._server: asyncio.base_events.Server | None = None
        self._socket_path = Path(config.socket_path)
        self._client_counter = 0
        self._current_clients = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending: Dict[str, PendingRequest] = {}

    async def start(self) -> None:
        if self._server is not None:
            return
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self._socket_path))
        logger.info('Unix Modbus/TCP frontend %s listening on %s', self.name, self._socket_path)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None
        for task in list(self._tasks):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._pending.clear()
        if self._socket_path.exists():
            self._socket_path.unlink()

    async def handle_response(self, message: RoutedResponse) -> None:
        request_id = message.context.request_id
        if not request_id:
            return
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        adu = self._response_adu(message)
        if adu is None:
            return
        frame = self._build_mbap(pending.transaction_id, pending.protocol_id, len(adu)) + adu
        try:
            pending.writer.write(frame)
            await pending.writer.drain()
        except Exception:  # pragma: no cover - best effort
            logger.exception('Failed to write Unix Modbus/TCP response for %s', request_id)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._current_clients >= self.config.max_clients:
            logger.warning('Unix frontend %s refusing connection: max clients reached', self.name)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
        self._current_clients += 1
        self._client_counter += 1
        client_id = f"{self.name}-u{self._client_counter}"
        peercred: Tuple[int, int, int] | None = writer.get_extra_info('peercred')
        peername = writer.get_extra_info('peername')
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction_id = int.from_bytes(header[0:2], byteorder='big')
                protocol_id = int.from_bytes(header[2:4], byteorder='big')
                length = int.from_bytes(header[4:6], byteorder='big')
                unit_id = header[6]
                if length < 1:
                    logger.warning('Malformed Modbus/TCP length field from %s', client_id)
                    continue
                payload = await reader.readexactly(length - 1)
                if protocol_id != 0:
                    continue
                adu = bytes([unit_id]) + payload
                try:
                    request = ModbusRequest.from_adu(adu)
                except Exception:
                    logger.exception('Failed to decode Modbus request from %s', client_id)
                    continue
                request_id = f"{client_id}:{transaction_id}"
                metadata = {
                    'client_id': client_id,
                    'transport': 'unix_modbus_tcp',
                    'socket_path': str(self._socket_path),
                    'peername': peername,
                }
                if peercred is not None:
                    metadata.update({'peer_pid': peercred[0], 'peer_uid': peercred[1], 'peer_gid': peercred[2]})
                context = RequestContext(frontend=self.name, request_id=request_id, metadata=metadata)
                routed = RoutedRequest(context=context, pdu=request)
                self._pending[request_id] = PendingRequest(
                    request_id=request_id,
                    writer=writer,
                    transaction_id=transaction_id,
                    protocol_id=protocol_id,
                    unit_id=unit_id,
                    client_id=client_id,
                )
                await self.bus.publish('requests', routed)
        except asyncio.IncompleteReadError:
            pass
        finally:
            to_remove = [req_id for req_id, info in self._pending.items() if info.client_id == client_id]
            for req_id in to_remove:
                self._pending.pop(req_id, None)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if task:
                self._tasks.discard(task)
            self._current_clients = max(0, self._current_clients - 1)

    @staticmethod
    def _build_mbap(transaction_id: int, protocol_id: int, adu_length: int) -> bytes:
        return (
            transaction_id.to_bytes(2, 'big')
            + protocol_id.to_bytes(2, 'big')
            + adu_length.to_bytes(2, 'big')
        )

    @staticmethod
    def _response_adu(message: RoutedResponse) -> bytes | None:
        pdu = message.response or message.exception
        if pdu is None:
            return None
        return pdu.to_adu()
