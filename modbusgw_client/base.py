"""Client base class with context manager support."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from .pdu import ModbusPDU


class BaseClient(ABC):
    def __enter__(self) -> 'BaseClient':
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    async def __aenter__(self) -> 'BaseClient':
        await self.connect_async()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close_async()

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    async def connect_async(self) -> None:
        self.connect()

    async def close_async(self) -> None:
        self.close()

    @abstractmethod
    def execute(self, request: ModbusPDU) -> ModbusPDU:
        raise NotImplementedError

    def bulk_execute(self, requests: Iterable[ModbusPDU]) -> list[ModbusPDU]:
        return [self.execute(req) for req in requests]
