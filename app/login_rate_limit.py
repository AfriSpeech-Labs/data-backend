"""Simple in-memory rate limit for contributor PIN attempts (per normalized phone)."""
from __future__ import annotations

import time

_WINDOW_SEC = 900
_MAX_ATTEMPTS = 15
_attempt_ts: dict[str, list[float]] = {}


def rate_limit_allow(key: str) -> bool:
    now = time.monotonic()
    arr = [t for t in _attempt_ts.get(key, []) if now - t < _WINDOW_SEC]
    if len(arr) >= _MAX_ATTEMPTS:
        _attempt_ts[key] = arr
        return False
    arr.append(now)
    _attempt_ts[key] = arr
    return True


def rate_limit_reset(key: str) -> None:
    _attempt_ts.pop(key, None)
