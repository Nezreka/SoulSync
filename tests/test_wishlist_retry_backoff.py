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


def _wishlisted_track(db, sp_id="trk1", album_id="a1"):
    payload = {
        'id': sp_id, 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
        'album': {'id': album_id, 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
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


def test_retry_update_scopes_composite_keys_and_supports_bare_legacy_callers(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db, album_id="a1")
    _wishlisted_track(db, album_id="a2")

    assert db.update_wishlist_retry("trk1::a1", False, "first", profile_id=1)
    rows = {
        row["spotify_track_id"]: row
        for row in db.get_wishlist_tracks()
    }
    assert rows["trk1::a1"]["retry_count"] == 1
    assert rows["trk1::a2"]["retry_count"] == 0

    assert db.update_wishlist_retry("trk1", False, "both", profile_id=1)
    rows = {
        row["spotify_track_id"]: row
        for row in db.get_wishlist_tracks()
    }
    assert rows["trk1::a1"]["retry_count"] == 2
    assert rows["trk1::a2"]["retry_count"] == 1


def test_record_failed_attempt_guards(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    assert record_failed_attempt(svc, {'id': 'wing_it_x'}, 'e', 1) is False   # not on the wishlist
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


def test_wing_it_batch_no_longer_blanket_skips_wishlist():
    src = (_ROOT / "core" / "downloads" / "wishlist_failed.py").read_text(encoding="utf-8")
    # Used to return before ever reaching is_stub_id()/should_wishlist_stub(),
    # so wishlist.wing_it_guesses had no effect for Wing It download batches.
    # Only the per-track gate should govern now.
    assert "batch.get('wing_it')" not in src
    assert "is_stub_id(sp_id) and not should_wishlist_stub(_artist" in src


def test_backoff_applies_to_scheduled_cycles_only():
    src = (_ROOT / "core" / "wishlist" / "processing.py").read_text(encoding="utf-8")
    assert "split_due_for_retry" in src
    # Structural, not a character window: the old version measured 700 chars
    # back from split_due_for_retry and broke the moment anything landed
    # between the gate and its use (#1196 put the clock reset there).
    gate_line = next(ln for ln in src.splitlines()
                     if ln.strip().startswith("_backoff = apply_backoff"))
    assert "automation_id is not None" in gate_line   # manual Process Now bypasses
    assert "apply_backoff" in gate_line               # pipelines can opt in explicitly
    assert src.index("if _backoff:") < src.index("split_due_for_retry")


# ── configurable ignore TTL ──────────────────────────────────────────────────

def test_ignore_ttl_reads_config(monkeypatch):
    import core.wishlist.ignore as ig

    class _Cfg:
        def __init__(self, v):
            self.v = v

        def get(self, key, default=None):
            return self.v if key == 'wishlist.ignore_ttl_days' else default

    import core.settings as cs
    monkeypatch.setattr(cs, 'config_manager', _Cfg(7))
    assert ig.configured_ttl_days() == 7
    monkeypatch.setattr(cs, 'config_manager', _Cfg(9999))
    assert ig.configured_ttl_days() == 365          # clamped
    monkeypatch.setattr(cs, 'config_manager', _Cfg('garbage'))
    assert ig.configured_ttl_days() == 30           # fallback
    monkeypatch.setattr(cs, 'config_manager', _Cfg(0))
    assert ig.configured_ttl_days() == 1            # floor


def test_wing_it_track_on_the_wishlist_accrues_backoff(tmp_path):
    """A Wing It stub that reached the wishlist must be stamped like any other.

    It could not be, before wishlist.wing_it_guesses existed, because a stub was
    never on the wishlist to stamp — record_failed_attempt returned early on the
    id prefix. If that early return survived the setting, retry_count would stay
    0 forever and retry_backoff would never escalate the track, burning a fresh
    search every cycle on something that has failed for months."""
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt
    from core.wishlist.retry_backoff import cooldown_seconds

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db, sp_id='wing_it_e6e3736d43fb')
    svc = _ForwardingService(db)

    for _ in range(4):
        assert record_failed_attempt(
            svc, {'id': 'wing_it_e6e3736d43fb'}, 'Not found', 1) is True

    row = db.get_wishlist_tracks()[0]
    assert row['retry_count'] == 4
    # 4 failures earns the top cooldown tier, so it stops being retried hourly.
    assert cooldown_seconds(row['retry_count']) == 7 * 24 * 3600


# ── #1196 (Zombiehamser): stranded after the source came back ────────────────
# 674 tracks, 634 failing with "No matching track found" at retry 3-4 from a
# period when slskd was unreachable. After the source recovered, scheduled
# cycles kept sitting them out (24h / 7d) because the counters were stamped
# during the outage and nothing ever cleared them — and pressing "process
# wishlist" created no batch at all.

def test_manual_run_clears_the_retry_clock_on_failing_tracks(tmp_path):
    """A manual run means "the source is back". Selection already ignored
    backoff for it, but leaving retry_count=4 in the row meant the NEXT
    scheduled cycle still stranded the track for another 7 days."""
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db, sp_id='trk1')
    _wishlisted_track(db, sp_id='trk2')
    svc = _ForwardingService(db)
    for _ in range(4):
        record_failed_attempt(svc, {'id': 'trk1'}, 'No matching track found', 1)
    record_failed_attempt(svc, {'id': 'trk2'}, 'No matching track found', 1)

    # A wishlist row's id is per-ALBUM on this branch (`<track>::<album>`), so
    # index by the SOURCE track id the caller actually holds.
    def _by_source_id(database):
        return {str(r['spotify_track_id']).split('::')[0]: r
                for r in database.get_wishlist_tracks()}

    rows = _by_source_id(db)
    assert rows['trk1']['retry_count'] == 4          # 7-day cooldown earned
    assert cooldown_seconds(rows['trk1']['retry_count']) == 7 * 24 * 3600

    cleared = db.reset_wishlist_retry_backoff(['trk1', 'trk2'])
    assert cleared == 2
    rows = _by_source_id(db)
    assert rows['trk1']['retry_count'] == 0
    assert rows['trk1']['last_attempted'] is None
    # ...and the track is due again on the very next scheduled cycle
    assert is_due(rows['trk1'], datetime.utcnow()) is True


def test_reset_only_touches_rows_that_actually_failed(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db, sp_id='failed_one')
    _wishlisted_track(db, sp_id='never_tried')
    svc = _ForwardingService(db)
    record_failed_attempt(svc, {'id': 'failed_one'}, 'No matching track found', 1)

    # a never-attempted row has nothing to unstick, so it is not counted
    assert db.reset_wishlist_retry_backoff(['never_tried']) == 0
    assert db.reset_wishlist_retry_backoff(['failed_one']) == 1
    # an empty id list must not become "reset everything"
    assert db.reset_wishlist_retry_backoff([]) == 0


def test_reset_survives_a_wishlist_larger_than_sqlites_parameter_cap(tmp_path):
    """674 ids is fine; the chunking exists so a five-figure wishlist is too."""
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db, sp_id='real_one')
    svc = _ForwardingService(db)
    record_failed_attempt(svc, {'id': 'real_one'}, 'No matching track found', 1)

    ids = [f'ghost_{i}' for i in range(1500)] + ['real_one']
    assert db.reset_wishlist_retry_backoff(ids) == 1


def _auto_runtime(db, batches):
    """A runtime stub that drives the real process_wishlist_automatically."""
    import contextlib
    import threading

    from core.wishlist.processing import WishlistAutoProcessingRuntime

    class _Profiles:
        def get_all_profiles(self):
            return [{'id': 1, 'name': 'me'}]

    class _Executor:
        def submit(self, fn, *a, **k):
            class _F:
                def result(self, *_a, **_k):
                    return None

                def add_done_callback(self, *_a, **_k):
                    pass
            return _F()

    return WishlistAutoProcessingRuntime(
        processing_guard=lambda: contextlib.nullcontext(True),
        is_actually_processing=lambda: False,
        app_context_factory=lambda: contextlib.nullcontext(None),
        get_profiles_database=lambda: _Profiles(),
        get_music_database=lambda: db,
        download_batches=batches,
        tasks_lock=threading.RLock(),
        update_automation_progress=lambda *a, **k: None,
        automation_engine=None,
        missing_download_executor=_Executor(),
        run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3,
        get_active_server=lambda: 'plex',
        current_time_fn=lambda: 0.0,
    )


@pytest.fixture()
def _wishlist_singles(tmp_path, monkeypatch):
    """A singles-only wishlist stranded at retry 4, cycle parked on 'albums'.

    The service is a module singleton bound to the app database rather than
    injected through the runtime, so it has to be pointed at the fixture db.
    """
    from database.music_database import MusicDatabase
    import core.wishlist.service as svc
    from core.wishlist.state import set_wishlist_cycle

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    for i in range(3):
        _wishlisted_track(db, sp_id=f'u{i}')
        for _ in range(4):
            db.update_wishlist_retry(f'u{i}', success=False,
                                     error_message='No matching track found')

    service = svc.WishlistService()
    service._database = db
    monkeypatch.setattr(svc, '_wishlist_service', service)
    monkeypatch.setattr('core.wishlist.processing.get_wishlist_service', lambda: service)
    set_wishlist_cycle(lambda: db, 'albums')       # the EMPTY half — the coin flip
    return db


def test_a_user_click_never_idles_on_the_empty_half_of_the_cycle(_wishlist_singles):
    """The hidden albums/singles cycle made "process wishlist" a coin flip: if
    the cycle sat on the empty category the run toggled it and returned — no
    batch, no work, no message. That is what Zombiehamser hit (#1196).

    Behavioural on purpose: this drives the real entry point, because a
    source-text assertion would still pass if the branch stopped being
    reachable (an earlier guard bailing first is exactly what happened while
    building the harness for this)."""
    from core.wishlist.processing import process_wishlist_automatically
    from core.wishlist.state import get_wishlist_cycle

    db = _wishlist_singles
    batches: dict = {}
    process_wishlist_automatically(_auto_runtime(db, batches),
                                   automation_id=None, apply_backoff=None)

    assert batches, "a user click produced no batch"
    assert get_wishlist_cycle(lambda: db) == 'singles'
    # ...and the same click unstuck the 7-day backoff clock
    assert {r['retry_count'] for r in db.get_wishlist_tracks()} == {0}


def test_a_scheduled_run_keeps_its_old_cadence(_wishlist_singles):
    """The fix must not turn scheduled cycles into category-hunters: an empty
    category still toggles and waits for the next tick, and the retry clock is
    left alone (only a user saying "go" clears it)."""
    from core.wishlist.processing import process_wishlist_automatically
    from core.wishlist.state import get_wishlist_cycle

    db = _wishlist_singles
    batches: dict = {}
    process_wishlist_automatically(_auto_runtime(db, batches), automation_id='auto-1')

    assert batches == {}
    assert get_wishlist_cycle(lambda: db) == 'singles'      # toggled for next tick
    assert {r['retry_count'] for r in db.get_wishlist_tracks()} == {4}
