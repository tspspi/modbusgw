"""Serial RTU frontend implemented via pseudo-terminal."""
from __future__ import annotations

import asyncio
import contextlib
import os
import pty
import tty
from pathlib import Path

from .base import FrontendBase
from ..config.models import SerialRtuSocketConfig
from ..core.bus import GatewayBus
from ..core.messages import ModbusRequest, RequestContext, RoutedRequest, RoutedResponse
from ..utils.crc import crc16_modbus


class SerialRTUFrontend(FrontendBase):
    """Expose a PTY that feeds decoded Modbus RTU requests into the GatewayBus."""

    def __init__(self, config: SerialRtuSocketConfig, bus: GatewayBus) -> None:
        self.config = config
        self.bus = bus
        self.name = config.id
        self._master_fd: int | None = None
        self._symlink_path = Path(config.socket_path)
        self._slave_path: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._raw_fd: int | None = None
        self._buffer = bytearray()
        self._buffer_lock = asyncio.Lock()
        self._flush_handle: asyncio.Handle | None = None
        self._frame_timeout = self.config.frame_timeout_ms / 1000.0

    @property
    def slave_path(self) -> str | None:
        """Expose the real PTY slave path (useful for tests/diagnostics)."""
        return self._slave_path

    async def start(self) -> None:
        if self._master_fd is not None:
            return
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        try:
            slave_path = os.ttyname(slave_fd)
        finally:
            os.close(slave_fd)
        self._slave_path = slave_path
        self._prepare_symlink(slave_path)
        self._set_raw_mode(slave_path)
        self._loop = asyncio.get_running_loop()
        self._reader_task = self._loop.create_task(self._read_loop())

    async def stop(self) -> None:
        if self._master_fd is None:
            return
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        os.close(self._master_fd)
        self._master_fd = None
        if self._raw_fd is not None:
            os.close(self._raw_fd)
            self._raw_fd = None
        if self._symlink_path.exists() or self._symlink_path.is_symlink():
            self._symlink_path.unlink()

    def _set_raw_mode(self, slave_path: str) -> None:
        self._raw_fd = os.open(slave_path, os.O_RDWR | os.O_NOCTTY)
        tty.setraw(self._raw_fd)

    def _prepare_symlink(self, slave_path: str) -> None:
        target = self._symlink_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        os.symlink(slave_path, target)

    async def _read_loop(self) -> None:
        assert self._master_fd is not None
        while True:
            data = await asyncio.to_thread(os.read, self._master_fd, 4096)
            if not data:
                await asyncio.sleep(0.01)
                continue
            async with self._buffer_lock:
                self._buffer.extend(data)
                self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._loop is None:
            return
        if self._flush_handle:
            self._flush_handle.cancel()
        self._flush_handle = self._loop.call_later(self._frame_timeout, self._trigger_flush)

    def _trigger_flush(self) -> None:
        if self._loop is None:
            return
        self._flush_handle = None
        self._loop.create_task(self._finalize_frame())

    async def _finalize_frame(self) -> None:
        async with self._buffer_lock:
            if not self._buffer:
                return
            frame = bytes(self._buffer)
            self._buffer.clear()
        if len(frame) < 4:
            return
        crc_expected = int.from_bytes(frame[-2:], byteorder='little')
        crc_actual = crc16_modbus(frame[:-2])
        if crc_expected != crc_actual:
            return
        pdu = ModbusRequest.from_adu(frame[:-2])
        context = RequestContext(
            frontend=self.name,
            metadata={'transport': 'serial_rtu', 'socket': str(self._symlink_path)},
        )
        message = RoutedRequest(context=context, pdu=pdu)
        await self.bus.publish('requests', message)

    async def handle_response(self, message: RoutedResponse) -> None:
        if message.frontend != self.name:
            return
        frame = self._encode_response(message)
        if frame is None:
            return
        master_fd = self._master_fd
        if master_fd is None:
            return
        await asyncio.to_thread(os.write, master_fd, frame)

    def _encode_response(self, message: RoutedResponse) -> bytes | None:
        pdu = message.response or message.exception
        if pdu is None:
            return None
        adu = pdu.to_adu()
        return adu + crc16_modbus(adu).to_bytes(2, byteorder='little')
