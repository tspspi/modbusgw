"""High-level client facade for the Modbus gateway."""
from __future__ import annotations

import struct
from typing import Any, Iterable, Sequence

from .base import BaseClient
from .exceptions import ModbusServerError, ProtocolError
from .pdu import (
    ModbusExceptionResponse,
    ModbusRequest,
    ModbusResponse,
    ReadCoilsRequest,
    ReadCoilsResponse,
    ReadDiscreteInputsRequest,
    ReadHoldingRegistersRequest,
    ReadHoldingRegistersResponse,
    ReadInputRegistersRequest,
    ReadInputRegistersResponse,
    WriteMultipleCoilsRequest,
    WriteMultipleCoilsResponse,
    WriteMultipleRegistersRequest,
    WriteMultipleRegistersResponse,
    WriteSingleCoilRequest,
    WriteSingleCoilResponse,
    WriteSingleRegisterRequest,
    WriteSingleRegisterResponse,
)
from .serial_client import SerialClient
from .tcp_client import TLSConfig, TcpClient


class ModbusClient:
    """Convenience wrapper exposing pymodbus-style helpers."""

    def __init__(self, transport: BaseClient, *, default_unit_id: int = 1) -> None:
        self._transport = transport
        self.default_unit_id = default_unit_id

    @classmethod
    def serial(
        cls,
        port: str,
        *,
        unit_id: int = 1,
        baudrate: int = 9600,
        parity: str = 'N',
        stop_bits: int = 1,
        timeout: float = 1.0,
    ) -> 'ModbusClient':
        transport = SerialClient(
            port,
            baudrate=baudrate,
            parity=parity,
            stop_bits=stop_bits,
            timeout=timeout,
        )
        return cls(transport, default_unit_id=unit_id)

    @classmethod
    def tcp(
        cls,
        host: str,
        port: int = 502,
        *,
        unit_id: int = 1,
        timeout: float = 2.0,
        tls: TLSConfig | None = None,
    ) -> 'ModbusClient':
        transport = TcpClient(host=host, port=port, timeout=timeout, tls=tls)
        return cls(transport, default_unit_id=unit_id)

    @classmethod
    def unix(
        cls,
        socket_path: str,
        *,
        unit_id: int = 1,
        timeout: float = 2.0,
    ) -> 'ModbusClient':
        transport = TcpClient(unix_socket=socket_path, timeout=timeout)
        return cls(transport, default_unit_id=unit_id)

    def connect(self) -> None:
        self._transport.connect()

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> 'ModbusClient':
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    async def __aenter__(self) -> 'ModbusClient':
        await self._transport.connect_async()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._transport.close_async()

    def read_coils(self, address: int, quantity: int, *, unit_id: int | None = None) -> Sequence[bool]:
        request = ReadCoilsRequest(unit_id=self._unit(unit_id), address=address, quantity=quantity)
        response = self._execute(request, ReadCoilsResponse)
        return response.bits[:quantity]

    def read_discrete_inputs(self, address: int, quantity: int, *, unit_id: int | None = None) -> Sequence[bool]:
        request = ReadDiscreteInputsRequest(unit_id=self._unit(unit_id), address=address, quantity=quantity)
        response = self._execute(request, ReadCoilsResponse)
        return response.bits[:quantity]

    def read_input_registers(
        self,
        address: int,
        quantity: int,
        fmt: str | None = None,
        *,
        unit_id: int | None = None,
    ) -> Sequence[int] | tuple[Any, ...]:
        request = ReadInputRegistersRequest(unit_id=self._unit(unit_id), address=address, quantity=quantity)
        response = self._execute(request, ReadInputRegistersResponse)
        return self._decode_registers(response.values, fmt)

    def read_holding_registers(
        self,
        address: int,
        quantity: int,
        fmt: str | None = None,
        *,
        unit_id: int | None = None,
    ) -> Sequence[int] | tuple[Any, ...]:
        request = ReadHoldingRegistersRequest(unit_id=self._unit(unit_id), address=address, quantity=quantity)
        response = self._execute(request, ReadHoldingRegistersResponse)
        return self._decode_registers(response.values, fmt)

    def write_coil(self, address: int, value: bool | int, *, unit_id: int | None = None) -> bool:
        request = WriteSingleCoilRequest(unit_id=self._unit(unit_id), address=address, value=value)
        response = self._execute(request, WriteSingleCoilResponse)
        return bool(response.value)

    def write_coils(self, address: int, values: Iterable[bool], *, unit_id: int | None = None) -> int:
        packed = tuple(bool(v) for v in values)
        request = WriteMultipleCoilsRequest(unit_id=self._unit(unit_id), address=address, values=packed)
        response = self._execute(request, WriteMultipleCoilsResponse)
        return response.quantity

    def write_register(
        self,
        address: int,
        value: Any,
        *,
        unit_id: int | None = None,
        fmt: str | None = None,
    ) -> int:
        if fmt is not None:
            registers = self._encode_register_payload(fmt, value)
            if len(registers) == 1:
                payload = registers[0]
            else:
                return self.write_registers(address, registers, unit_id=unit_id)
        else:
            payload = int(value) & 0xFFFF
        request = WriteSingleRegisterRequest(unit_id=self._unit(unit_id), address=address, value=payload)
        response = self._execute(request, WriteSingleRegisterResponse)
        return response.value

    def write_registers(
        self,
        address: int,
        values: Iterable[int] | Any,
        *,
        unit_id: int | None = None,
        fmt: str | None = None,
    ) -> int:
        if fmt is not None:
            packed = self._encode_register_payload(fmt, values)
        else:
            packed = tuple(int(v) & 0xFFFF for v in values)
        request = WriteMultipleRegistersRequest(unit_id=self._unit(unit_id), address=address, values=packed)
        response = self._execute(request, WriteMultipleRegistersResponse)
        return response.quantity

    def execute(self, request: ModbusRequest) -> ModbusResponse:
        """Send a raw request using the underlying transport."""
        return self._execute(request, expected_type=None)

    def _execute(self, request: ModbusRequest, expected_type: type[ModbusResponse] | None) -> ModbusResponse:
        response = self._transport.execute(request)
        if isinstance(response, ModbusExceptionResponse):
            raise ModbusServerError(request.function_code, response.exception_code)
        if expected_type is not None and not isinstance(response, expected_type):
            raise ProtocolError(
                f'Unexpected response type {type(response).__name__}, expected {expected_type.__name__}'
            )
        return response

    def _decode_registers(
        self, values: Sequence[int], fmt: str | None
    ) -> Sequence[int] | tuple[Any, ...]:
        if fmt is None:
            return values
        raw = b''.join(struct.pack('>H', v & 0xFFFF) for v in values)
        expected = struct.calcsize(fmt)
        if expected != len(raw):
            raise ValueError(
                f'fmt {fmt!r} expects {expected} bytes, but register payload is {len(raw)} bytes'
            )
        return struct.unpack(fmt, raw)

    def _encode_register_payload(self, fmt: str, value: Any) -> tuple[int, ...]:
        raw = self._pack_struct(fmt, value)
        if len(raw) % 2 != 0:
            raise ValueError('Formatted payload must cover a whole number of registers')
        return tuple(int.from_bytes(raw[i:i + 2], 'big') for i in range(0, len(raw), 2))

    def _pack_struct(self, fmt: str, value: Any) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            raise TypeError('fmt payload cannot be a string')
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            try:
                return struct.pack(fmt, *value)
            except TypeError:
                return struct.pack(fmt, value)
        return struct.pack(fmt, value)

    def _unit(self, override: int | None) -> int:
        return self.default_unit_id if override is None else override


__all__ = ['ModbusClient', 'TLSConfig']
