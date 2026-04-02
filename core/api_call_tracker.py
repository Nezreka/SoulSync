"""
Centralized API call tracker for all enrichment services.

Tracks actual API calls (not items processed) with rolling timestamps
for real-time rate monitoring and minute-bucketed history for 24-hour graphs.
Thread-safe, no persistence (resets on restart).
"""

import threading
import time
from collections import deque, defaultdict


# Known rate limits per service (calls/minute)
RATE_LIMITS = {
    'spotify': 171,       # MIN_API_INTERVAL=0.35s → ~171/min
    'itunes': 20,         # MIN_API_INTERVAL=3.0s → ~20/min
    'deezer': 60,         # MIN_API_INTERVAL=1.0s → ~60/min
    'lastfm': 300,        # MIN_API_INTERVAL=0.2s → ~300/min
    'genius': 30,         # MIN_API_INTERVAL=2.0s → ~30/min
    'musicbrainz': 60,    # MIN_API_INTERVAL=1.0s → ~60/min
    'audiodb': 30,        # MIN_API_INTERVAL=2.0s → ~30/min
    'tidal': 120,         # MIN_API_INTERVAL=0.5s → ~120/min
    'qobuz': 60,          # Variable throttle, ~60/min estimate
}

# Display names for UI
SERVICE_LABELS = {
    'spotify': 'Spotify',
    'itunes': 'Apple Music',
    'deezer': 'Deezer',
    'lastfm': 'Last.fm',
    'genius': 'Genius',
    'musicbrainz': 'MusicBrainz',
    'audiodb': 'AudioDB',
    'tidal': 'Tidal',
    'qobuz': 'Qobuz',
}

# Display order
SERVICE_ORDER = [
    'spotify', 'itunes', 'deezer', 'lastfm', 'genius',
    'musicbrainz', 'audiodb', 'tidal', 'qobuz',
]


class ApiCallTracker:
    """Centralized tracker for actual API calls across all enrichment services."""

    def __init__(self):
        self._lock = threading.Lock()
        # Recent call timestamps per service (last 60s window for speedometer)
        # maxlen=600 covers 10 calls/sec for 60s — more than any service does
        self._recent_calls = defaultdict(lambda: deque(maxlen=600))

        # 24-hour minute-bucketed history per service
        # Each entry: (minute_floor_timestamp, call_count)
        self._minute_history = defaultdict(lambda: deque(maxlen=1440))
        self._current_minute_counts = defaultdict(int)
        self._current_minute_ts = {}

    def record_call(self, service_key, endpoint=None):
        """Record an API call. Called from rate_limited decorators.

        Args:
            service_key: Service identifier ('spotify', 'itunes', etc.)
            endpoint: Optional endpoint name for per-endpoint tracking (Spotify only)
        """
        now = time.time()
        minute_floor = int(now // 60) * 60

        with self._lock:
            # Record in recent timestamps
            self._recent_calls[service_key].append(now)
            # Roll minute bucket
            self._roll_minute(service_key, minute_floor)

            # Spotify per-endpoint tracking
            if endpoint and service_key == 'spotify':
                ep_key = f"spotify:{endpoint}"
                self._recent_calls[ep_key].append(now)
                self._roll_minute(ep_key, minute_floor)

    def _roll_minute(self, key, minute_floor):
        """Roll the minute bucket forward if we've moved to a new minute.
        Must be called while holding self._lock."""
        prev_ts = self._current_minute_ts.get(key)

        if prev_ts is None or minute_floor > prev_ts:
            # Flush previous minute's count to history
            if prev_ts is not None and self._current_minute_counts[key] > 0:
                self._minute_history[key].append((prev_ts, self._current_minute_counts[key]))
            # Fill gaps with zeros (if minutes were skipped)
            if prev_ts is not None:
                gap_start = prev_ts + 60
                while gap_start < minute_floor:
                    self._minute_history[key].append((gap_start, 0))
                    gap_start += 60
            # Start new minute
            self._current_minute_ts[key] = minute_floor
            self._current_minute_counts[key] = 1
        else:
            self._current_minute_counts[key] += 1

    def get_calls_per_minute(self, service_key):
        """Get current calls/minute rate from last 60 seconds."""
        now = time.time()
        cutoff = now - 60.0

        with self._lock:
            dq = self._recent_calls.get(service_key)
            if not dq:
                return 0.0
            count = sum(1 for ts in dq if ts > cutoff)
            return float(count)

    def get_24h_history(self, service_key):
        """Return list of [minute_timestamp, count] for last 24 hours.
        Includes the current in-progress minute."""
        now = time.time()
        cutoff = now - 86400

        with self._lock:
            history = []
            for ts, count in self._minute_history.get(service_key, []):
                if ts >= cutoff:
                    history.append([ts, count])

            # Include current minute in progress
            cur_ts = self._current_minute_ts.get(service_key)
            cur_count = self._current_minute_counts.get(service_key, 0)
            if cur_ts is not None and cur_count > 0 and cur_ts >= cutoff:
                history.append([cur_ts, cur_count])

            return history

    def get_all_rates(self):
        """Get current rates for all services. Used by WebSocket emission."""
        result = {}
        for svc in SERVICE_ORDER:
            cpm = self.get_calls_per_minute(svc)
            entry = {
                'cpm': round(cpm, 1),
                'limit': RATE_LIMITS.get(svc, 60),
            }

            # Spotify per-endpoint breakdown
            if svc == 'spotify':
                endpoints = {}
                for key in list(self._recent_calls.keys()):
                    if key.startswith('spotify:'):
                        ep_name = key[8:]  # strip 'spotify:'
                        ep_cpm = self.get_calls_per_minute(key)
                        if ep_cpm > 0:
                            endpoints[ep_name] = round(ep_cpm, 1)
                entry['endpoints'] = endpoints

            result[svc] = entry
        return result


# Singleton instance
api_call_tracker = ApiCallTracker()
