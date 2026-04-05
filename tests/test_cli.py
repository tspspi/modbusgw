"""CLI lifecycle helper tests."""
from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from modbusgw import cli


def _write_config(tmp_path: Path, pid_path: Path, *, log_file: str | None = None) -> Path:
    service = {
        'pid_file': str(pid_path),
        'state_dir': str(tmp_path / 'state'),
        'log_level': 'INFO',
    }
    if log_file:
        service['log_file'] = log_file
    cfg = {
        'service': service,
        'bus': {'request_queue_size': 16, 'response_timeout_ms': 1500},
        'frontends': [],
        'backends': [],
        'routes': [],
    }
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(cfg), encoding='utf-8')
    return config_path


def test_read_pid_file(tmp_path: Path) -> None:
    pid_file = tmp_path / 'modbusgw.pid'
    assert cli._read_pid_file(pid_file) is None
    pid_file.write_text('123\n', encoding='utf-8')
    assert cli._read_pid_file(pid_file) == 123
    pid_file.write_text('oops', encoding='utf-8')
    assert cli._read_pid_file(pid_file) is None


def test_pid_is_running(tmp_path: Path) -> None:
    proc = subprocess.Popen(['sleep', '2'])
    try:
        assert cli._pid_is_running(proc.pid)
    finally:
        proc.terminate()
        proc.wait()
    assert not cli._pid_is_running(proc.pid)


def test_controller_stop_terminates_process(tmp_path: Path) -> None:
    pid_file = tmp_path / 'gw.pid'
    config_path = _write_config(tmp_path, pid_file)
    controller = cli.ServiceController(str(config_path))

    launcher = subprocess.Popen(
        ['sh', '-c', 'sleep 30 >/dev/null & echo $!'], stdout=subprocess.PIPE, text=True
    )
    assert launcher.stdout is not None
    daemon_pid = int(launcher.stdout.readline().strip())
    launcher.wait()

    pid_file.write_text(str(daemon_pid), encoding='utf-8')
    try:
        controller.stop(timeout=5.0)
    finally:
        try:
            os.kill(daemon_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert not pid_file.exists()


def test_status_handles_stale_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / 'gw.pid'
    config_path = _write_config(tmp_path, pid_file)
    controller = cli.ServiceController(str(config_path))

    pid_file.write_text('424242', encoding='utf-8')
    running, message = controller.status()
    assert running is False
    assert 'not running' in message.lower()


def test_start_foreground_uses_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / 'gw.pid'
    config_path = _write_config(tmp_path, pid_file)
    controller = cli.ServiceController(str(config_path))

    captured: dict[str, str] = {}

    def fake_run(path: str) -> None:
        captured['path'] = path

    monkeypatch.setattr(cli, '_run_foreground', fake_run)
    controller.start(foreground=True, log_file=None, chdir='/', umask=0o022)
    assert captured['path'] == str(config_path)


def test_start_rejects_running_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / 'gw.pid'
    config_path = _write_config(tmp_path, pid_file)
    controller = cli.ServiceController(str(config_path))

    proc = subprocess.Popen(['sleep', '30'])
    pid_file.write_text(str(proc.pid), encoding='utf-8')
    monkeypatch.setattr(cli, '_run_foreground', lambda _: None)
    try:
        with pytest.raises(cli.CLIError):
            controller.start(foreground=True, log_file=None, chdir='/', umask=0o022)
    finally:
        proc.terminate()
        proc.wait()


def test_parser_allows_global_log_file() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(['--log-file', '/tmp/foo', 'start', '--foreground'])
    assert args.log_file == '/tmp/foo'
    args = parser.parse_args(['start', '--log-file', '/tmp/bar', '--foreground'])
    assert args.log_file == '/tmp/bar'
    parser.parse_args(['stop', '--log-file', '/tmp/ignored'])
    parser.parse_args(['reload', '--log-file', '/tmp/ignored'])
    parser.parse_args(['status', '--log-file', '/tmp/ignored'])


def test_start_uses_config_log_file_when_not_overridden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / 'gw.pid'
    config_log = tmp_path / 'config.log'
    config_path = _write_config(tmp_path, pid_file, log_file=str(config_log))
    controller = cli.ServiceController(str(config_path))

    captured: dict[str, Path | None] = {}

    def fake_start_daemon(cfg: str, pid: Path, log_path: Path | None, chdir: str, umask: int) -> None:
        captured['log_path'] = log_path

    monkeypatch.setattr(cli, '_start_daemon', fake_start_daemon)
    controller.start(foreground=False, log_file=None, chdir='/', umask=0o022)
    assert captured['log_path'] == config_log


def test_start_prefers_cli_log_file_over_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / 'gw.pid'
    config_log = tmp_path / 'config.log'
    cli_log = tmp_path / 'cli.log'
    config_path = _write_config(tmp_path, pid_file, log_file=str(config_log))
    controller = cli.ServiceController(str(config_path))

    captured: dict[str, Path | None] = {}

    def fake_start_daemon(cfg: str, pid: Path, log_path: Path | None, chdir: str, umask: int) -> None:
        captured['log_path'] = log_path

    monkeypatch.setattr(cli, '_start_daemon', fake_start_daemon)
    controller.start(foreground=False, log_file=str(cli_log), chdir='/', umask=0o022)
    assert captured['log_path'] == cli_log
