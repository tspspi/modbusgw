"""Modbus PDU implementations and routing helpers."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Dict, Iterable, Type
import struct


def _attach_raw(pdu: "ModbusPDU", payload: bytes, adu: bytes, mbap: bytes | None = None) -> "ModbusPDU":
    pdu.raw_pdu = payload
    pdu.raw_adu = adu
    pdu.mbap = mbap
    return pdu


@dataclass(slots=True)
class ModbusPDU:
    """Base object that represents a Modbus Protocol Data Unit."""

    unit_id: int

    FUNCTION_CODE: ClassVar[int] = -1

    def __post_init__(self) -> None:
        self.raw_pdu: bytes | None = None
        self.raw_adu: bytes | None = None
        self.mbap: bytes | None = None

    @property
    def function_code(self) -> int:
        return getattr(self, "_function_code_override", self.FUNCTION_CODE)

    @property
    def operation(self) -> str | None:  # pragma: no cover - simple mapping
        return _OPERATION_MAP.get(self.function_code)

    @property
    def register(self) -> int | None:
        return None

    def encode(self) -> bytes:
        raise NotImplementedError

    def to_adu(self) -> bytes:
        payload = self.encode()
        self.raw_pdu = payload
        frame = bytes([self.unit_id, self.function_code]) + payload
        self.raw_adu = frame
        return frame

    def with_unit(self, unit_id: int) -> 'ModbusPDU':
        clone = replace(self, unit_id=unit_id)
        clone.raw_pdu = self.raw_pdu
        clone.raw_adu = self.raw_adu
        clone.mbap = self.mbap
        return clone

    @classmethod
    def _split_adu(cls, adu: bytes) -> tuple[int, int, bytes]:
        if len(adu) < 2:
            raise ValueError("Frame too short")
        return adu[0], adu[1], adu[2:]


class ModbusRequest(ModbusPDU):
    """Base class for concrete Modbus requests."""

    _registry: ClassVar[Dict[int, Type['ModbusRequest']]] = {}

    @classmethod
    def register(cls, impl: Type['ModbusRequest']) -> Type['ModbusRequest']:
        cls._registry[impl.FUNCTION_CODE] = impl
        return impl

    @classmethod
    def from_adu(cls, adu: bytes, *, mbap: bytes | None = None) -> 'ModbusRequest':
        unit_id, function_code, payload = cls._split_adu(adu)
        impl = cls._registry.get(function_code, RawModbusRequest)
        return impl._from_payload(unit_id, payload, adu, mbap, function_code)

    @classmethod
    def _from_payload(
        cls,
        unit_id: int,
        payload: bytes,
        adu: bytes,
        mbap: bytes | None,
        function_code: int,
    ) -> 'ModbusRequest':
        raise NotImplementedError


class ModbusResponse(ModbusPDU):
    """Base class for concrete Modbus responses."""

    _registry: ClassVar[Dict[int, Type['ModbusResponse']]] = {}

    @classmethod
    def register(cls, impl: Type['ModbusResponse']) -> Type['ModbusResponse']:
        cls._registry[impl.FUNCTION_CODE] = impl
        return impl

    @classmethod
    def from_adu(cls, adu: bytes, *, mbap: bytes | None = None) -> 'ModbusResponse':
        unit_id, function_code, payload = cls._split_adu(adu)
        if function_code & 0x80:
            return ModbusExceptionResponse._from_payload(unit_id, payload, adu, mbap, function_code & 0x7F)
        impl = cls._registry.get(function_code, RawModbusResponse)
        return impl._from_payload(unit_id, payload, adu, mbap, function_code)

    @classmethod
    def _from_payload(
        cls,
        unit_id: int,
        payload: bytes,
        adu: bytes,
        mbap: bytes | None,
        function_code: int,
    ) -> 'ModbusResponse':
        raise NotImplementedError


@ModbusRequest.register
@dataclass(slots=True)
class ReadCoilsRequest(ModbusRequest):
    FUNCTION_CODE: ClassVar[int] = 1
    address: int
    quantity: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.quantity)

    @property
    def register(self) -> int:
        return self.address

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid read coils request")
        address, quantity = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, quantity=quantity)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusRequest.register
@dataclass(slots=True)
class ReadDiscreteInputsRequest(ReadCoilsRequest):
    FUNCTION_CODE: ClassVar[int] = 2


@ModbusRequest.register
@dataclass(slots=True)
class ReadHoldingRegistersRequest(ModbusRequest):
    FUNCTION_CODE: ClassVar[int] = 3
    address: int
    quantity: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.quantity)

    @property
    def register(self) -> int:
        return self.address

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid read holding registers request")
        address, quantity = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, quantity=quantity)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusRequest.register
@dataclass(slots=True)
class ReadInputRegistersRequest(ReadHoldingRegistersRequest):
    FUNCTION_CODE: ClassVar[int] = 4


@ModbusRequest.register
@dataclass(slots=True)
class WriteSingleCoilRequest(ModbusRequest):
    FUNCTION_CODE: ClassVar[int] = 5
    address: int
    value: int | bool

    def encode(self) -> bytes:
        val = self._encode_value(self.value)
        return struct.pack(">HH", self.address, val)

    @property
    def register(self) -> int:
        return self.address

    @staticmethod
    def _encode_value(value: int | bool) -> int:
        if isinstance(value, bool):
            return 0xFF00 if value else 0x0000
        return int(value) & 0xFFFF

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid write single coil request")
        address, raw_value = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, value=raw_value)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusRequest.register
@dataclass(slots=True)
class WriteSingleRegisterRequest(ModbusRequest):
    FUNCTION_CODE: ClassVar[int] = 6
    address: int
    value: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.value & 0xFFFF)

    @property
    def register(self) -> int:
        return self.address

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid write single register request")
        address, value = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, value=value)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusRequest.register
@dataclass(slots=True)
class WriteMultipleCoilsRequest(ModbusRequest):
    FUNCTION_CODE: ClassVar[int] = 15
    address: int
    values: tuple[bool, ...]

    def encode(self) -> bytes:
        bytes_payload = _pack_coils(self.values)
        return struct.pack(">HHB", self.address, len(self.values), len(bytes_payload)) + bytes_payload

    @property
    def register(self) -> int:
        return self.address

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 5:
            raise ValueError("Invalid write multiple coils request")
        address, quantity = struct.unpack(">HH", payload[:4])
        byte_count = payload[4]
        data = payload[5:5 + byte_count]
        values = _unpack_coils(data, quantity)
        msg = cls(unit_id=unit_id, address=address, values=tuple(values))
        return _attach_raw(msg, payload[:5 + byte_count], adu, mbap)


@ModbusRequest.register
@dataclass(slots=True)
class WriteMultipleRegistersRequest(ModbusRequest):
    FUNCTION_CODE: ClassVar[int] = 16
    address: int
    values: tuple[int, ...]

    def encode(self) -> bytes:
        byte_count = len(self.values) * 2
        payload = struct.pack(">HHB", self.address, len(self.values), byte_count)
        for value in self.values:
            payload += struct.pack(">H", value & 0xFFFF)
        return payload

    @property
    def register(self) -> int:
        return self.address

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 5:
            raise ValueError("Invalid write multiple registers request")
        address, quantity = struct.unpack(">HH", payload[:4])
        byte_count = payload[4]
        data = payload[5:5 + byte_count]
        if len(data) < quantity * 2:
            raise ValueError("Write multiple registers payload truncated")
        values = tuple(struct.unpack(">" + "H" * quantity, data[: quantity * 2]))
        msg = cls(unit_id=unit_id, address=address, values=values)
        return _attach_raw(msg, payload[:5 + byte_count], adu, mbap)


@ModbusResponse.register
@dataclass(slots=True)
class ReadCoilsResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 1
    values: bytes

    def encode(self) -> bytes:
        return bytes([len(self.values)]) + self.values

    @property
    def bits(self) -> tuple[bool, ...]:
        return tuple(_unpack_coils(self.values, len(self.values) * 8))

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if not payload:
            raise ValueError("Invalid read coils response")
        byte_count = payload[0]
        data = payload[1:1 + byte_count]
        msg = cls(unit_id=unit_id, values=data)
        return _attach_raw(msg, payload[:1 + byte_count], adu, mbap)


@ModbusResponse.register
@dataclass(slots=True)
class ReadDiscreteInputsResponse(ReadCoilsResponse):
    FUNCTION_CODE: ClassVar[int] = 2


@ModbusResponse.register
@dataclass(slots=True)
class ReadHoldingRegistersResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 3
    values: tuple[int, ...]

    def encode(self) -> bytes:
        payload = bytes([len(self.values) * 2])
        for value in self.values:
            payload += struct.pack(">H", value & 0xFFFF)
        return payload

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if not payload:
            raise ValueError("Invalid read holding registers response")
        byte_count = payload[0]
        data = payload[1:1 + byte_count]
        if len(data) % 2:
            raise ValueError("Register response byte count is odd")
        values = tuple(struct.unpack(">" + "H" * (len(data) // 2), data))
        msg = cls(unit_id=unit_id, values=values)
        return _attach_raw(msg, payload[:1 + byte_count], adu, mbap)


@ModbusResponse.register
@dataclass(slots=True)
class ReadInputRegistersResponse(ReadHoldingRegistersResponse):
    FUNCTION_CODE: ClassVar[int] = 4


@ModbusResponse.register
@dataclass(slots=True)
class WriteSingleCoilResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 5
    address: int
    value: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.value & 0xFFFF)

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid write single coil response")
        address, value = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, value=value)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusResponse.register
@dataclass(slots=True)
class WriteSingleRegisterResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 6
    address: int
    value: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.value & 0xFFFF)

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid write single register response")
        address, value = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, value=value)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusResponse.register
@dataclass(slots=True)
class WriteMultipleCoilsResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 15
    address: int
    quantity: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.quantity)

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid write multiple coils response")
        address, quantity = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, quantity=quantity)
        return _attach_raw(msg, payload[:4], adu, mbap)


@ModbusResponse.register
@dataclass(slots=True)
class WriteMultipleRegistersResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 16
    address: int
    quantity: int

    def encode(self) -> bytes:
        return struct.pack(">HH", self.address, self.quantity)

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, _fc):
        if len(payload) < 4:
            raise ValueError("Invalid write multiple registers response")
        address, quantity = struct.unpack(">HH", payload[:4])
        msg = cls(unit_id=unit_id, address=address, quantity=quantity)
        return _attach_raw(msg, payload[:4], adu, mbap)


@dataclass(slots=True)
class ModbusExceptionResponse(ModbusResponse):
    FUNCTION_CODE: ClassVar[int] = 0
    base_function_code: int = 0
    exception_code: int = 0

    def encode(self) -> bytes:
        return bytes([self.exception_code])

    @property
    def function_code(self) -> int:
        return self.base_function_code | 0x80

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, base_fc):
        if not payload:
            raise ValueError("Invalid exception response")
        msg = cls(unit_id=unit_id, base_function_code=base_fc, exception_code=payload[0])
        return _attach_raw(msg, payload[:1], adu, mbap)


@dataclass(slots=True)
class RawModbusRequest(ModbusRequest):
    payload: bytes
    _function_code_override: int

    def encode(self) -> bytes:
        return self.payload

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, fc):
        msg = cls(unit_id=unit_id, payload=payload, _function_code_override=fc)
        return _attach_raw(msg, payload, adu, mbap)


@dataclass(slots=True)
class RawModbusResponse(ModbusResponse):
    payload: bytes
    _function_code_override: int

    def encode(self) -> bytes:
        return self.payload

    @classmethod
    def _from_payload(cls, unit_id, payload, adu, mbap, fc):
        msg = cls(unit_id=unit_id, payload=payload, _function_code_override=fc)
        return _attach_raw(msg, payload, adu, mbap)


def _pack_coils(values: Iterable[bool]) -> bytes:
    bits = list(values)
    byte_count = (len(bits) + 7) // 8
    result = bytearray(byte_count)
    for idx, bit in enumerate(bits):
        if bit:
            result[idx // 8] |= 1 << (idx % 8)
    return bytes(result)


def _unpack_coils(data: bytes, quantity: int) -> list[bool]:
    result: list[bool] = []
    for idx in range(quantity):
        byte_index = idx // 8
        bit_index = idx % 8
        if byte_index >= len(data):
            break
        result.append(bool(data[byte_index] & (1 << bit_index)))
    return result


_OP_READ = {1, 2, 3, 4}
_OP_WRITE = {5, 6, 15, 16}
_OP_OPERATION_LIMITED = {5}

_OPRATION_EXTRA = {}

_OP_OPERATION_MAP = {fc: 'read' for fc in _OP_READ}
_OP_OPERATION_MAP.update({fc: 'write' for fc in _OP_WRITE})

_OPERATION_MAP = _OP_OPERATION_MAP


@dataclass(slots=True)
class RequestContext:
    frontend: str
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoutedRequest:
    context: RequestContext
    pdu: ModbusRequest

    @property
    def frontend(self) -> str:
        return self.context.frontend

    @property
    def unit_id(self) -> int:
        return self.pdu.unit_id

    @property
    def function_code(self) -> int:
        return self.pdu.function_code

    @property
    def operation(self) -> str | None:
        return self.pdu.operation

    @property
    def register(self) -> int | None:
        return self.pdu.register

    def with_unit(self, unit_id: int) -> 'RoutedRequest':
        return RoutedRequest(context=self.context, pdu=self.pdu.with_unit(unit_id))


@dataclass(slots=True)
class ResponseContext:
    frontend: str
    backend: str
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoutedResponse:
    context: ResponseContext
    request: ModbusRequest | None
    response: ModbusResponse | None = None
    exception: ModbusExceptionResponse | None = None
    error: str | None = None

    @property
    def frontend(self) -> str:
        return self.context.frontend

    @property
    def backend(self) -> str:
        return self.context.backend

    @property
    def request_id(self) -> str | None:
        return self.context.request_id
