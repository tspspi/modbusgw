"""Client API behaviour tests."""
from __future__ import annotations

import pytest

from modbusgw_client.api import ModbusClient
from modbusgw_client.base import BaseClient
from modbusgw_client.exceptions import ModbusServerError
from modbusgw_client.pdu import (
    ModbusExceptionResponse,
    ModbusRequest,
    ModbusResponse,
    ReadCoilsRequest,
    ReadHoldingRegistersRequest,
    ReadHoldingRegistersResponse,
    WriteMultipleRegistersRequest,
    WriteMultipleRegistersResponse,
    WriteSingleCoilResponse,
)


class StubTransport(BaseClient):
    """Simple in-memory transport used for high-level API tests."""

    def __init__(self, responses: list[ModbusResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ModbusRequest] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def execute(self, request: ModbusRequest) -> ModbusResponse:  # type: ignore[override]
        if not self.connected:
            raise RuntimeError('Transport not connected')
        self.requests.append(request)
        return self.responses.pop(0)


def test_read_holding_registers_roundtrip() -> None:
    response = ReadHoldingRegistersResponse(unit_id=1, values=(10, 11))
    stub = StubTransport([response])
    client = ModbusClient(stub)
    client.connect()
    values = client.read_holding_registers(16, 2)
    assert values == (10, 11)
    assert isinstance(stub.requests[0], ReadHoldingRegistersRequest)


def test_write_coil_returns_echo() -> None:
    response = WriteSingleCoilResponse(unit_id=1, address=0, value=0xFF00)
    stub = StubTransport([response])
    client = ModbusClient(stub)
    client.connect()
    result = client.write_coil(0, True)
    assert result is True


def test_modbus_exception_raises() -> None:
    response = ModbusExceptionResponse(unit_id=1, base_function_code=3, exception_code=2)
    stub = StubTransport([response])
    client = ModbusClient(stub)
    client.connect()
    with pytest.raises(ModbusServerError):
        client.read_holding_registers(0, 1)


def test_request_roundtrip_decode() -> None:
    request = ReadCoilsRequest(unit_id=7, address=0, quantity=8)
    decoded = ReadCoilsRequest.from_adu(request.to_adu())
    assert isinstance(decoded, ReadCoilsRequest)
    assert decoded.address == request.address
    assert decoded.quantity == request.quantity


def test_read_holding_registers_fmt_unpack() -> None:
    response = ReadHoldingRegistersResponse(unit_id=1, values=(0x3F80, 0x0000))
    stub = StubTransport([response])
    client = ModbusClient(stub)
    client.connect()
    decoded = client.read_holding_registers(16, 2, fmt='>f')
    assert decoded[0] == pytest.approx(1.0)


def test_write_register_fmt_promotes_to_multiple() -> None:
    response = WriteMultipleRegistersResponse(unit_id=1, address=0, quantity=2)
    stub = StubTransport([response])
    client = ModbusClient(stub)
    client.connect()
    quantity = client.write_register(0, 1.0, fmt='>f')
    assert quantity == 2
    sent = stub.requests[0]
    assert isinstance(sent, WriteMultipleRegistersRequest)
    assert sent.values == (0x3F80, 0x0000)

