"""Client-side exception hierarchy."""
from __future__ import annotations


class ModbusClientError(Exception):
    """Base class for all client-side errors."""


class TransportError(ModbusClientError):
    """Raised when the underlying transport fails or disconnects."""


class ProtocolError(ModbusClientError):
    """Raised when a frame cannot be decoded or violates expectations."""


class ModbusServerError(ModbusClientError):
    """Raised when the server responds with a Modbus exception frame."""

    def __init__(self, function_code: int, exception_code: int) -> None:
        self.function_code = function_code
        self.exception_code = exception_code
        super().__init__(
            f'Modbus exception (fc={function_code}, code={exception_code})'
        )


class ConnectionClosed(TransportError):
    """Raised when a request is attempted without an open connection."""
