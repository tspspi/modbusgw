"""Dispatcher unit tests."""
from __future__ import annotations

import asyncio

import pytest

from modbusgw.core.bus import GatewayBus
from modbusgw.core.dispatcher import Dispatcher
from modbusgw.core.router import Router, RoutingRule
from modbusgw.backends.base import BackendBase
from modbusgw.core.messages import (
    ReadHoldingRegistersRequest,
    ReadHoldingRegistersResponse,
    RequestContext,
    RoutedRequest,
    RoutedResponse,
    ResponseContext,
)


class DummyBackend(BackendBase):
    def __init__(self) -> None:
        self.requests: list[RoutedRequest] = []
        self.name = 'dummy'

    async def submit(self, request: RoutedRequest) -> RoutedResponse:  # type: ignore[override]
        self.requests.append(request)
        response = ReadHoldingRegistersResponse(unit_id=request.unit_id, values=(0,))
        ctx = ResponseContext(frontend=request.frontend, backend=self.name, request_id=request.context.request_id)
        return RoutedResponse(context=ctx, request=request.pdu, response=response)


@pytest.mark.asyncio
async def test_dispatcher_routes_requests() -> None:
    bus = GatewayBus()
    router = Router()
    router.add_rule(
        RoutingRule(
            frontend='uds',
            backend='serial',
            match={'unit_ids': ['*'], 'function_codes': [3], 'operations': ['read']},
            unit_override=42,
        )
    )
    backend = DummyBackend()
    dispatcher = Dispatcher(bus, router, {'serial': backend})

    req = ReadHoldingRegistersRequest(unit_id=1, address=0x10, quantity=1)
    routed = RoutedRequest(RequestContext(frontend='uds', request_id='abc'), req)
    await bus.publish('requests', routed)
    await dispatcher.run_once()

    assert backend.requests and backend.requests[0].unit_id == 42
    response = await asyncio.wait_for(bus.get('responses'), timeout=0.1)
    assert isinstance(response, RoutedResponse)
    assert isinstance(response.response, ReadHoldingRegistersResponse)


@pytest.mark.asyncio
async def test_dispatcher_handles_missing_backend() -> None:
    bus = GatewayBus()
    router = Router()
    router.add_rule(RoutingRule(frontend='uds', backend='missing', match={'unit_ids': ['*']}))
    dispatcher = Dispatcher(bus, router, {})
    req = ReadHoldingRegistersRequest(unit_id=1, address=0x00, quantity=1)
    routed = RoutedRequest(RequestContext(frontend='uds'), req)
    await bus.publish('requests', routed)
    await dispatcher.run_once()
    response = await asyncio.wait_for(bus.get('responses'), timeout=0.1)
    assert isinstance(response, RoutedResponse)
    assert response.error == 'backend_not_found'
