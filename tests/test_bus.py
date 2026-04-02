"""GatewayBus tests."""
from __future__ import annotations

import asyncio

import pytest

from modbusgw.core.bus import GatewayBus


@pytest.mark.asyncio
async def test_publish_and_get_round_trip() -> None:
    bus = GatewayBus()
    payload = {'request_id': 'abc'}
    await bus.publish('requests', payload)
    received = await asyncio.wait_for(bus.get('requests'), timeout=0.1)
    assert received is payload


@pytest.mark.asyncio
async def test_tracer_invoked() -> None:
    bus = GatewayBus()
    seen: list[tuple[str, dict[str, str]]] = []

    async def tracer(topic: str, message: dict[str, str]) -> None:
        seen.append((topic, message))

    bus.register_tracer(tracer)
    payload = {'event': 'something'}
    await bus.publish('events', payload)
    assert seen == [('events', payload)]
