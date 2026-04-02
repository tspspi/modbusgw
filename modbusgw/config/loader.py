"""JSON configuration loader."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import GatewayConfig

DEFAULT_CONFIG = Path('~/.config/modbusgateway.cfg').expanduser()


def load_config(path: str | None = None) -> GatewayConfig:
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG
    with cfg_path.open('r', encoding='utf-8') as handle:
        data: dict[str, Any] = json.load(handle)
    return GatewayConfig.model_validate(data)
