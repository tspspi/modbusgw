"""Serial RTU client implementation."""
from __future__ import annotations

import time
from typing import Callable

import serial  # type: ignore

from .base import BaseClient
from .codecs import build_rtu_frame, crc16_modbus, strip_rtu_frame
from .exceptions import ConnectionClosed, TransportError
from .pdu import ModbusRequest, ModbusResponse

SerialFactory = Callable[[], serial.Serial]


class SerialClient(BaseClient):
    """Blocking serial RTU client with simple reconnect semantics."""

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 9600,
        parity: str = 'N',
        stop_bits: int = 1,
        timeout: float = 1.0,
        write_timeout: float = 1.0,
        port_factory: SerialFactory | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.parity = parity.upper()
        self.stop_bits = stop_bits
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._factory = port_factory
        self._serial: serial.Serial | None = None

    def connect(self) -> None:
        if self._serial is not None:
            return
        factory = self._factory or self._build_serial
        try:
            self._serial = factory()
        except Exception as exc:  # noqa: BLE001 - serial libs raise varied errors
            raise TransportError(f'Failed to open serial port {self.port}') from exc

    def close(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        finally:
            self._serial = None

    def execute(self, request: ModbusRequest) -> ModbusResponse:  # type: ignore[override]
        if self._serial is None:
            raise ConnectionClosed('Serial client is not connected')
        frame = build_rtu_frame(request.to_adu())
        try:
            if hasattr(self._serial, 'reset_input_buffer'):
                self._serial.reset_input_buffer()
            self._serial.write(frame)
            self._serial.flush()
        except serial.SerialException as exc:  # type: ignore[attr-defined]
            self.close()
            raise TransportError('Failed to write serial frame') from exc
        raw = self._read_frame()
        adu = strip_rtu_frame(raw)
        return ModbusResponse.from_adu(adu)

    def _build_serial(self) -> serial.Serial:
        return serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity=_PARITY_MAP[self.parity],
            stopbits=self.stop_bits,
            timeout=self.timeout,
            write_timeout=self.write_timeout,
        )

    def _read_frame(self) -> bytes:
        assert self._serial is not None
        buffer = bytearray()
        deadline = time.monotonic() + self.timeout
        while True:
            if time.monotonic() >= deadline:
                raise TransportError('Timed out waiting for serial response')
            chunk = self._serial.read(1)
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) >= 4 and self._has_valid_crc(buffer):
                return bytes(buffer)
            if len(buffer) > 256:
                raise TransportError('Serial frame exceeded maximum length')

    @staticmethod
    def _has_valid_crc(buffer: bytearray) -> bool:
        if len(buffer) < 4:
            return False
        body = buffer[:-2]
        crc_bytes = buffer[-2:]
        crc_expected = int.from_bytes(crc_bytes, byteorder='little')
        return crc_expected == crc16_modbus(body)


_PARITY_MAP = {
    'N': serial.PARITY_NONE,
    'E': serial.PARITY_EVEN,
    'O': serial.PARITY_ODD,
}
