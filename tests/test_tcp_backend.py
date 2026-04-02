"""TCP backend tests."""
from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess
from pathlib import Path

import pytest

from modbusgw.backends.base import BackendBase
from modbusgw.backends.tcp import TcpModbusBackend
from modbusgw.config.models import TcpBackendConfig, TcpModbusFrontendConfig, TlsConfig
from modbusgw.core.bus import GatewayBus
from modbusgw.core.dispatcher import Dispatcher
from modbusgw.core.messages import (
    ReadCoilsRequest,
    ReadCoilsResponse,
    RequestContext,
    ResponseContext,
    RoutedRequest,
    RoutedResponse,
)
from modbusgw.core.responder import ResponseRouter
from modbusgw.core.router import Router, RoutingRule
from modbusgw.frontends.tcp_modbus import TcpModbusFrontend

CERT_DIR = Path('examples/certs')
ROOT_CA = CERT_DIR / 'rootCA.crt'
SERVER_CERT = CERT_DIR / 'server.crt'
SERVER_KEY = CERT_DIR / 'server.key'
CLIENT_CERT = CERT_DIR / 'client.crt'
CLIENT_KEY = CERT_DIR / 'client.key'
CLIENT_CERT_ALT = CERT_DIR / 'client_alt.crt'
CLIENT_KEY_ALT = CERT_DIR / 'client_alt.key'

CLIENT_DN = 'CN=ModbusGW Test Client'
CLIENT_DN_ALT = 'CN=ModbusGW Alt Client'


def ensure_test_certs() -> None:
    missing = any(not p.exists() for p in [ROOT_CA, SERVER_CERT, CLIENT_CERT, CLIENT_CERT_ALT])
    if missing:
        subprocess.run(['/bin/sh', 'examples/gencerts.sh'], check=True)


async def modbus_echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, coil_value: int = 1) -> None:
    try:
        header = await reader.readexactly(6)
    except asyncio.IncompleteReadError:
        writer.close()
        await writer.wait_closed()
        return
    length = int.from_bytes(header[4:6], 'big')
    if length < 1:
        writer.close()
        await writer.wait_closed()
        return
    unit_byte = await reader.readexactly(1)
    body = await reader.readexactly(length - 1)
    unit_id = unit_byte[0]
    function_code = body[0]
    response_payload = bytes([function_code, 1, coil_value & 0xFF])
    response_length = len(response_payload) + 1
    response = header[0:2] + header[2:4] + response_length.to_bytes(2, 'big') + bytes([unit_id]) + response_payload
    writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class DummyDeviceBackend(BackendBase):
    def __init__(self, values: bytes) -> None:
        self.name = 'dummy_device'
        self._values = values

    async def submit(self, request: RoutedRequest) -> RoutedResponse:  # type: ignore[override]
        response = ReadCoilsResponse(unit_id=request.unit_id, values=self._values)
        context = ResponseContext(
            frontend=request.context.frontend,
            backend=self.name,
            request_id=request.context.request_id,
        )
        return RoutedResponse(
            context=context,
            request=request.pdu,
            response=response,
        )


async def start_test_gateway(
    frontend_cfg: TcpModbusFrontendConfig,
    response_values: bytes = b'\x01',
):
    bus = GatewayBus()
    router = Router()
    router.add_rule(
        RoutingRule(
            frontend=frontend_cfg.id,
            backend='dummy_device',
            match={'unit_ids': ['*'], 'function_codes': ['*']},
        )
    )
    backend = DummyDeviceBackend(response_values)
    dispatcher = Dispatcher(bus, router, {backend.name: backend})
    await dispatcher.start()
    frontend = TcpModbusFrontend(frontend_cfg, bus)
    await frontend.start()
    responder = ResponseRouter(bus, {frontend.name: frontend})
    await responder.start()
    return dispatcher, responder, frontend


async def stop_test_gateway(
    dispatcher: Dispatcher, responder: ResponseRouter, frontend: TcpModbusFrontend
) -> None:
    await responder.stop()
    await frontend.stop()
    await dispatcher.stop()


async def exercise_backend_to_frontend(
    frontend_cfg: TcpModbusFrontendConfig,
    backend_tls: TlsConfig | None,
    expect_success: bool = True,
) -> None:
    dispatcher, responder, frontend = await start_test_gateway(frontend_cfg)
    try:
        server = frontend._server  # type: ignore[attr-defined]
        assert server is not None
        host, port = server.sockets[0].getsockname()[:2]
        backend_cfg = TcpBackendConfig(
            id='tcp_backend_client',
            host=host,
            port=port,
            use_tls=backend_tls is not None,
            tls=backend_tls,
            connect_timeout=2.0,
            pool_size=1,
        )
        backend = TcpModbusBackend(backend_cfg)
        try:
            if expect_success:
                response = await backend.submit(build_request())
                assert response.response is not None
                assert response.response.values == b''
            else:
                with pytest.raises(RuntimeError):
                    await backend.submit(build_request())
        finally:
            await backend.close()
    finally:
        await stop_test_gateway(dispatcher, responder, frontend)


def build_request() -> RoutedRequest:
    request = ReadCoilsRequest(unit_id=1, address=0, quantity=1)
    context = RequestContext(frontend='test_frontend', request_id='req-1')
    return RoutedRequest(context=context, pdu=request)


@pytest.mark.asyncio
async def test_tcp_backend_round_trip() -> None:
    server = await asyncio.start_server(lambda r, w: modbus_echo(r, w), '127.0.0.1', 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        backend = TcpModbusBackend(
            TcpBackendConfig(id='tcp', host='127.0.0.1', port=port, use_tls=False, pool_size=1, connect_timeout=1.0)
        )
        try:
            response = await backend.submit(build_request())
            assert response.response is not None
            assert response.response.values == b'\x01'
        finally:
            await backend.close()


@pytest.mark.asyncio
async def test_tcp_backend_tls_with_client_certificate() -> None:
    ensure_test_certs()

    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_ctx.load_cert_chain(certfile=str(SERVER_CERT), keyfile=str(SERVER_KEY))
    ssl_ctx.load_verify_locations(str(ROOT_CA))
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED

    server = await asyncio.start_server(lambda r, w: modbus_echo(r, w), '127.0.0.1', 0, ssl=ssl_ctx)
    async with server:
        port = server.sockets[0].getsockname()[1]
        config = TcpBackendConfig(
            id='tcp_tls',
            host='127.0.0.1',
            port=port,
            use_tls=True,
            tls=TlsConfig(ca_file=ROOT_CA, cert_file=CLIENT_CERT, key_file=CLIENT_KEY),
            connect_timeout=2.0,
            pool_size=1,
        )
        backend = TcpModbusBackend(config)
        try:
            response = await backend.submit(build_request())
            assert response.response is not None
            assert response.response.values == b'\x01'
        finally:
            await backend.close()


@pytest.mark.asyncio
async def test_tcp_backend_tls_without_verification() -> None:
    ensure_test_certs()

    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_ctx.load_cert_chain(certfile=str(SERVER_CERT), keyfile=str(SERVER_KEY))
    server = await asyncio.start_server(lambda r, w: modbus_echo(r, w, coil_value=0), '127.0.0.1', 0, ssl=ssl_ctx)
    async with server:
        port = server.sockets[0].getsockname()[1]
        config = TcpBackendConfig(
            id='tcp_tls_insecure',
            host='127.0.0.1',
            port=port,
            use_tls=True,
            tls=TlsConfig(verify_server_cert=False),
            connect_timeout=2.0,
            pool_size=1,
        )
        backend = TcpModbusBackend(config)
        try:
            response = await backend.submit(build_request())
            assert response.response is not None
            assert response.response.values == b'\x00'
        finally:
            await backend.close()


@pytest.mark.asyncio
async def test_tcp_backend_connects_to_tcp_frontend_plain() -> None:
    front_cfg = TcpModbusFrontendConfig(
        id='front_plain',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
    )
    await exercise_backend_to_frontend(front_cfg, backend_tls=None)


@pytest.mark.asyncio
async def test_tcp_backend_connects_to_tcp_frontend_tls() -> None:
    ensure_test_certs()
    front_cfg = TcpModbusFrontendConfig(
        id='front_tls',
        type='tcp_modbus_tcp',
        host='127.0.0.1',
        port=get_free_port(),
        cidr_allow=['127.0.0.0/8'],
        tls=TlsConfig(cert_file=SERVER_CERT, key_file=SERVER_KEY),
    )
    backend_tls = TlsConfig(ca_file=ROOT_CA)
    await exercise_backend_to_frontend(front_cfg, backend_tls=backend_tls)


@pytest.mark.asyncio
async def test_tcp_backend_connects_to_tcp_frontend_mtls_valid() -> None:
    ensure_test_certs()
    front_cfg = TcpModbusFrontendConfig(
        id='front_mtls',
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
    backend_tls = TlsConfig(ca_file=ROOT_CA, cert_file=CLIENT_CERT, key_file=CLIENT_KEY)
    await exercise_backend_to_frontend(front_cfg, backend_tls=backend_tls)


@pytest.mark.asyncio
async def test_tcp_backend_connects_to_tcp_frontend_mtls_rejects_invalid_client() -> None:
    ensure_test_certs()
    front_cfg = TcpModbusFrontendConfig(
        id='front_mtls_deny',
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
    backend_tls = TlsConfig(ca_file=ROOT_CA, cert_file=CLIENT_CERT_ALT, key_file=CLIENT_KEY_ALT)
    await exercise_backend_to_frontend(front_cfg, backend_tls=backend_tls, expect_success=False)
