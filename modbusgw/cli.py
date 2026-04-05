"""Command-line entrypoint for the ModBus gateway."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable

from daemonize import Daemonize

from .app import GatewayApplication
from .config.loader import DEFAULT_CONFIG, load_config
from .config.models import GatewayConfig, ServiceConfig

LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
DEFAULT_UMASK = 0o022


class CLIError(RuntimeError):
    """Raised when CLI operations fail."""


def _parse_umask(value: str) -> int:
    raw = value.strip().lower()
    if raw.startswith('0o'):
        raw = raw[2:]
    try:
        mask = int(raw, 8)
    except ValueError as exc:  # pragma: no cover - argparse converts to error
        raise argparse.ArgumentTypeError('umask must be an octal value') from exc
    if mask < 0 or mask > 0o777:
        raise argparse.ArgumentTypeError('umask must be between 000 and 777')
    return mask


def _read_pid_file(pid_file: Path) -> int | None:
    try:
        content = pid_file.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if not content:
        return None
    try:
        return int(content)
    except ValueError:
        return None


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.2)
    return not _pid_is_running(pid)


def _pid_file_from_raw_config(config_path: str) -> Path | None:
    path = Path(config_path).expanduser()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    service = data.get('service')
    if isinstance(service, dict):
        pid_file = service.get('pid_file')
        if isinstance(pid_file, str):
            return Path(pid_file).expanduser()
    return None


def _resolve_log_path(log_file: str | None, config_path: str) -> Path | None:
    if not log_file:
        return None
    candidate = Path(log_file).expanduser()
    if not candidate.is_absolute():
        candidate = Path(config_path).expanduser().parent / candidate
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _run_foreground(config_path: str) -> None:
    application = GatewayApplication(config_path)
    try:
        asyncio.run(application.run())
    except KeyboardInterrupt:  # pragma: no cover - CLI convenience
        pass


def _start_daemon(config_path: str, pid_file: Path, log_path: Path | None, chdir: str, umask: int) -> None:
    def _run() -> None:
        previous_umask = os.umask(umask)
        previous_stdout = sys.stdout
        previous_stderr = sys.stderr
        log_handle = None
        try:
            if log_path:
                try:
                    log_handle = log_path.open('a', buffering=1, encoding='utf-8')
                except OSError as exc:  # pragma: no cover - path permissions
                    raise CLIError(f'Unable to open log file {log_path}: {exc}') from exc
                os.dup2(log_handle.fileno(), sys.stdout.fileno())
                os.dup2(log_handle.fileno(), sys.stderr.fileno())
                sys.stdout = log_handle
                sys.stderr = log_handle
            logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True, handlers=[logging.StreamHandler(sys.stderr)])
            _run_foreground(config_path)
        finally:
            if log_handle:
                with contextlib.suppress(OSError):
                    log_handle.flush()
                    log_handle.close()
                sys.stdout = previous_stdout
                sys.stderr = previous_stderr
            os.umask(previous_umask)

    daemon_pid = pid_file.with_name(pid_file.name + '.daemon')
    try:
        daemon_pid.unlink()
    except OSError:
        pass
    daemon = Daemonize(app='modbusgw-service', pid=str(daemon_pid), action=_run, chdir=chdir)

    def _cleanup_daemon_pidfile() -> None:
        try:
            daemon_pid.unlink()
        except OSError:
            pass

    daemon.exit = _cleanup_daemon_pidfile
    try:
        daemon.start()
    except Exception as exc:  # pragma: no cover - daemonize failures
        raise CLIError(f'Failed to daemonize gateway: {exc}') from exc


class ServiceController:
    """Manage gateway lifecycle via PID files and signals."""

    def __init__(self, config_path: str):
        self.config_path = str(Path(config_path).expanduser())
        self._config: GatewayConfig | None = None

    def _load_config(self) -> GatewayConfig:
        if self._config is None:
            self._config = load_config(self.config_path)
        return self._config

    def pid_file_path(self, *, strict: bool) -> Path:
        if strict:
            cfg = self._load_config()
            return Path(cfg.service.pid_file).expanduser()
        if self._config is not None:
            return Path(self._config.service.pid_file).expanduser()
        try:
            cfg = self._load_config()
            return Path(cfg.service.pid_file).expanduser()
        except Exception:
            fallback = _pid_file_from_raw_config(self.config_path)
            if fallback:
                return fallback
            return Path(ServiceConfig().pid_file).expanduser()


    def _service_log_file(self) -> str | None:
        cfg = self._load_config()
        if cfg.service.log_file is None:
            return None
        return str(Path(cfg.service.log_file).expanduser())

    def start(self, *, foreground: bool, log_file: str | None, chdir: str, umask: int) -> None:
        pid_file = self.pid_file_path(strict=True)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_pid_file(pid_file)
        if existing and _pid_is_running(existing):
            raise CLIError(f'Gateway already running (PID {existing})')
        with contextlib.suppress(OSError):
            pid_file.unlink()
        if foreground:
            print(f'modbusgw starting in foreground using {self.config_path}')
            _run_foreground(self.config_path)
            return
        resolved_log = log_file if log_file is not None else self._service_log_file()
        log_path = _resolve_log_path(resolved_log, self.config_path)
        _start_daemon(self.config_path, pid_file, log_path, chdir, umask)
        pid = _read_pid_file(pid_file)
        if pid:
            print(f'modbusgw daemonized (PID {pid}) using {self.config_path}')
        else:
            print(f'modbusgw daemonized using {self.config_path}')

    def stop(self, timeout: float) -> None:
        pid_file = self.pid_file_path(strict=False)
        pid = _read_pid_file(pid_file)
        if not pid:
            raise CLIError('Gateway is not running (pidfile missing or unreadable)')
        if not _pid_is_running(pid):
            with contextlib.suppress(OSError):
                pid_file.unlink()
            raise CLIError('Gateway pidfile exists but process is not running')
        os.kill(pid, signal.SIGTERM)
        if not _wait_for_exit(pid, timeout):
            os.kill(pid, signal.SIGKILL)
            if not _wait_for_exit(pid, 5.0):
                raise CLIError(f'Gateway did not exit within {timeout} seconds')
        with contextlib.suppress(OSError):
            pid_file.unlink()

    def reload(self) -> None:
        pid_file = self.pid_file_path(strict=False)
        pid = _read_pid_file(pid_file)
        if not pid or not _pid_is_running(pid):
            raise CLIError('Gateway is not running')
        os.kill(pid, signal.SIGHUP)

    def status(self) -> tuple[bool, str]:
        pid_file = self.pid_file_path(strict=False)
        pid = _read_pid_file(pid_file)
        if not pid:
            return False, 'modbusgw is not running'
        if _pid_is_running(pid):
            return True, f'modbusgw running as PID {pid}'
        return False, 'modbusgw pidfile exists but process not running'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='ModBus gateway daemon controller')
    parser.add_argument('-c', '--config', default=str(DEFAULT_CONFIG), help='Path to JSON configuration file')
    parser.add_argument('--log-file', dest='log_file', default=None, help='Log file for daemonized mode (stdout/stderr redirection)')
    subparsers = parser.add_subparsers(dest='command')

    log_option_parent = argparse.ArgumentParser(add_help=False)
    log_option_parent.add_argument('--log-file', dest='log_file', default=argparse.SUPPRESS, help='Log file for daemonized mode (stdout/stderr redirection)')

    start_parent = argparse.ArgumentParser(add_help=False)
    start_parent.add_argument('--foreground', action='store_true', help='Run in the foreground (default: daemonize)')
    start_parent.add_argument('--chdir', default='/', help='Working directory for daemonized mode (default: /)')
    start_parent.add_argument('--umask', type=_parse_umask, default=DEFAULT_UMASK, help='Umask for daemonized mode (octal, default 022)')

    stop_parent = argparse.ArgumentParser(add_help=False)
    stop_parent.add_argument('--timeout', type=float, default=15.0, help='Seconds to wait for shutdown')

    start_parser = subparsers.add_parser('start', parents=[log_option_parent, start_parent], help='Start the gateway service')
    start_parser.set_defaults(handler=_command_start)

    stop_parser = subparsers.add_parser('stop', parents=[log_option_parent, stop_parent], help='Request a graceful shutdown')
    stop_parser.set_defaults(handler=_command_stop)

    reload_parser = subparsers.add_parser('reload', parents=[log_option_parent], help='Trigger a configuration reload via SIGHUP')
    reload_parser.set_defaults(handler=_command_reload)

    status_parser = subparsers.add_parser('status', parents=[log_option_parent], help='Show daemon status')
    status_parser.set_defaults(handler=_command_status)

    restart_parser = subparsers.add_parser('restart', parents=[log_option_parent, start_parent, stop_parent], help='Restart the service')
    restart_parser.set_defaults(handler=_command_restart)

    return parser


def _command_start(args: argparse.Namespace, controller: ServiceController) -> None:
    controller.start(foreground=args.foreground, log_file=args.log_file, chdir=args.chdir, umask=args.umask)


def _command_stop(args: argparse.Namespace, controller: ServiceController) -> None:
    controller.stop(timeout=args.timeout)
    print('modbusgw stopped')


def _command_reload(_: argparse.Namespace, controller: ServiceController) -> None:
    controller.reload()
    print('modbusgw reload requested')


def _command_status(_: argparse.Namespace, controller: ServiceController) -> None:
    running, message = controller.status()
    print(message)
    if not running:
        raise CLIError('Service not running')


def _command_restart(args: argparse.Namespace, controller: ServiceController) -> None:
    try:
        controller.stop(timeout=args.timeout)
    except CLIError:
        pass
    controller.start(foreground=args.foreground, log_file=args.log_file, chdir=args.chdir, umask=args.umask)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace, ServiceController], None] | None = getattr(args, 'handler', None)
    if handler is None:
        args.command = 'start'
        args.foreground = True
        args.log_file = getattr(args, 'log_file', None)
        args.chdir = getattr(args, 'chdir', '/')
        args.umask = getattr(args, 'umask', DEFAULT_UMASK)
        handler = _command_start
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    controller = ServiceController(args.config)
    try:
        handler(args, controller)
    except CLIError as exc:
        print(f'[error] {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':  # pragma: no cover - CLI usage
    main()
