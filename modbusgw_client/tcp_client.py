
"""Modbus/TCP (and Unix socket) client implementation."""
from __future__ import annotations

import itertools
import socket
import ssl
from dataclasses import dataclass

from .base import BaseClient
from .codecs import build_mbap_frame, parse_mbap_frame
from .exceptions import ConnectionClosed, TransportError
from .pdu import ModbusRequest, ModbusResponse


@dataclass
class TLSConfig:
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None
    verify: bool = True


class TcpClient(BaseClient):
    """Blocking Modbus/TCP client with optional TLS and Unix-socket support."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        unix_socket: str | None = None,
        timeout: float = 2.0,
        tls: TLSConfig | None = None,
    ) -> None:
        if unix_socket is None and (host is None or port is None):
            raise ValueError('host and port are required for TCP connections')
        if unix_socket is not None and tls is not None:
            raise ValueError('TLS is not supported with Unix domain sockets')
        self.host = host
        self.port = port
        self.unix_socket = unix_socket
        self.timeout = timeout
        self.tls = tls
        self._socket: socket.socket | ssl.SSLSocket | None = None
        self._tx_counter = itertools.count(1)

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            sock = self._create_socket()
        except OSError as exc:
            raise TransportError('Failed to establish TCP connection') from exc
        self._socket = sock

    def close(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.close()
        finally:
            self._socket = None

    def execute(self, request: ModbusRequest) -> ModbusResponse:  # type: ignore[override]
        if self._socket is None:
            raise ConnectionClosed('TCP client is not connected')
        tx_id = self._next_transaction_id()
        frame = build_mbap_frame(tx_id, request.to_adu())
        try:
            self._socket.sendall(frame)
            header = self._read_exact(7)
            length = int.from_bytes(header[4:6], 'big')
            payload = self._read_exact(length - 1)
            response_tx, adu = parse_mbap_frame(header, payload)
        except (OSError, ValueError) as exc:
            self.close()
            raise TransportError('Modbus/TCP exchange failed') from exc
        if response_tx != tx_id:
            raise TransportError('Mismatched Modbus transaction id')
        return ModbusResponse.from_adu(adu)

    def _create_socket(self) -> socket.socket | ssl.SSLSocket:
        if self.unix_socket:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect(self.unix_socket)
            return sock
        assert self.host is not None and self.port is not None
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        raw.settimeout(self.timeout)
        if not self.tls:
            return raw
        ctx = self._build_ssl_context(self.tls)
        return ctx.wrap_socket(raw, server_hostname=self.host if self.tls.verify else None)

    @staticmethod
    def _build_ssl_context(config: TLSConfig) -> ssl.SSLContext:
        purpose = ssl.Purpose.SERVER_AUTH
        ctx = ssl.create_default_context(purpose)
        if config.ca_file:
            ctx.load_verify_locations(cafile=config.ca_file)
        if not config.verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if config.cert_file and config.key_file:
            ctx.load_cert_chain(certfile=config.cert_file, keyfile=config.key_file)
        return ctx

    def _read_exact(self, size: int) -> bytes:
        if size <= 0:
            return b''
        assert self._socket is not None
        data = bytearray()
        while len(data) < size:
            chunk = self._socket.recv(size - len(data))
            if not chunk:
                raise TransportError('Connection closed by peer')
            data.extend(chunk)
        return bytes(data)

    def _next_transaction_id(self) -> int:
        value = next(self._tx_counter) & 0xFFFF
        if value == 0:
            value = next(self._tx_counter) & 0xFFFF
        return value
