"""§69.1 reverse-direction sync: Watchlist→Library demonitor + Wishlist reconcile.

Self-contained: builds lib2 rows on the imported test DB and drives the mirror
through a fake DB that records the legacy wishlist add/remove calls (no legacy
tables required — the outbox itself is a lib2 table).
"""

from __future__ import annotations

import sqlite3

from core.library2.monitor_rules import (
    PROVENANCE_LEGACY,
    PROVENANCE_USER,
    record_rule,
)
from core.library2.monitor_sync import (
    demonitor_lib2_artists_for_removed_watchlist,
    reconcile_track_wishlist,
    sync_watchlist_removal,
)
from core.library2.wanted import recompute_wanted


class _FakeDB:
    """Records the legacy wishlist/watchlist ops the mirror drain issues."""

    def __init__(self, path: str):
        self.path = path
        self.added: list = []
        self.removed: list = []
        self.watchlist_removed: list = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def add_to_wishlist(self, payload, *, source_type="album", source_info=None,
                        user_initiated=False, profile_id=1, quality_profile_id=None,
                        raise_on_error=False):
        self.added.append(payload.get("id"))
        return True

    def remove_from_wishlist(self, track_id, profile_id, raise_on_error=False):
        self.removed.append(track_id)
        return True

    def remove_artist_from_watchlist(self, ext, profile_id, raise_on_error=False):
        self.watchlist_removed.append(ext)
        return True


def _add_artist(conn, name, *, monitored=1, spotify_id=None, external_ids=None):
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, monitored, spotify_id, external_ids) "
        "VALUES(?,?,?,COALESCE(?, '{}'))",
        (name, monitored, spotify_id, external_ids))
    return cur.lastrowid


def _add_track(conn, artist_id, title, *, monitored=1, with_file=False,
               spotify_id=None, provenance=PROVENANCE_LEGACY):
    cur = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, monitored) VALUES(?,?,?)",
        (artist_id, f"Alb-{title}", monitored))
    album_id = cur.lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                 (album_id, artist_id))
    cur = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, monitored, spotify_id) VALUES(?,?,?,?)",
        (album_id, title, monitored, spotify_id))
    track_id = cur.lastrowid
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                 (track_id, artist_id))
    if with_file:
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, format) VALUES(?,?, 'flac')",
            (track_id, f"/m/{track_id}.flac"))
    record_rule(conn, "album", album_id, bool(monitored), provenance)
    record_rule(conn, "track", track_id, bool(monitored), provenance)
    recompute_wanted(conn, track_ids=[track_id])
    return track_id


# --- Reconcile: monitored+missing track re-enters the wishlist ---------------


def test_reconcile_readds_monitored_missing_track(imported_conn, tmp_path):
    conn = imported_conn
    artist = _add_artist(conn, "SawanoHiroyuki[nZk]", spotify_id="art-sp")
    missing = _add_track(conn, artist, "Lost and Found", monitored=1,
                         with_file=False, spotify_id="miss-sp")
    satisfied = _add_track(conn, artist, "Owned", monitored=1,
                           with_file=True, spotify_id="sat-sp")
    conn.commit()

    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    stats = reconcile_track_wishlist(db, profile_id=1)

    assert "miss-sp" in db.added  # the monitored missing track was re-added
    assert "sat-sp" not in db.added  # a satisfied file is never queued
    assert stats["wanted"] >= 1
    assert missing and satisfied  # rows exist


def test_reconcile_prunes_unwanted_wishlist_entry(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Prune Artist", spotify_id="pa-sp")
    # An UNmonitored track that still has a stale library_v2 wishlist row.
    track = _add_track(conn, artist, "Stale", monitored=0, with_file=False,
                       spotify_id="stale-sp")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wishlist_tracks(
               id INTEGER PRIMARY KEY AUTOINCREMENT, spotify_track_id TEXT,
               source_type TEXT, source_info TEXT, profile_id INTEGER)""")
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, source_type, source_info, profile_id) "
        "VALUES('stale-sp', 'album', ?, 1)",
        (f'{{"source": "library_v2", "lib2_track_id": {track}}}',))
    conn.commit()

    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    reconcile_track_wishlist(db, profile_id=1)

    assert "stale-sp" in db.removed  # the no-longer-wanted entry was pruned


def test_reconcile_is_idempotent(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Idem", spotify_id="idem-sp")
    _add_track(conn, artist, "M", monitored=1, with_file=False, spotify_id="idem-t")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    first = reconcile_track_wishlist(db, profile_id=1)
    second = reconcile_track_wishlist(db, profile_id=1)
    assert first["wanted"] == second["wanted"]


# --- Reverse edge: watchlist removal demonitors the lib2 artist --------------


def test_demonitor_matches_by_spotify_id(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "VOJ", monitored=1, spotify_id="wl-sp")
    other = _add_artist(conn, "Untouched", monitored=1, spotify_id="other-sp")
    conn.commit()

    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    result = demonitor_lib2_artists_for_removed_watchlist(
        db, ["wl-sp"], "VOJ", profile_id=1)

    assert result["demonitored"] == 1
    assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (artist,)
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (other,)
                        ).fetchone()[0] == 1  # unrelated artist untouched
    rule = conn.execute(
        "SELECT monitored, provenance FROM lib2_monitor_rules "
        "WHERE entity_type='artist' AND entity_id=?", (artist,)).fetchone()
    assert dict(rule) == {"monitored": 0, "provenance": PROVENANCE_USER}


def test_demonitor_matches_by_name_fallback(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Justin Bieber", monitored=1, spotify_id="jb-real")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    # Provider id doesn't match any lib2 row → name fallback resolves it.
    result = demonitor_lib2_artists_for_removed_watchlist(
        db, ["no-such-id"], "justin bieber", profile_id=1)
    assert result["demonitored"] == 1
    assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (artist,)
                        ).fetchone()[0] == 0


def test_demonitor_idempotent(imported_conn):
    conn = imported_conn
    _add_artist(conn, "Once", monitored=1, spotify_id="once-sp")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    first = demonitor_lib2_artists_for_removed_watchlist(db, ["once-sp"], "Once", profile_id=1)
    second = demonitor_lib2_artists_for_removed_watchlist(db, ["once-sp"], "Once", profile_id=1)
    assert first["demonitored"] == 1
    assert second["demonitored"] == 0  # already unmonitored, nothing to do


def test_demonitor_no_match_is_noop(imported_conn):
    conn = imported_conn
    _add_artist(conn, "Present", monitored=1, spotify_id="present-sp")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    result = demonitor_lib2_artists_for_removed_watchlist(
        db, ["ghost-id"], "Ghost Artist", profile_id=1)
    assert result == {"matched": 0, "demonitored": 0}


def test_sync_watchlist_removal_feature_gated(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Gated", monitored=1, spotify_id="gate-sp")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    class _Cfg:
        def get(self, key, default=None):
            return False  # features.library_v2 off

    sync_watchlist_removal(db, _Cfg(), {"external_ids": ["gate-sp"], "name": "Gated"},
                           profile_id=1)
    # Feature off → artist stays monitored.
    assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (artist,)
                        ).fetchone()[0] == 1
