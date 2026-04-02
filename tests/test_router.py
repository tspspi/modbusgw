"""Router matching tests."""
from __future__ import annotations

from modbusgw.core.router import Router, RoutingRule


def make_request(**overrides):
    base = {
        'frontend': 'uds_rtu',
        'unit_id': 1,
        'function_code': 3,
        'register': 10,
        'operation': 'read',
    }
    base.update(overrides)
    return base


def test_router_matches_wildcards() -> None:
    router = Router()
    router.add_rule(
        RoutingRule(
            frontend='uds_rtu',
            backend='serial_main',
            match={
                'unit_ids': ['*'],
                'function_codes': [3],
                'register_range': {'start': 0, 'end': 100},
                'operations': ['read'],
            },
            unit_override=10,
            mirror_to_mqtt=['temp_feed'],
        )
    )
    plan = router.resolve(make_request())
    assert plan is not None
    assert plan.backend == 'serial_main'
    assert plan.unit_id == 10
    assert plan.mirror_to_mqtt == ['temp_feed']


def test_router_no_match_returns_none() -> None:
    router = Router()
    router.add_rule(
        RoutingRule(
            frontend='tcp_frontend',
            backend='tcp_backend',
            match={'unit_ids': [5], 'function_codes': [4]},
        )
    )
    assert router.resolve(make_request(frontend='uds_rtu')) is None
