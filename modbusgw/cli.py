"""Command-line entrypoint for the ModBus gateway."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .app import GatewayApplication
from .config.loader import DEFAULT_CONFIG

LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='ModBus gateway daemon controller')
    parser.add_argument(
        '-c',
        '--config',
        default=str(DEFAULT_CONFIG),
        help='Path to JSON configuration file (default: %(default)s)',
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    config_path = str(Path(args.config).expanduser())
    application = GatewayApplication(config_path)
    try:
        asyncio.run(application.run())
    except KeyboardInterrupt:  # pragma: no cover - CLI convenience
        pass


if __name__ == '__main__':
    main()
