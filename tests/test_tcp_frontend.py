"""Unit tests for the Modbus/TCP frontend."""
from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess
from pathlib import Path

import pytest

from modbusgw.core.bus import GatewayBus
from modbusgw.core.messages import ReadCoilsResponse, ResponseContext, RoutedResponse
from modbusgw.frontends.tcp_modbus import TcpModbusFrontend
from modbusgw.config.models import TcpModbusFrontendConfig, TlsConfig

CERT_DIR = Path("examples/certs")
ROOT_CA = CERT_DIR / "rootCA.crt"
SERVER_CERT = CERT_DIR / "server.crt"
SERVER_KEY = CERT_DIR / "server.key"
CLIENT_CERT = CERT_DIR / "client.crt"
CLIENT_KEY = CERT_DIR / "client.key"
CLIENT_CERT_ALT = CERT_DIR / "client_alt.crt"
CLIENT_KEY_ALT = CERT_DIR / "client_alt.key"


CLIENT_DN = "CN=ModbusGW Test Client"
CLIENT_DN_ALT = "CN=ModbusGW Alt Client"


def ensure_test_certs() -> None:
    if (
        not ROOT_CA.exists()
        or not SERVER_CERT.exists()
        or not CLIENT_CERT.exists()
        or not CLIENT_CERT_ALT.exists()
    ):
        subprocess.run(["/bin/sh", "examples/gencerts.sh"], check=True)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def build_mbap(transaction_id: int, protocol_id: int, payload: bytes) -> bytes:
    length = len(payload)
    return (
        transaction_id.to_bytes(2, 'big')
        + protocol_id.to_bytes(2, 'big')
        + length.to_bytes(2, 'big')
        + payload
    )


async def perform_round_trip(bus: GatewayBus, frontend: TcpModbusFrontend, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> tuple[bytes, bytes, bytes]:
    payload = bytes([1, 1, 0, 0, 0, 1])
    writer.write(build_mbap(0x1234, 0, payload))
    await writer.drain()
    message = await asyncio.wait_for(bus.get('requests'), timeout=1.0)
    response = ReadCoilsResponse(unit_id=message.pdu.unit_id, values=b'\x01')
    context = ResponseContext(frontend=frontend.name, backend='dummy', request_id=message.context.request_id)
    routed_response = RoutedResponse(context=context, request=message.pdu, response=response)
    await frontend.handle_response(routed_response)
    mbap = await asyncio.wait_for(reader.readexactly(6), timeout=1.0)
    unit = await asyncio.wait_for(reader.readexactly(1), timeout=1.0)
    body_length = int.from_bytes(mbap[4:6], 'big') - 1
    body = await asyncio.wait_for(reader.readexactly(body_length), timeout=1.0)
    return mbap, unit, body


@pytest.mark.asyncio
async def test_tcp_frontend_forwards_requests() -> None:
    bus = GatewayBus()
    config = TcpModbusFrontendConfig(
        id='tcp',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
    )
    frontend = TcpModbusFrontend(config, bus)
    await frontend.start()
    server = frontend._server  # type: ignore[attr-defined]
    assert server is not None
    host, port = server.sockets[0].getsockname()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        mbap, unit, body = await perform_round_trip(bus, frontend, reader, writer)
        assert mbap[0:2] == b'\x12\x34'
        assert unit == b'\x01'
        assert body[:2] == b'\x01\x01'
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()


@pytest.mark.asyncio
async def test_tcp_frontend_cidr_policy_blocks_clients() -> None:
    bus = GatewayBus()
    config = TcpModbusFrontendConfig(
        id='tcp',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['10.0.0.0/8'],
    )
    frontend = TcpModbusFrontend(config, bus)
    await frontend.start()
    server = frontend._server  # type: ignore[attr-defined]
    assert server is not None
    host, port = server.sockets[0].getsockname()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        data = await asyncio.wait_for(reader.read(), timeout=1.0)
        assert data == b''
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()


@pytest.mark.asyncio
async def test_tcp_frontend_tls_round_trip() -> None:
    ensure_test_certs()
    bus = GatewayBus()
    config = TcpModbusFrontendConfig(
        id='tcp_tls',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
        tls=TlsConfig(cert_file=SERVER_CERT, key_file=SERVER_KEY),
    )
    frontend = TcpModbusFrontend(config, bus)
    await frontend.start()
    server = frontend._server  # type: ignore[attr-defined]
    assert server is not None
    host, port = server.sockets[0].getsockname()
    ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ROOT_CA))
    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx, server_hostname='localhost')
    try:
        mbap, unit, body = await perform_round_trip(bus, frontend, reader, writer)
        assert mbap[0:2] == b'\x12\x34'
        assert unit == b'\x01'
        assert body[:2] == b'\x01\x01'
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()


@pytest.mark.asyncio
async def test_tcp_frontend_mutual_tls_requires_client_cert() -> None:
    ensure_test_certs()
    bus = GatewayBus()
    config = TcpModbusFrontendConfig(
        id='tcp_mtls',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
        tls=TlsConfig(cert_file=SERVER_CERT, key_file=SERVER_KEY, ca_file=ROOT_CA, require_client_cert=True),
    )
    frontend = TcpModbusFrontend(config, bus)
    await frontend.start()
    server = frontend._server  # type: ignore[attr-defined]
    assert server is not None
    host, port = server.sockets[0].getsockname()

    # Connection without client cert should fail (server closes immediately)
    ssl_ctx_no_client = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ROOT_CA))
    try:
        reader_nc, writer_nc = await asyncio.open_connection(host, port, ssl=ssl_ctx_no_client, server_hostname='localhost')
    except Exception:
        pass
    else:
        data = await asyncio.wait_for(reader_nc.read(), timeout=1.0)
        assert data == b''
        writer_nc.close()
        await writer_nc.wait_closed()

    # Connection with client cert succeeds
    client_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ROOT_CA))
    client_ctx.load_cert_chain(certfile=str(CLIENT_CERT), keyfile=str(CLIENT_KEY))
    reader, writer = await asyncio.open_connection(host, port, ssl=client_ctx, server_hostname='localhost')
    try:
        mbap, unit, body = await perform_round_trip(bus, frontend, reader, writer)
        assert mbap[0:2] == b'\x12\x34'
        assert unit == b'\x01'
        assert body[:2] == b'\x01\x01'
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()


@pytest.mark.asyncio
async def test_tcp_frontend_client_dn_allowlist_allows_known_cert() -> None:
    ensure_test_certs()
    bus = GatewayBus()
    config = TcpModbusFrontendConfig(
        id='tcp_dn_allow',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
        tls=TlsConfig(
            cert_file=SERVER_CERT,
            key_file=SERVER_KEY,
            ca_file=ROOT_CA,
            require_client_cert=True,
            client_dn_allow=[CLIENT_DN],
        ),
    )
    frontend = TcpModbusFrontend(config, bus)
    await frontend.start()
    server = frontend._server  # type: ignore[attr-defined]
    assert server is not None
    host, port = server.sockets[0].getsockname()
    client_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ROOT_CA))
    client_ctx.load_cert_chain(certfile=str(CLIENT_CERT), keyfile=str(CLIENT_KEY))
    reader, writer = await asyncio.open_connection(host, port, ssl=client_ctx, server_hostname='localhost')
    try:
        mbap, unit, body = await perform_round_trip(bus, frontend, reader, writer)
        assert mbap[0:2] == b'4'
        assert unit == b''
        assert body[:2] == b''
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()


@pytest.mark.asyncio
async def test_tcp_frontend_client_dn_allowlist_blocks_unknown() -> None:
    ensure_test_certs()
    bus = GatewayBus()
    config = TcpModbusFrontendConfig(
        id='tcp_dn_block',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
        tls=TlsConfig(
            cert_file=SERVER_CERT,
            key_file=SERVER_KEY,
            ca_file=ROOT_CA,
            require_client_cert=True,
            client_dn_allow=['CN=Not Allowed'],
        ),
    )
    frontend = TcpModbusFrontend(config, bus)
    await frontend.start()
    server = frontend._server  # type: ignore[attr-defined]
    assert server is not None
    host, port = server.sockets[0].getsockname()
    client_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ROOT_CA))
    client_ctx.load_cert_chain(certfile=str(CLIENT_CERT_ALT), keyfile=str(CLIENT_KEY_ALT))
    reader, writer = await asyncio.open_connection(host, port, ssl=client_ctx, server_hostname='localhost')
    try:
        data = await asyncio.wait_for(reader.read(), timeout=1.0)
        assert data == b''
    finally:
        writer.close()
        await writer.wait_closed()
        await frontend.stop()
