"""Frontend base class."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.messages import RoutedResponse


class FrontendBase(ABC):
    name: str

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def handle_response(self, message: RoutedResponse) -> None:
        raise NotImplementedError
