"""Curation signal storage + the cleaner's use of it (Cremonies).

The decision rules live in tests/library/test_curation_signals.py. This covers
the parts that touch the database and the job: storing one user's signals,
grouping them for the decision, and above all the STALE guard — a signal sweep
that quietly stopped working must never look like "nobody likes any of this".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.library.expired_cleanup import path_suffix_key
from core.repair_jobs.expired_download_cleaner import ExpiredDownloadCleanerJob
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track

OLD = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
TRACK = "/music/Artist/Album/01 Track.flac"
KEY = path_suffix_key(TRACK)


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "curation.db"))


def _download(db, hid=1, file_path=TRACK):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO library_history (id, event_type, origin, origin_context, "
        "file_path, title, artist_name, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (hid, "download", "playlist", "Discovery Weekly", file_path,
         "Track", "Artist", OLD))
    conn.commit()


def _track(db, tid="t1", file_path=TRACK, play_count=0):
    """The catalogue row behind the download, with its active file."""
    conn = db._get_connection()
    track_id = seed_library_track(
        conn, artist="Artist", album="Album", title="Track",
        track_server_id=tid, file_path=file_path)
    conn.execute("UPDATE lib2_tracks SET play_count=? WHERE id=?",
                 (play_count, track_id))
    conn.commit()
    return track_id


class _Ctx:
    def __init__(self, db):
        self.db = db
        self.config_manager = None
        self.update_progress = None
        self.findings = []
        self.create_finding = lambda **kw: (self.findings.append(kw) or True)

    def check_stop(self):
        return False


def _scan(db, **settings):
    job = ExpiredDownloadCleanerJob()
    ctx = _Ctx(db)
    merged = {"watchlist_retention": "1w", "playlist_retention": "1w",
              "keep_if_played_at_least": 2, "dry_run": True}
    merged.update(settings)
    job._get_settings = lambda _c: merged
    job.scan(ctx)
    return ctx.findings


# ── storage ───────────────────────────────────────────────────────────────

def test_signals_round_trip(db):
    db.replace_curation_signals("navidrome", "alice", [
        {"track_key": KEY, "favorite": True, "rating": 5, "source_path": TRACK},
    ])
    grouped = db.get_curation_signals_by_track_key()
    assert grouped[KEY][0]["favorite"] is True
    assert grouped[KEY][0]["user"] == "alice"


def test_replacing_a_users_signals_drops_the_old_ones(db):
    """Unstarring must actually remove protection — a merge would keep a
    track protected forever after one accidental star."""
    db.replace_curation_signals("navidrome", "alice", [{"track_key": KEY, "favorite": True}])
    db.replace_curation_signals("navidrome", "alice", [])
    assert db.get_curation_signals_by_track_key() == {}


def test_one_users_replace_does_not_touch_another(db):
    db.replace_curation_signals("navidrome", "alice", [{"track_key": KEY, "favorite": True}])
    db.replace_curation_signals("navidrome", "bob", [])
    assert len(db.get_curation_signals_by_track_key()[KEY]) == 1


def test_signals_from_two_servers_coexist(db):
    db.replace_curation_signals("navidrome", "alice", [{"track_key": KEY, "favorite": True}])
    db.replace_curation_signals("jellyfin", "alice", [{"track_key": KEY, "rating": 4}])
    assert len(db.get_curation_signals_by_track_key()[KEY]) == 2


def test_rows_without_a_track_key_are_ignored(db):
    stored = db.replace_curation_signals("navidrome", "alice", [
        {"favorite": True}, {"track_key": "", "favorite": True}, {"track_key": KEY},
    ])
    assert stored == 1


# ── the cleaner consulting them ───────────────────────────────────────────

def test_a_favorited_track_is_not_proposed(db):
    _download(db)
    _track(db)
    db.replace_curation_signals("navidrome", "alice", [{"track_key": KEY, "favorite": True}])
    db.mark_curation_sync()
    assert _scan(db) == []


def test_an_uncurated_track_is_still_proposed(db):
    _download(db)
    _track(db)
    db.replace_curation_signals("navidrome", "alice", [])
    db.mark_curation_sync()
    assert len(_scan(db)) == 1


def test_a_low_rating_does_not_protect(db):
    _download(db)
    _track(db)
    db.replace_curation_signals("navidrome", "alice", [{"track_key": KEY, "rating": 2}])
    db.mark_curation_sync()
    assert len(_scan(db)) == 1


def test_a_signal_matches_across_a_docker_style_path_difference(db):
    """SoulSync recorded /data/media/..., the server reports /music/... — the
    signal must still land on the same track."""
    _download(db, file_path="/data/media/Artist/Album/01 Track.flac")
    _track(db, file_path=TRACK)
    db.replace_curation_signals("navidrome", "alice", [
        {"track_key": path_suffix_key(TRACK), "favorite": True}])
    db.mark_curation_sync()
    assert _scan(db) == []


# ── the stale guard ───────────────────────────────────────────────────────

def test_never_synced_keeps_everything(db):
    """L2-001: curation is ON (the default) and no sweep has ever completed, so
    we do not know what anyone favourited. That is not the same as "nobody
    favourited anything", and this job deletes files.

    The sweep is scheduled by the same settings that enable this job, so the
    state resolves itself on the next media-server poll. An install with no
    media server turns ``use_curation_signals`` off — see the test below — and
    gets the pre-feature behaviour back immediately."""
    _download(db)
    _track(db)
    assert _scan(db) == []


def test_a_sweep_that_found_no_capable_server_is_not_a_blocker(db):
    """The other half of L2-001's "pending" rule. Once the sweep has actually
    run and reported that no configured server can produce curation signals,
    there is nothing for the feature to protect and the job must work normally
    — otherwise it would be permanently disabled on those installs."""
    _download(db)
    _track(db)
    db.mark_curation_sync({'complete': True, 'expected_servers': [],
                           'servers': [], 'failed': []})
    assert len(_scan(db)) == 1


def test_an_incomplete_sweep_keeps_everything(db):
    """A sweep where one server or one user failed produced a snapshot that
    says those people like nothing. Stamping it fresh anyway is what deleted
    favourited files."""
    _download(db)
    _track(db)
    db.mark_curation_sync({'complete': False, 'expected_servers': ['plex'],
                           'servers': [], 'failed': ['plex/bob']})
    assert _scan(db) == []


def test_a_corrupt_status_record_keeps_everything(db):
    _download(db)
    _track(db)
    db.set_preference(db.CURATION_STATUS_KEY, 'not json')
    assert _scan(db) == []


def test_unreadable_signals_keep_everything(db, monkeypatch):
    """The read used to swallow its error and return {}, which the cleaner read
    as "nobody curated anything"."""
    _download(db)
    _track(db)
    db.mark_curation_sync({'complete': True, 'expected_servers': ['plex'],
                           'servers': ['plex'], 'failed': []})
    monkeypatch.setattr(
        type(db), 'get_curation_signals_by_track_key',
        lambda self: (_ for _ in ()).throw(RuntimeError("no such table")))
    assert _scan(db) == []


def test_a_stale_sweep_keeps_everything(db):
    """A sweep that WAS working and then broke — server down, credentials
    rotated, worker crashed — must not read as 'nobody likes anything'. This
    is the case that genuinely warrants blocking every deletion."""
    _download(db)
    _track(db)
    db.set_preference(
        db.CURATION_SYNC_KEY,
        (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat())
    assert _scan(db, curation_max_age_hours=48) == []


def test_a_fresh_complete_sweep_allows_deletion(db):
    _download(db)
    _track(db)
    db.mark_curation_sync({'complete': True, 'expected_servers': ['plex'],
                           'servers': ['plex'], 'failed': []})
    assert len(_scan(db)) == 1


def test_a_bare_legacy_stamp_still_allows_deletion(db):
    """An adapter/caller that only writes the timestamp keeps working: a stamp
    with no structured record is still a completed sweep."""
    _download(db)
    _track(db)
    db.mark_curation_sync()
    assert len(_scan(db)) == 1


def test_turning_curation_off_restores_the_old_behaviour(db):
    """With the feature disabled the job must behave exactly as it did
    before it existed — including not being blocked by a missing sweep."""
    _download(db)
    _track(db)
    assert len(_scan(db, use_curation_signals=False)) == 1
