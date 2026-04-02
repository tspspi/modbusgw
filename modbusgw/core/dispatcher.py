"""Dispatcher loop that routes requests to backends."""
from __future__ import annotations

import asyncio
import contextlib

from ..backends.base import BackendBase
from .bus import GatewayBus
from .router import Router
from .messages import RoutedRequest, RoutedResponse, ResponseContext


class Dispatcher:
    """Consume requests from the bus, resolve routing, and call backends."""

    def __init__(self, bus: GatewayBus, router: Router, backends: dict[str, BackendBase]) -> None:
        self._bus = bus
        self._router = router
        self._backends = backends
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while self._running:
            await self.run_once()

    async def run_once(self) -> None:
        message = await self._bus.get('requests')
        if not isinstance(message, RoutedRequest):
            raise TypeError('Dispatcher expects RoutedRequest objects on the bus')
        await self._handle_request(message)

    async def _handle_request(self, request: RoutedRequest) -> None:
        plan = self._router.resolve(request)
        if plan is None:
            await self._bus.publish(
                'responses',
                RoutedResponse(
                    context=ResponseContext(
                        frontend=request.frontend,
                        backend='dispatcher',
                        request_id=request.context.request_id,
                    ),
                    request=request.pdu,
                    error='no_route',
                ),
            )
            return
        backend = self._backends.get(plan.backend)
        if backend is None:
            await self._bus.publish(
                'responses',
                RoutedResponse(
                    context=ResponseContext(
                        frontend=request.frontend,
                        backend=plan.backend,
                        request_id=request.context.request_id,
                    ),
                    request=request.pdu,
                    error='backend_not_found',
                ),
            )
            return
        routed = request if plan.unit_id == request.unit_id else request.with_unit(plan.unit_id)
        try:
            response = await backend.submit(routed)
        except Exception as exc:  # noqa: BLE001 - backend errors become bus responses
            await self._bus.publish(
                'responses',
                RoutedResponse(
                    context=ResponseContext(
                        frontend=request.frontend,
                        backend=plan.backend,
                        request_id=request.context.request_id,
                    ),
                    request=request.pdu,
                    error=f'backend_error:{exc}',
                ),
            )
            return
        await self._bus.publish('responses', response)
