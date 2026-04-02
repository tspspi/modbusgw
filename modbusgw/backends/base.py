"""Backend base class."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.messages import RoutedRequest, RoutedResponse


class BackendBase(ABC):
    name: str

    @abstractmethod
    async def submit(self, request: RoutedRequest) -> RoutedResponse:
        raise NotImplementedError
