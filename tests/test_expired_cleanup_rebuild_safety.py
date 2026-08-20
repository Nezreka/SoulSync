"""Integration safety for the Expired Download Cleaner (Cremonies' thread).

Covers the two halves that live outside the pure decision core:

B1 — ``get_origin_cleanup_candidates`` must report play_count as UNKNOWN
(None) when it cannot match a download to a library track, and must match
across the two path namespaces (SoulSync's post-processed path vs whatever the
media server reported) rather than only on an exact string.

B2 — a library rebuild must stamp itself, and everything downloaded before
that stamp must be permanently out of scope. Otherwise the wipe destroys
play_count while library_history keeps created_at, and the next run deletes
tracks the user played hundreds of times.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.repair_jobs.expired_download_cleaner import ExpiredDownloadCleanerJob
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

OLD = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "cleanup.db"))


def _download(db, hid, file_path, created_at=OLD, origin="playlist"):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO library_history (id, event_type, origin, origin_context, "
        "file_path, title, artist_name, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (hid, "download", origin, "Discovery Weekly", file_path,
         f"Track {hid}", "Artist", created_at),
    )
    conn.commit()


def _track(db, tid, file_path, play_count, server_source="plex"):
    """A catalogue track the media server owns, with its active file.

    Addressed by the server's own id (``tid``) so a re-scan after a rebuild
    updates the same row instead of stacking a second one — which would make
    the rebuild test pass while the old play count was still there.
    """
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id="ar1", name="Artist",
                            server_source=server_source)
    album_id = seed_album(conn, server_id="al1", title="Album",
                          artist_id=artist_id, server_source=server_source)
    row = conn.execute(
        "SELECT id FROM lib2_tracks WHERE server_source=? AND server_id=?",
        (server_source, tid)).fetchone()
    if row:
        track_id = int(row[0])
        conn.execute("UPDATE lib2_track_files SET path=? WHERE track_id=?",
                     (file_path, track_id))
    else:
        track_id = seed_track(conn, server_id=tid, title=f"Track {tid}",
                              album_id=album_id, artist_id=artist_id,
                              server_source=server_source, file_path=file_path)
    conn.execute("UPDATE lib2_tracks SET play_count=? WHERE id=?",
                 (play_count, track_id))
    conn.commit()
    return track_id


# ── B1: play_count is unknown, not zero ───────────────────────────────────

def test_exact_path_match_still_reports_the_play_count(db):
    _download(db, 1, "/music/Artist/Album/01 Track.flac")
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 7)
    got = db.get_origin_cleanup_candidates()
    assert [c["play_count"] for c in got] == [7]


def test_no_matching_track_reports_unknown_not_zero(db):
    """An orphan history row must not read as 'never played' — that is the
    state that makes the cleaner delete things it cannot account for."""
    _download(db, 1, "/music/Artist/Album/01 Track.flac")
    got = db.get_origin_cleanup_candidates()
    assert got[0]["play_count"] is None


def test_play_count_survives_a_docker_style_path_difference(db):
    """SoulSync wrote /data/media/..., the media server reported /music/... —
    same file, different namespaces. Before the fix the exact join missed and
    play_count read 0, so the 'you played it' protection never fired at all on
    Docker and NAS installs."""
    _download(db, 1, "/data/media/Artist/Album/01 Track.flac")
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 12)
    got = db.get_origin_cleanup_candidates()
    assert got[0]["play_count"] == 12


def test_windows_and_posix_separators_match(db):
    _download(db, 1, r"H:\Music\Artist\Album\01 Track.flac")
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 4)
    assert db.get_origin_cleanup_candidates()[0]["play_count"] == 4


def test_an_ambiguous_suffix_takes_the_highest_play_count(db):
    """Two albums can hold the same filename. Ambiguity must fail toward
    keeping the file, so the most protective count wins."""
    _download(db, 1, "/data/Artist/Album/01 Track.flac")
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 0)
    _track(db, "t2", "/other/Artist/Album/01 Track.flac", 9)
    assert db.get_origin_cleanup_candidates()[0]["play_count"] == 9


def test_a_genuinely_unrelated_track_does_not_match(db):
    _download(db, 1, "/data/Artist/Album/01 Track.flac")
    _track(db, "t1", "/music/Someone/Else/99 Other.flac", 50)
    assert db.get_origin_cleanup_candidates()[0]["play_count"] is None


# ── B2: the rebuild stamp ─────────────────────────────────────────────────

def test_clear_server_data_stamps_the_rebuild(db):
    _track(db, "t1", "/music/a.flac", 3)
    assert db.get_preference("library_rebuilt_at") is None
    db.clear_server_data("plex")
    assert db.get_preference("library_rebuilt_at"), "rebuild was not recorded"


class _Ctx:
    """Minimal JobContext stand-in — the job only needs these."""

    def __init__(self, db):
        self.db = db
        self.config_manager = None
        self.update_progress = None
        self.create_finding = self._finding
        self.findings = []

    def check_stop(self):
        return False

    def _finding(self, **kw):
        self.findings.append(kw)
        return True


def _scan(db, settings=None):
    job = ExpiredDownloadCleanerJob()
    ctx = _Ctx(db)
    merged = {"watchlist_retention": "1w", "playlist_retention": "1w",
              "keep_if_played_at_least": 2, "dry_run": True}
    merged.update(settings or {})
    job._get_settings = lambda _c: merged
    job.scan(ctx)
    return ctx.findings


def test_an_expired_unplayed_download_is_still_proposed(db):
    """The feature still works — this is the case that SHOULD be found."""
    _download(db, 1, "/music/Artist/Album/01 Track.flac")
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 0)
    assert len(_scan(db)) == 1


def test_nothing_is_proposed_after_a_rebuild(db):
    """The disaster case, end to end. A much-played download, then a library
    refresh: the track row (and its play_count) is destroyed, history keeps
    the original created_at. Without the stamp this is deleted."""
    _download(db, 1, "/music/Artist/Album/01 Track.flac")
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 40)
    db.clear_server_data("plex")           # play_count gone, history intact
    _track(db, "t1", "/music/Artist/Album/01 Track.flac", 0)   # re-scanned
    assert _scan(db) == [], "a rebuilt library proposed deleting a played track"


def test_a_download_made_after_the_rebuild_is_still_in_scope(db):
    """The stamp must not become a blanket amnesty for everything forever.

    Library rebuilt 100 days ago, download made 30 days ago: it happened
    AFTER the rebuild, so its play_count is trustworthy and it is a normal
    candidate again."""
    db.set_preference(
        "library_rebuilt_at",
        (datetime.now(timezone.utc) - timedelta(days=100)).isoformat())
    fresh = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _download(db, 2, "/music/Artist/Album/02 New.flac", created_at=fresh)
    _track(db, "t2", "/music/Artist/Album/02 New.flac", 0)
    assert len(_scan(db)) == 1
