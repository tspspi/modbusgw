"""Async message bus with topic queues and tracing hooks."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

Tracer = Callable[[str, Any], Awaitable[None]] | Callable[[str, Any], None]


class GatewayBus:
    """Lightweight pub/sub fabric for gateway components."""

    def __init__(self, *, queue_size: int = 1024) -> None:
        self._queue_size = queue_size
        self._topics: Dict[str, asyncio.Queue[Any]] = {}
        self._tracers: list[Tracer] = []
        # Pre-create primary topics
        for name in ("requests", "responses", "events", "management"):
            self._topics[name] = asyncio.Queue(maxsize=queue_size)

    def register_tracer(self, tracer: Tracer) -> None:
        """Attach a tracer that will be notified on every publish."""
        self._tracers.append(tracer)

    def queue(self, name: str) -> asyncio.Queue[Any]:
        """Return the queue for a topic, creating it if needed."""
        if name not in self._topics:
            self._topics[name] = asyncio.Queue(maxsize=self._queue_size)
        return self._topics[name]

    async def publish(self, topic: str, message: Any) -> None:
        queue = self.queue(topic)
        await queue.put(message)
        await self._trace(topic, message)

    async def _trace(self, topic: str, message: Any) -> None:
        if not self._tracers:
            return
        for tracer in self._tracers:
            result = tracer(topic, message)
            if asyncio.iscoroutine(result):
                await result

    async def get(self, topic: str) -> Any:
        """Convenience wrapper for awaiting one item from a topic."""
        return await self.queue(topic).get()
