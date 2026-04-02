"""Tracing recorder placeholder."""
from __future__ import annotations


class TraceRecorder:
    def __init__(self, output_file: str) -> None:
        self.output_file = output_file

    def enable(self) -> None:  # pragma: no cover - placeholder
        raise NotImplementedError
