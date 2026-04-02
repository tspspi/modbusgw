"""Serial backend tests."""
from __future__ import annotations

import pytest

from modbusgw.backends.serial import SerialBackend, SerialSession
from modbusgw.config.models import SerialBackendConfig, RetryConfig
from modbusgw.core.messages import (
    ReadHoldingRegistersRequest,
    ReadHoldingRegistersResponse,
    RequestContext,
    RoutedRequest,
)
from modbusgw.utils.crc import crc16_modbus


def build_frame(unit_id: int, function_code: int, payload: bytes) -> bytes:
    adu = bytes([unit_id, function_code]) + payload
    crc = crc16_modbus(adu)
    return adu + crc.to_bytes(2, byteorder='little')


class DummySession(SerialSession):
    def __init__(self, responses: list[bytes], fail_first: bool = False) -> None:
        self.frames = []
        self._responses = responses
        self._fail_first = fail_first

    async def exchange(self, frame: bytes) -> bytes:
        self.frames.append(frame)
        if self._fail_first:
            self._fail_first = False
            raise IOError('boom')
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_serial_backend_invokes_session() -> None:
    config = SerialBackendConfig(id='serial', device='/dev/null', baudrate=9600, retry=RetryConfig())

    async def factory(_: SerialBackendConfig) -> DummySession:
        response = build_frame(1, 3, b"\x02\x00\x01")
        return DummySession([response])

    backend = SerialBackend(config, session_factory=factory)
    request = RoutedRequest(RequestContext(frontend='uds', request_id='abc'), ReadHoldingRegistersRequest(unit_id=1, address=0x10, quantity=1))
    result = await backend.submit(request)
    assert isinstance(result.response, ReadHoldingRegistersResponse)
    assert result.response.values == (1,)


@pytest.mark.asyncio
async def test_serial_backend_reconnects_after_failure() -> None:
    config = SerialBackendConfig(id='serial', device='/dev/null', baudrate=9600, retry=RetryConfig(max_attempts=2))
    sessions = [
        DummySession([build_frame(1, 3, b"\x02\x00\x02")], fail_first=True),
        DummySession([build_frame(1, 3, b"\x02\x00\x02")]),
    ]

    async def factory(_: SerialBackendConfig) -> DummySession:
        return sessions.pop(0)

    backend = SerialBackend(config, session_factory=factory)
    request = RoutedRequest(RequestContext(frontend='uds', request_id='abc'), ReadHoldingRegistersRequest(unit_id=1, address=0x10, quantity=1))
    result = await backend.submit(request)
    assert result.response.values == (2,)
