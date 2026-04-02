"""Serial RTU frontend tests."""
from __future__ import annotations

import asyncio
import os

import pytest

from modbusgw.core.bus import GatewayBus
from modbusgw.frontends.serial_rtu import SerialRTUFrontend
from modbusgw.config.models import SerialRtuSocketConfig
from modbusgw.utils.crc import crc16_modbus


def build_frame(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + crc.to_bytes(2, byteorder='little')


@pytest.mark.asyncio
async def test_serial_rtu_frontend_emits_bus_events(tmp_path) -> None:
    socket_path = tmp_path / 'tty'
    config = SerialRtuSocketConfig(
        id='uds',
        type='serial_rtu_socket',
        socket_path=str(socket_path),
        pty_mode='rw',
        idle_close_seconds=600,
        frame_timeout_ms=1.0,
    )
    bus = GatewayBus()
    frontend = SerialRTUFrontend(config, bus)
    await frontend.start()
    try:
        frame = build_frame(bytes.fromhex('010300100001'))
        fd = os.open(frontend.slave_path or config.socket_path, os.O_RDWR | os.O_NOCTTY)
        os.write(fd, frame)
        os.close(fd)
        message = await asyncio.wait_for(bus.get('requests'), timeout=1.0)
        assert message.frontend == 'uds'
        assert message.unit_id == 1
        assert message.function_code == 3
        assert message.pdu.raw_adu == frame[:-2]
    finally:
        await frontend.stop()
