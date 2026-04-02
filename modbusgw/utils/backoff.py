"""Retry/backoff helpers."""
from __future__ import annotations

def exponential_backoff(attempt: int) -> float:
    return min(0.5 * (2 ** attempt), 30.0)
