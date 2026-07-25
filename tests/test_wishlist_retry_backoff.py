"""javiavid — wishlist retry accounting + progressive backoff + ignore TTL.

The attempt counter existed but was DEAD: update_wishlist_retry (the only
retry_count increment) had a single caller, mark_track_download_result, which
itself had no callers — so retry_count stayed 0 forever and the 3.1.1 failing
badge/filter (keyed on retry_count >= 3) never fired on the music side.

Under test:
  * record_failed_attempt stamps every failed cycle attempt (fresh add AND
    duplicate-skip), feeding the badge and the backoff
  * the backoff ladder (0-1 → none, 2 → 4h, 3 → 24h, 4+ → 7d), fail-open on
    unparseable timestamps, and the due/cooling split
  * scheduled cycles apply backoff, the manual Process Now click does not
    (source contract — automation_id gates it)
  * IGNORE_TTL_DAYS honors wishlist.ignore_ttl_days (clamped 1-365)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.wishlist.retry_backoff import (
    cooldown_seconds,
    is_due,
    split_due_for_retry,
)

_ROOT = Path(__file__).resolve().parent.parent


# ── the ladder ───────────────────────────────────────────────────────────────

def test_cooldown_ladder():
    assert cooldown_seconds(0) == 0
    assert cooldown_seconds(1) == 0
    assert cooldown_seconds(2) == 4 * 3600
    assert cooldown_seconds(3) == 24 * 3600
    assert cooldown_seconds(4) == 7 * 24 * 3600
    assert cooldown_seconds(25) == 7 * 24 * 3600
    assert cooldown_seconds(None) == 0
    assert cooldown_seconds("nope") == 0


def test_is_due_and_split():
    now = datetime(2026, 7, 23, 12, 0, 0)
    fresh = {"retry_count": 0, "last_attempted": None}
    twice_recent = {"retry_count": 2,
                    "last_attempted": (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')}
    twice_stale = {"retry_count": 2,
                   "last_attempted": (now - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S')}
    chronic = {"retry_count": 9,
               "last_attempted": (now - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')}
    chronic_due = {"retry_count": 9,
                   "last_attempted": (now - timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S')}
    broken_ts = {"retry_count": 9, "last_attempted": "not a date"}   # fail-open

    assert is_due(fresh, now) is True
    assert is_due(twice_recent, now) is False
    assert is_due(twice_stale, now) is True
    assert is_due(chronic, now) is False
    assert is_due(chronic_due, now) is True
    assert is_due(broken_ts, now) is True

    due, cooling = split_due_for_retry(
        [fresh, twice_recent, twice_stale, chronic, chronic_due, broken_ts], now)
    assert len(due) == 4 and len(cooling) == 2


# ── the counter finally counts ───────────────────────────────────────────────

class _ForwardingService:
    """mark_track_download_result → the real DB method (hermetic singleton-free)."""

    def __init__(self, db):
        self.db = db

    def mark_track_download_result(self, spotify_track_id, success,
                                   error_message=None, profile_id=1):
        return self.db.update_wishlist_retry(spotify_track_id, success,
                                             error_message, profile_id=profile_id)


def _wishlisted_track(db, sp_id="trk1"):
    payload = {
        'id': sp_id, 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
        'album': {'id': 'a1', 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
                  'images': [], 'album_type': 'single', 'release_date': '2020-01-01',
                  'total_tracks': 1},
        'duration_ms': 1000, 'track_number': 1, 'disc_number': 1,
    }
    assert db.add_to_wishlist(spotify_track_data=payload, failure_reason='Not found',
                              source_type='wishlist', source_info='{}', profile_id=1)


def test_record_failed_attempt_accumulates(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db)
    svc = _ForwardingService(db)

    assert record_failed_attempt(svc, {'id': 'trk1'}, 'Not found', 1) is True
    assert record_failed_attempt(svc, {'id': 'trk1'}, 'Still not found', 1) is True
    row = db.get_wishlist_tracks()[0]
    assert row['retry_count'] == 2
    assert row['last_attempted']                       # stamped
    assert row['failure_reason'] == 'Still not found'


def test_record_failed_attempt_guards(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    assert record_failed_attempt(svc, {'id': 'wing_it_x'}, 'e', 1) is False   # wing-it skip
    assert record_failed_attempt(svc, {}, 'e', 1) is False                    # no id
    assert record_failed_attempt(svc, None, 'e', 1) is False                  # bad shape
    assert record_failed_attempt(svc, {'id': 'unknown'}, 'e', 1) is False     # no row → no-op

    class _Boom:
        def mark_track_download_result(self, *a, **k):
            raise RuntimeError('db locked')
    assert record_failed_attempt(_Boom(), {'id': 'x'}, 'e', 1) is False       # swallowed


# ── wiring contracts ─────────────────────────────────────────────────────────

def test_failed_processor_stamps_every_attempt():
    src = (_ROOT / "core" / "downloads" / "wishlist_failed.py").read_text(encoding="utf-8")
    assert "_record_failed_attempt(" in src
    # the stamp must NOT be gated on the add succeeding — the duplicate-skip
    # IS the repeat-failure signal
    body = src[src.index("_record_failed_attempt("):]
    assert body.index("if success:") > 0


def test_backoff_applies_to_scheduled_cycles_only():
    src = (_ROOT / "core" / "wishlist" / "processing.py").read_text(encoding="utf-8")
    assert "split_due_for_retry" in src
    gate = src[src.index("split_due_for_retry") - 700:src.index("split_due_for_retry")]
    assert "automation_id is not None" in gate     # manual Process Now bypasses
    assert "apply_backoff" in gate                 # pipelines can opt in explicitly


# ── configurable ignore TTL ──────────────────────────────────────────────────

def test_ignore_ttl_reads_config(monkeypatch):
    import core.wishlist.ignore as ig

    class _Cfg:
        def __init__(self, v):
            self.v = v

        def get(self, key, default=None):
            return self.v if key == 'wishlist.ignore_ttl_days' else default

    import config.settings as cs
    monkeypatch.setattr(cs, 'config_manager', _Cfg(7))
    assert ig.configured_ttl_days() == 7
    monkeypatch.setattr(cs, 'config_manager', _Cfg(9999))
    assert ig.configured_ttl_days() == 365          # clamped
    monkeypatch.setattr(cs, 'config_manager', _Cfg('garbage'))
    assert ig.configured_ttl_days() == 30           # fallback
    monkeypatch.setattr(cs, 'config_manager', _Cfg(0))
    assert ig.configured_ttl_days() == 1            # floor
