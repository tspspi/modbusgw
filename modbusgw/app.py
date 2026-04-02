"""Daemon bootstrap helpers (PID/state management)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .backends.base import BackendBase
from .backends.serial import SerialBackend
from .backends.tcp import TcpModbusBackend
from .config.loader import load_config
from .config.models import (
    BackendConfig,
    FrontendConfig,
    GatewayConfig,
    SerialBackendConfig,
    SerialRtuSocketConfig,
    TcpBackendConfig,
    TcpModbusFrontendConfig,
    UnixModbusTcpConfig,
)
from .core.bus import GatewayBus
from .core.dispatcher import Dispatcher
from .core.responder import ResponseRouter
from .core.router import Router, RoutingRule
from .frontends.base import FrontendBase
from .frontends.serial_rtu import SerialRTUFrontend
from .frontends.tcp_modbus import TcpModbusFrontend
from .frontends.unix_modbus_tcp import UnixModbusTCPFrontend

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeBundle:
    config: GatewayConfig
    bus: GatewayBus
    router: Router
    dispatcher: Dispatcher
    responder: ResponseRouter
    frontends: Dict[str, FrontendBase]
    backends: Dict[str, BackendBase]


class GatewayApplication:
    """Coordinate loading configs, running components, and handling signals."""

    def __init__(self, config_path: str | None) -> None:
        self._config_path = config_path
        self._runtime: RuntimeBundle | None = None
        self._stop_event = asyncio.Event()
        self._reload_event = asyncio.Event()
        self._pid_file: Path | None = None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._install_signal_handlers(loop)
        await self._reload_runtime(initial=True)
        while True:
            waiters = [asyncio.create_task(self._stop_event.wait())]
            if self._runtime is not None:
                waiters.append(asyncio.create_task(self._reload_event.wait()))
            done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if self._stop_event.is_set():
                break
            if self._reload_event.is_set():
                self._reload_event.clear()
                await self._reload_runtime(initial=False)
        await self._stop_runtime()
        self._cleanup_pid_file()

    def request_shutdown(self) -> None:
        self._stop_event.set()

    def request_reload(self) -> None:
        self._reload_event.set()

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig, handler in (
            (signal.SIGTERM, self.request_shutdown),
            (signal.SIGINT, self.request_shutdown),
            (signal.SIGHUP, self.request_reload),
        ):
            try:
                loop.add_signal_handler(sig, handler)
            except NotImplementedError:  # pragma: no cover - Windows/non-main threads
                logger.debug('Signal handler for %s not supported on this platform', sig)

    async def _reload_runtime(self, *, initial: bool) -> None:
        try:
            config = load_config(self._config_path)
        except Exception:  # noqa: BLE001 - config errors must be logged
            logger.exception('Failed to load configuration from %s', self._config_path)
            if initial:
                raise
            return
        service = config.service
        service.state_dir = Path(service.state_dir).expanduser()
        service.pid_file = Path(service.pid_file).expanduser()
        service.state_dir.mkdir(parents=True, exist_ok=True)
        await self._stop_runtime()
        try:
            runtime = await self._start_runtime(config)
        except Exception:
            logger.exception('Failed to start runtime with %s', self._config_path)
            if initial:
                raise
            return
        self._runtime = runtime
        self._write_pid_file(runtime.config)
        self._configure_logging(runtime.config)
        logger.info('Gateway runtime started with config %s', self._config_path)

    async def _start_runtime(self, config: GatewayConfig) -> RuntimeBundle:
        bus = GatewayBus(queue_size=config.bus.request_queue_size)
        router = build_router_from_config(config)
        backends = self._build_backends(config.backends)
        dispatcher = Dispatcher(bus, router, backends)
        await dispatcher.start()
        frontend_map: Dict[str, FrontendBase] = {}
        responder = ResponseRouter(bus, frontend_map)
        await responder.start()
        for frontend_cfg in config.frontends:
            frontend = self._build_frontend(frontend_cfg, bus)
            frontend_map[frontend_cfg.id] = frontend
            await frontend.start()
        return RuntimeBundle(
            config=config,
            bus=bus,
            router=router,
            dispatcher=dispatcher,
            responder=responder,
            frontends=frontend_map,
            backends=backends,
        )

    async def _stop_runtime(self) -> None:
        if self._runtime is None:
            return
        runtime = self._runtime
        self._runtime = None
        for frontend in runtime.frontends.values():
            with contextlib.suppress(Exception):
                await frontend.stop()
        with contextlib.suppress(Exception):
            await runtime.responder.stop()
        with contextlib.suppress(Exception):
            await runtime.dispatcher.stop()
        for backend in runtime.backends.values():
            close = getattr(backend, 'close', None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result):
                with contextlib.suppress(Exception):
                    await result
        logger.info('Gateway runtime stopped')

    def _write_pid_file(self, config: GatewayConfig) -> None:
        pid_file = Path(config.service.pid_file)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding='utf-8')
        self._pid_file = pid_file

    def _cleanup_pid_file(self) -> None:
        if self._pid_file and self._pid_file.exists():
            with contextlib.suppress(Exception):
                self._pid_file.unlink()
        self._pid_file = None

    def _configure_logging(self, config: GatewayConfig) -> None:
        level_name = config.service.log_level.upper()
        level = getattr(logging, level_name, logging.INFO)
        logging.getLogger().setLevel(level)

    @staticmethod
    def _build_backends(configs: list[BackendConfig]) -> Dict[str, BackendBase]:
        backends: Dict[str, BackendBase] = {}
        for backend_cfg in configs:
            if isinstance(backend_cfg, SerialBackendConfig):
                backends[backend_cfg.id] = SerialBackend(backend_cfg)
            elif isinstance(backend_cfg, TcpBackendConfig):
                backends[backend_cfg.id] = TcpModbusBackend(backend_cfg)
            else:  # pragma: no cover - defensive guard for future backends
                raise NotImplementedError(f'Unsupported backend type {backend_cfg.type}')
        return backends

    @staticmethod
    def _build_frontend(config: FrontendConfig, bus: GatewayBus) -> FrontendBase:
        if isinstance(config, SerialRtuSocketConfig):
            return SerialRTUFrontend(config, bus)
        if isinstance(config, TcpModbusFrontendConfig):
            return TcpModbusFrontend(config, bus)
        if isinstance(config, UnixModbusTcpConfig):
            return UnixModbusTCPFrontend(config, bus)
        raise NotImplementedError(f'Unsupported frontend type {config.type}')


def build_router_from_config(config: GatewayConfig) -> Router:
    """Construct Router rules from the validated configuration."""
    router = Router()
    for route in config.routes:
        match_dict = {
            "unit_ids": route.match.unit_ids,
            "function_codes": route.match.function_codes,
        }
        if route.match.register_range is not None:
            match_dict["register_range"] = route.match.register_range.model_dump()
        if route.match.operations is not None:
            match_dict["operations"] = route.match.operations
        rule = RoutingRule(
            frontend=route.frontend,
            backend=route.backend,
            match=match_dict,
            unit_override=route.unit_override,
            mirror_to_mqtt=list(route.mirror_to_mqtt),
        )
        router.add_rule(rule)
    return router
