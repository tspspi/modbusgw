"""Serial backend tests."""
from __future__ import annotations

import pytest

from modbusgw.backends.serial import SerialAsyncSession, SerialBackend, SerialSession
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


@pytest.mark.asyncio
async def test_serial_backend_enforces_min_inter_frame_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    response = build_frame(1, 3, b"\x02\x00\x01")

    class Reader:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload
            self._offset = 0

        async def readexactly(self, count: int) -> bytes:
            chunk = self._payload[self._offset:self._offset + count]
            self._offset += count
            return chunk

    class Writer:
        def __init__(self) -> None:
            self.frames: list[bytes] = []

        def write(self, payload: bytes) -> None:
            self.frames.append(payload)

        async def drain(self) -> None:
            return

        def close(self) -> None:
            return

        async def wait_closed(self) -> None:
            return

    reader = Reader(response + response)
    writer = Writer()
    session = SerialAsyncSession(
        reader,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        timeout=1.0,
        baudrate=9600,
        parity='N',
        stop_bits=1.0,
        inter_frame_gap_ms=None,
    )

    sleeps: list[float] = []
    now = [100.0]

    async def fake_sleep(duration: float) -> None:
        sleeps.append(duration)
        now[0] += duration

    monkeypatch.setattr("modbusgw.backends.serial.time.monotonic", lambda: now[0])
    monkeypatch.setattr("modbusgw.backends.serial.asyncio.sleep", fake_sleep)

    await session.exchange(b"\x01\x03\x00\x10\x00\x01")
    await session.exchange(b"\x01\x03\x00\x10\x00\x01")

    expected_min_gap = 3.5 * (10 / 9600)
    assert sleeps
    assert sleeps[0] >= expected_min_gap - 0.001
