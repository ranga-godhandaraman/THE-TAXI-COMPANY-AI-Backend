"""Lightweight in-memory rate limiting for auth endpoints (POC)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    """
    Return True if the request is allowed.
    Sliding window per key (e.g. client IP + route).
    """
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        q = _buckets[key]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset_rate_limits() -> None:
    """Test helper."""
    with _lock:
        _buckets.clear()
