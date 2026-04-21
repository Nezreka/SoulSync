"""
Centralized API call tracker for all enrichment services.

Tracks actual API calls (not items processed) with rolling timestamps
for real-time rate monitoring and minute-bucketed history for 24-hour graphs.
Thread-safe, persists 24h history to disk on shutdown and restores on startup.
"""

import json
import os
import threading
import time
from collections import deque, defaultdict

from utils.logging_config import get_logger


logger = get_logger("api_call_tracker")


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
    'discogs': 60,        # MIN_API_INTERVAL=1.0s with auth → ~60/min
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
    'discogs': 'Discogs',
}

# Display order
SERVICE_ORDER = [
    'spotify', 'itunes', 'deezer', 'lastfm', 'genius',
    'musicbrainz', 'audiodb', 'tidal', 'qobuz', 'discogs',
]


_PERSIST_PATH = os.path.join('database', 'api_call_history.json')


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

        # Rate limit event log — records bans, peaks, escalations
        # Each entry: {ts, event, service, endpoint, duration, detail}
        self._events = deque(maxlen=200)

        # Restore persisted history from disk
        self._load()

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

    def record_event(self, service_key, event_type, detail='', endpoint='', duration=0):
        """Record a rate limit event (ban, escalation, cooldown, etc.).
        Called from spotify_client.py when rate limits are detected."""
        with self._lock:
            self._events.append({
                'ts': time.time(),
                'event': event_type,
                'service': service_key,
                'endpoint': endpoint,
                'duration': duration,
                'detail': detail,
            })

    def get_events(self, since=None):
        """Get rate limit events, optionally filtered by timestamp."""
        cutoff = since or (time.time() - 86400)
        with self._lock:
            return [e for e in self._events if e['ts'] >= cutoff]

    def get_debug_summary(self):
        """Build a comprehensive debug summary for Copy Debug Info.
        Includes 24h totals, peaks, rate limit events, and per-endpoint breakdown."""
        now = time.time()
        cutoff_24h = now - 86400
        summary = {}

        with self._lock:
            for svc in SERVICE_ORDER:
                # 24h total calls
                total = 0
                peak_cpm = 0
                peak_ts = 0
                for ts, count in self._minute_history.get(svc, []):
                    if ts >= cutoff_24h:
                        total += count
                        if count > peak_cpm:
                            peak_cpm = count
                            peak_ts = ts
                # Include current minute
                cur_ts = self._current_minute_ts.get(svc)
                cur_count = self._current_minute_counts.get(svc, 0)
                if cur_ts and cur_ts >= cutoff_24h:
                    total += cur_count
                    if cur_count > peak_cpm:
                        peak_cpm = cur_count
                        peak_ts = cur_ts

                if total == 0:
                    continue

                entry = {
                    'total_24h': total,
                    'peak_cpm': peak_cpm,
                    'limit_cpm': RATE_LIMITS.get(svc, 60),
                }
                if peak_ts:
                    entry['peak_at'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(peak_ts))
                summary[svc] = entry

                # Spotify per-endpoint breakdown
                if svc == 'spotify':
                    ep_totals = {}
                    for key in list(self._minute_history.keys()):
                        if key.startswith('spotify:'):
                            ep_name = key[8:]
                            ep_total = sum(c for ts, c in self._minute_history[key] if ts >= cutoff_24h)
                            cur = self._current_minute_counts.get(key, 0)
                            ep_total += cur
                            if ep_total > 0:
                                ep_totals[ep_name] = ep_total
                    if ep_totals:
                        summary[svc]['endpoints'] = ep_totals

            # Rate limit events
            events = [e for e in self._events if e['ts'] >= cutoff_24h]

        if events:
            summary['_rate_limit_events'] = [{
                'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['ts'])),
                'event': e['event'],
                'service': e['service'],
                'endpoint': e.get('endpoint', ''),
                'duration': e.get('duration', 0),
                'detail': e.get('detail', ''),
            } for e in events[-20:]]  # Last 20 events

        return summary

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


    def save(self):
        """Persist 24h minute history to disk. Call on shutdown."""
        try:
            now = time.time()
            cutoff = now - 86400
            data = {}
            with self._lock:
                for key, hist in self._minute_history.items():
                    entries = [[ts, count] for ts, count in hist if ts >= cutoff]
                    # Include current in-progress minute
                    cur_ts = self._current_minute_ts.get(key)
                    cur_count = self._current_minute_counts.get(key, 0)
                    if cur_ts is not None and cur_count > 0 and cur_ts >= cutoff:
                        entries.append([cur_ts, cur_count])
                    if entries:
                        data[key] = entries
                events = [dict(e) for e in self._events if e['ts'] >= cutoff]
            with open(_PERSIST_PATH, 'w') as f:
                json.dump({'ts': now, 'history': data, 'events': events}, f)
        except Exception as e:
            logger.error(f"[ApiCallTracker] Failed to save history: {e}")

    def _load(self):
        """Restore 24h minute history from disk. Called on init."""
        try:
            if not os.path.exists(_PERSIST_PATH):
                return
            if os.path.getsize(_PERSIST_PATH) == 0:
                logger.info(f"[ApiCallTracker] History file is empty, starting fresh: {_PERSIST_PATH}")
                return
            with open(_PERSIST_PATH, 'r') as f:
                raw = json.load(f)
            saved_ts = raw.get('ts', 0)
            # Only restore if saved within last 24h
            if time.time() - saved_ts > 86400:
                return
            history = raw.get('history', {})
            events = raw.get('events', [])
            cutoff = time.time() - 86400
            with self._lock:
                for key, entries in history.items():
                    for ts, count in entries:
                        if ts >= cutoff:
                            self._minute_history[key].append((ts, count))
                for e in events:
                    if e.get('ts', 0) >= cutoff:
                        self._events.append(e)
            logger.info(f"[ApiCallTracker] Restored history for {len(history)} services, {len(events)} events")
        except json.JSONDecodeError as e:
            logger.warning(f"[ApiCallTracker] History file is not valid JSON, starting fresh: {_PERSIST_PATH} ({e})")
        except Exception as e:
            logger.error(f"[ApiCallTracker] Failed to load history: {e}")


# Singleton instance
api_call_tracker = ApiCallTracker()
