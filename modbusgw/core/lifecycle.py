"""Lifecycle controller scaffolding."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class Component:
    name: str

    async def start(self) -> None:  # pragma: no cover - placeholder
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover - placeholder
        raise NotImplementedError


class LifecycleController:
    """Owns signal handling and orchestrates all components."""

    def __init__(self) -> None:
        self._components: List[Component] = []

    def register(self, component: Component) -> None:
        self._components.append(component)

    async def start(self) -> None:  # pragma: no cover - placeholder
        for component in self._components:
            await component.start()

    async def stop(self) -> None:  # pragma: no cover - placeholder
        for component in reversed(self._components):
            await component.stop()
