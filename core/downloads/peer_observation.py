"""Short-lived, process-local Soulseek peer throughput observations."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


_TTL_SECONDS = 3600
_MAX_PEERS = 512
# Ranking signal, intentionally separate from the user-configured retry floor.
HEALTHY_PEER_BPS = 500_000
_lock = threading.Lock()


@dataclass
class _Observation:
    bytes_per_second: float
    updated_at: float
    sample_count: int


_observations: dict[str, _Observation] = {}


def observe_peer(username: str, bytes_per_second: float, sample_seconds: float) -> None:
    """Record only a meaningful moving transfer; never store an IP or path."""
    if not username or sample_seconds < 10 or bytes_per_second < 0:
        return
    now = time.monotonic()
    weight = min(0.7, max(0.15, sample_seconds / 120))
    with _lock:
        stale = [peer for peer, row in _observations.items()
                 if now - row.updated_at > _TTL_SECONDS]
        for peer in stale:
            del _observations[peer]
        previous = _observations.get(username)
        if previous is None:
            if len(_observations) >= _MAX_PEERS:
                oldest = min(_observations, key=lambda peer: _observations[peer].updated_at)
                del _observations[oldest]
            _observations[username] = _Observation(float(bytes_per_second), now, 1)
        else:
            previous.bytes_per_second = (
                previous.bytes_per_second * (1 - weight) + bytes_per_second * weight
            )
            previous.updated_at = now
            previous.sample_count += 1


def peer_speed(username: str) -> float | None:
    if not username:
        return None
    now = time.monotonic()
    with _lock:
        row = _observations.get(username)
        if row is None or now - row.updated_at > _TTL_SECONDS:
            if row is not None:
                del _observations[username]
            return None
        return row.bytes_per_second
