"""Message broker base class."""
from __future__ import annotations

from abc import ABC, abstractmethod


class MessageBrokerBase(ABC):
    name: str

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError
