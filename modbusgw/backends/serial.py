"""Serial backend implementation."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Awaitable, Callable

import serial  # type: ignore
import serial_asyncio  # type: ignore

from .base import BackendBase
from ..config.models import SerialBackendConfig
from ..core.messages import (
    ModbusExceptionResponse,
    ModbusResponse,
    RoutedRequest,
    RoutedResponse,
    ResponseContext,
)
from ..utils.crc import crc16_modbus

SerialSessionFactory = Callable[[SerialBackendConfig], Awaitable['SerialSession']]


class SerialSession:
    """Async serial session placeholder/interface."""

    async def exchange(self, frame: bytes) -> bytes:  # pragma: no cover - placeholder
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - placeholder
        pass


class SerialAsyncSession(SerialSession):
    """serial_asyncio-powered Modbus RTU session."""

    _MAX_FRAME = 256

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        timeout: float,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = timeout
        self._lock = asyncio.Lock()

    async def exchange(self, frame: bytes) -> bytes:
        async with self._lock:
            self._writer.write(frame)
            await self._writer.drain()
            return await self._read_response()

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()

    async def _read_response(self) -> bytes:
        buffer = bytearray()
        while True:
            try:
                chunk = await asyncio.wait_for(self._reader.readexactly(1), timeout=self._timeout)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                raise TimeoutError('Serial response timed out') from exc
            if not chunk:
                raise ConnectionError('Serial device returned EOF')
            buffer.extend(chunk)
            if len(buffer) > self._MAX_FRAME:
                raise ValueError('Serial response exceeded maximum frame length')
            if len(buffer) >= 4 and self._has_valid_crc(buffer):
                return bytes(buffer)

    @staticmethod
    def _has_valid_crc(buffer: bytearray) -> bool:
        body = buffer[:-2]
        crc_bytes = buffer[-2:]
        crc_expected = int.from_bytes(crc_bytes, byteorder='little')
        return crc_expected == crc16_modbus(body)


class SerialBackend(BackendBase):
    """Serial Modbus backend that converts ModbusRequest objects into RTU frames."""

    def __init__(
        self,
        config: SerialBackendConfig,
        *,
        session_factory: SerialSessionFactory | None = None,
    ) -> None:
        self.config = config
        self._session_factory = session_factory or default_session_factory
        self._session: SerialSession | None = None
        self._lock = asyncio.Lock()
        self.name = config.id

    async def submit(self, routed_request: RoutedRequest) -> RoutedResponse:  # type: ignore[override]
        frame = routed_request.pdu.to_adu()
        payload = frame + crc16_modbus(frame).to_bytes(2, byteorder='little')
        retry = self.config.retry
        backoff = retry.backoff_min
        last_exc: Exception | None = None
        for attempt in range(retry.max_attempts):
            try:
                session = await self._ensure_session()
                response_frame = await session.exchange(payload)
                response, exception = self._decode_response(response_frame)
                context = ResponseContext(
                    frontend=routed_request.context.frontend,
                    backend=self.name,
                    request_id=routed_request.context.request_id,
                )
                return RoutedResponse(
                    context=context,
                    request=routed_request.pdu,
                    response=response,
                    exception=exception,
                )
            except Exception as exc:  # pragma: no cover - exercised via tests
                last_exc = exc
                await self._reset_session()
                if attempt == retry.max_attempts - 1:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, retry.backoff_max)
        raise RuntimeError('Serial backend exhausted retries') from last_exc

    def _decode_response(self, frame: bytes) -> tuple[ModbusResponse | None, ModbusExceptionResponse | None]:
        if len(frame) < 4:
            raise ValueError('Response frame too short')
        body = frame[:-2]
        crc_expected = int.from_bytes(frame[-2:], byteorder='little')
        if crc_expected != crc16_modbus(body):
            raise ValueError('CRC mismatch on response')
        response = ModbusResponse.from_adu(body)
        if isinstance(response, ModbusExceptionResponse):
            return None, response
        return response, None

    async def _ensure_session(self) -> SerialSession:
        if self._session is not None:
            return self._session
        async with self._lock:
            if self._session is None:
                self._session = await self._session_factory(self.config)
        return self._session

    async def _reset_session(self) -> None:
        async with self._lock:
            if self._session is not None:
                with contextlib.suppress(Exception):
                    await self._session.close()
            self._session = None


_PARITY_MAP = {
    'N': serial.PARITY_NONE,
    'E': serial.PARITY_EVEN,
    'O': serial.PARITY_ODD,
}


async def default_session_factory(config: SerialBackendConfig) -> SerialSession:
    reader, writer = await serial_asyncio.open_serial_connection(
        url=str(config.device),
        baudrate=config.baudrate,
        parity=_PARITY_MAP[config.parity],
        stopbits=config.stop_bits,
        bytesize=8,
    )
    timeout = config.request_timeout_ms / 1000.0
    return SerialAsyncSession(reader, writer, timeout=timeout)
