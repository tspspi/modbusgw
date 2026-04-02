"""Bootstrap wiring tests."""
from __future__ import annotations

import json

from modbusgw import app
from modbusgw.config.loader import load_config
from modbusgw.core.bus import GatewayBus


def test_build_router_from_config(tmp_path) -> None:
    config_path = tmp_path / 'config.json'
    config = {
        'service': {},
        'bus': {'request_queue_size': 16, 'response_timeout_ms': 2000},
        'routes': [
            {
                'frontend': 'uds',
                'backend': 'serial',
                'match': {'unit_ids': [1], 'function_codes': [3]},
                'mirror_to_mqtt': [],
            }
        ],
    }
    config_path.write_text(json.dumps(config), encoding='utf-8')

    cfg = load_config(str(config_path))
    bus = GatewayBus(queue_size=cfg.bus.request_queue_size)
    assert bus.queue('requests').maxsize == 16

    router = app.build_router_from_config(cfg)
    plan = router.resolve({'frontend': 'uds', 'unit_id': 1, 'function_code': 3})
    assert plan is not None
    assert plan.backend == 'serial'
