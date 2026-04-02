"""Response routing task that delivers backend replies to frontends."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Mapping

from ..frontends.base import FrontendBase
from .bus import GatewayBus
from .messages import RoutedResponse

logger = logging.getLogger(__name__)


class ResponseRouter:
    """Consume responses from the bus and dispatch them to frontends."""

    def __init__(self, bus: GatewayBus, frontends: Mapping[str, FrontendBase]) -> None:
        self._bus = bus
        self._frontends = frontends
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while self._running:
            message = await self._bus.get('responses')
            if not isinstance(message, RoutedResponse):
                continue
            frontend = self._frontends.get(message.frontend)
            if frontend is None:
                logger.debug('No frontend for response to %s', message.frontend)
                continue
            try:
                await frontend.handle_response(message)
            except Exception:  # pragma: no cover - best-effort logging
                logger.exception('Frontend %s failed to handle response', frontend.name)
