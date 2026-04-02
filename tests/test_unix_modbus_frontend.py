"""Unix Modbus/TCP frontend tests."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from modbusgw.core.bus import GatewayBus
from modbusgw.core.messages import ReadCoilsResponse, ResponseContext, RoutedResponse
from modbusgw.frontends.unix_modbus_tcp import UnixModbusTCPFrontend
from modbusgw.config.models import UnixModbusTcpConfig


def build_mbap(transaction_id: int, protocol_id: int, payload: bytes) -> bytes:
    length = len(payload)
    return (
        transaction_id.to_bytes(2, 'big')
        + protocol_id.to_bytes(2, 'big')
        + length.to_bytes(2, 'big')
        + payload
    )


@pytest.mark.asyncio
async def test_unix_modbus_frontend_forwards_requests(tmp_path: Path) -> None:
    socket_path = tmp_path / 'modbus.sock'
    config = UnixModbusTcpConfig(
        id='unix',
        type='unix_modbus_tcp',
        socket_path=str(socket_path),
        max_clients=4,
    )
    bus = GatewayBus()
    frontend = UnixModbusTCPFrontend(config, bus)
    await frontend.start()
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        payload = bytes([1, 1, 0, 0, 0, 1])
        writer.write(build_mbap(0x55AA, 0, payload))
        await writer.drain()
        message = await asyncio.wait_for(bus.get('requests'), timeout=1.0)
        response = ReadCoilsResponse(unit_id=message.pdu.unit_id, values=b'\x01')
        context = ResponseContext(frontend=frontend.name, backend='dummy', request_id=message.context.request_id)
        routed_response = RoutedResponse(context=context, request=message.pdu, response=response)
        await frontend.handle_response(routed_response)
        header = await asyncio.wait_for(reader.readexactly(6), timeout=1.0)
        unit = await asyncio.wait_for(reader.readexactly(1), timeout=1.0)
        body_len = int.from_bytes(header[4:6], 'big') - 1
        body = await asyncio.wait_for(reader.readexactly(body_len), timeout=1.0)
        assert header[0:2] == b'\x55\xAA'
        assert unit == b'\x01'
        assert body[:2] == b'\x01\x01'
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()


@pytest.mark.asyncio
async def test_unix_modbus_frontend_enforces_max_clients(tmp_path: Path) -> None:
    socket_path = tmp_path / 'modbus.sock'
    config = UnixModbusTcpConfig(
        id='unix',
        type='unix_modbus_tcp',
        socket_path=str(socket_path),
        max_clients=1,
    )
    bus = GatewayBus()
    frontend = UnixModbusTCPFrontend(config, bus)
    await frontend.start()
    reader1, writer1 = await asyncio.open_unix_connection(str(socket_path))
    try:
        reader2, writer2 = await asyncio.open_unix_connection(str(socket_path))
        try:
            data = await asyncio.wait_for(reader2.read(), timeout=1.0)
            assert data == b''
        finally:
            writer2.close()
            await writer2.wait_closed()
    finally:
        writer1.close()
        await writer1.wait_closed()
        await frontend.stop()
