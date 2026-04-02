"""TCP Modbus/TCP frontend."""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import ssl
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .base import FrontendBase
from ..config.models import TcpModbusFrontendConfig
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


class TcpModbusFrontend(FrontendBase):
    """Listen for Modbus/TCP clients and forward requests to the bus."""

    def __init__(self, config: TcpModbusFrontendConfig, bus: GatewayBus) -> None:
        self.config = config
        self.bus = bus
        self.name = config.id
        self._server: asyncio.base_events.Server | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._client_counter = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending: Dict[str, PendingRequest] = {}
        self._allow = [ipaddress.ip_network(c) for c in config.cidr_allow]
        self._dn_allow: set[str] | None = None
        tls = config.tls
        if tls and tls.client_dn_allow:
            if not tls.require_client_cert:
                raise ValueError('client_dn_allow requires require_client_cert=True')
            if tls.ca_file is None:
                raise ValueError('client_dn_allow requires a ca_file to be configured')
            self._dn_allow = {self._normalize_dn(entry) for entry in tls.client_dn_allow}

    async def start(self) -> None:
        if self._server is not None:
            return
        self._ssl_context = self._build_ssl_context()
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.host,
            port=self.config.port,
            ssl=self._ssl_context,
        )
        logger.info('TCP frontend %s listening on %s', self.name, self.sockets)

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

    @property
    def sockets(self) -> list[str]:
        if self._server is None or not self._server.sockets:
            return []
        return [str(sock.getsockname()) for sock in self._server.sockets]

    async def handle_response(self, message: RoutedResponse) -> None:
        request_id = message.context.request_id
        if not request_id:
            return
        pending = self._pending.pop(request_id, None)
        if pending is None:
            logger.debug('Unknown response %s for frontend %s', request_id, self.name)
            return
        adu = self._response_adu(message)
        if adu is None:
            return
        mbap = self._build_mbap(pending.transaction_id, pending.protocol_id, len(adu))
        frame = mbap + adu
        try:
            pending.writer.write(frame)
            await pending.writer.drain()
        except Exception:  # pragma: no cover - best effort
            logger.exception('Failed to write Modbus/TCP response for %s', request_id)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        addr = writer.get_extra_info('peername')
        ip = addr[0] if addr else '0.0.0.0'
        if not self._allow_connection(ip):
            logger.warning('Denying Modbus/TCP client %s due to CIDR policy', ip)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
        self._client_counter += 1
        client_id = f"{self.name}-c{self._client_counter}"
        ssl_obj = writer.get_extra_info('ssl_object')
        client_dn = self._extract_client_dn(ssl_obj)
        if not self._is_client_dn_allowed(client_dn):
            logger.warning('Denying Modbus/TCP client %s due to DN policy (dn=%s)', ip, client_dn)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
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
                remaining = length - 1
                payload = await reader.readexactly(remaining)
                if protocol_id != 0:
                    continue
                adu = bytes([unit_id]) + payload
                try:
                    request = ModbusRequest.from_adu(adu)
                except Exception:
                    logger.exception('Failed to decode Modbus request from %s', client_id)
                    continue
                request_id = f"{client_id}:{transaction_id}"
                context = RequestContext(
                    frontend=self.name,
                    request_id=request_id,
                    metadata={
                        'client_id': client_id,
                        'transaction_id': transaction_id,
                        'protocol_id': protocol_id,
                        'client_ip': ip,
                        'client_dn': client_dn,
                    },
                )
                routed = RoutedRequest(context=context, pdu=request)
                self._pending[request_id] = PendingRequest(
                    request_id=request_id,
                    writer=writer,
                    transaction_id=transaction_id,
                    protocol_id=protocol_id,
                    unit_id=request.unit_id,
                    client_id=client_id,
                )
                await self.bus.publish('requests', routed)
        except asyncio.IncompleteReadError:
            pass
        finally:
            # clean any pending for this client
            to_remove = [req_id for req_id, info in self._pending.items() if info.client_id == client_id]
            for req_id in to_remove:
                self._pending.pop(req_id, None)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if task:
                self._tasks.discard(task)

    def _allow_connection(self, ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if self._allow:
            return any(ip in net for net in self._allow)
        return True

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        if self.config.tls is None:
            return None
        tls = self.config.tls
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        if tls.cert_file and tls.key_file:
            ctx.load_cert_chain(certfile=str(tls.cert_file), keyfile=str(tls.key_file))
        if tls.ca_file:
            ctx.load_verify_locations(str(tls.ca_file))
        ctx.verify_mode = ssl.CERT_REQUIRED if tls.require_client_cert else ssl.CERT_OPTIONAL
        ctx.check_hostname = False
        return ctx

    def _build_mbap(self, transaction_id: int, protocol_id: int, adu_length: int) -> bytes:
        length = adu_length
        return (
            transaction_id.to_bytes(2, 'big')
            + protocol_id.to_bytes(2, 'big')
            + length.to_bytes(2, 'big')
        )

    @staticmethod
    def _response_adu(message: RoutedResponse) -> bytes | None:
        pdu = message.response or message.exception
        if pdu is None:
            return None
        adu = pdu.to_adu()
        return adu

    def _is_client_dn_allowed(self, client_dn: str | None) -> bool:
        if self._dn_allow is None:
            return True
        if client_dn is None:
            return False
        return self._normalize_dn(client_dn) in self._dn_allow

    @staticmethod
    def _normalize_dn(value: str) -> str:
        return value.strip().lower()

    def _extract_client_dn(self, ssl_obj: ssl.SSLObject | None) -> str | None:
        if ssl_obj is None:
            return None
        cert = ssl_obj.getpeercert()
        if not cert:
            return None
        subject = cert.get('subject')
        if not subject:
            return None
        return self._subject_to_string(subject)

    def _subject_to_string(self, subject: Sequence[Tuple[Tuple[str, str], ...]]) -> str:
        components: list[str] = []
        for rdn in subject:
            for attr, value in rdn:
                label = self._DN_ALIASES.get(attr, attr)
                components.append(f"{label}={value}")
        return ','.join(components)

    _DN_ALIASES: Dict[str, str] = {
        'commonName': 'CN',
        'countryName': 'C',
        'localityName': 'L',
        'stateOrProvinceName': 'ST',
        'organizationName': 'O',
        'organizationalUnitName': 'OU',
        'emailAddress': 'emailAddress',
    }
