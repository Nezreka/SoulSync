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
    demonitor_lib2_tracks_for_removed_wishlist,
    reconcile_artist_watchlist,
    reconcile_track_wishlist,
    sync_watchlist_removal,
    sync_wishlist_removal,
)
from core.library2.wanted import recompute_wanted


class _FakeDB:
    """Records the legacy wishlist/watchlist ops the mirror drain issues."""

    def __init__(self, path: str):
        self.path = path
        self.added: list = []
        self.removed: list = []
        self.watchlist_removed: list = []
        self.watchlist_added: list = []

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

    def add_artist_to_watchlist(self, ext, name, profile_id, source=None,
                                raise_on_error=False):
        self.watchlist_added.append((ext, name, profile_id, source))
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


def test_demonitor_name_fallback_is_noop_when_ambiguous(imported_conn):
    """A9: two lib2 artists sharing the removed Watchlist row's name (a
    genuine same-name collision, or an unmerged duplicate) must not both get
    demonitored — the name fallback is only safe when it resolves uniquely."""
    conn = imported_conn
    first = _add_artist(conn, "Same Name", monitored=1, spotify_id="dup-sp-1")
    second = _add_artist(conn, "Same Name", monitored=1, spotify_id="dup-sp-2")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    # Provider id doesn't match either row → falls to the ambiguous name.
    result = demonitor_lib2_artists_for_removed_watchlist(
        db, ["no-such-id"], "same name", profile_id=1)
    assert result == {"matched": 0, "demonitored": 0}
    assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (first,)
                        ).fetchone()[0] == 1
    assert conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (second,)
                        ).fetchone()[0] == 1


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


def test_watchlist_removal_supersedes_an_older_pending_add(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Pending", monitored=1, spotify_id="pending-sp")
    record_rule(conn, "artist", artist, True, PROVENANCE_USER)
    from core.library2.mirror_outbox import enqueue_artist_watchlist
    enqueue_artist_watchlist(conn, artist, True, profile_id=1)
    conn.commit()

    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])
    demonitor_lib2_artists_for_removed_watchlist(
        db, ["pending-sp"], "Pending", profile_id=1,
    )

    # The stale add is replayed first, then the newer explicit remove wins.
    assert db.watchlist_added[0][0] == "pending-sp"
    assert db.watchlist_removed[-1] == "pending-sp"
    assert conn.execute(
        "SELECT monitored FROM lib2_artists WHERE id=?", (artist,)
    ).fetchone()[0] == 0


# --- Reverse edge: wishlist removal demonitors the exact lib2 track ----------


def test_wishlist_removal_demonitors_by_embedded_lib2_id(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Track Artist", monitored=0, spotify_id="ta-sp")
    track = _add_track(
        conn, artist, "Remove Me", monitored=1, spotify_id="remove-sp",
        provenance=PROVENANCE_USER,
    )
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    result = demonitor_lib2_tracks_for_removed_wishlist(db, [{
        "spotify_track_id": "remove-sp",
        "source_info": {"source": "library_v2", "lib2_track_id": track},
    }])

    assert result["matched"] == 1
    assert result["demonitored"] == 1
    assert db.removed[-1] == "remove-sp"
    row = conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (track,),
    ).fetchone()
    assert row[0] == 0
    rule = conn.execute(
        "SELECT monitored, provenance FROM lib2_monitor_rules "
        "WHERE entity_type='track' AND entity_id=?", (track,),
    ).fetchone()
    assert dict(rule) == {"monitored": 0, "provenance": PROVENANCE_USER}


def test_wishlist_removal_matches_provider_payload_without_lib2_context(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Provider Artist", monitored=0)
    track = _add_track(conn, artist, "Provider Track", monitored=1,
                       spotify_id="provider-track")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    result = demonitor_lib2_tracks_for_removed_wishlist(db, [{
        "spotify_track_id": "provider-track::provider-album",
        "spotify_data": {
            "id": "provider-track",
            "provider": "spotify",
        },
    }])

    assert result["matched"] == 1
    assert conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (track,),
    ).fetchone()[0] == 0


def test_sync_wishlist_removal_is_admin_only(imported_conn):
    conn = imported_conn
    artist = _add_artist(conn, "Other Profile", monitored=0)
    track = _add_track(conn, artist, "Private", monitored=1, spotify_id="private-sp")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    class _Cfg:
        def get(self, key, default=None):
            return True

    sync_wishlist_removal(
        db,
        _Cfg(),
        [{"spotify_track_id": "private-sp"}],
        profile_id=2,
    )
    assert conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (track,),
    ).fetchone()[0] == 1


# --- Repair: explicit artist intent wins; schema-default drift does not ------


def _ensure_watchlist_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watchlist_artists(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               spotify_artist_id TEXT,
               musicbrainz_artist_id TEXT,
               artist_name TEXT NOT NULL,
               profile_id INTEGER NOT NULL DEFAULT 1)"""
    )


def test_artist_reconcile_readds_explicit_monitored_artist(imported_conn):
    conn = imported_conn
    _ensure_watchlist_table(conn)
    artist = _add_artist(conn, "Definite", monitored=1, spotify_id="def-sp")
    record_rule(conn, "artist", artist, True, PROVENANCE_USER)
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    stats = reconcile_artist_watchlist(db, profile_id=1)

    assert stats["watchlist_mirrors"] == 1
    assert db.watchlist_added[-1][0] == "def-sp"
    assert conn.execute(
        "SELECT monitored FROM lib2_artists WHERE id=?", (artist,),
    ).fetchone()[0] == 1


def test_artist_reconcile_clears_nonexplicit_default_drift(imported_conn):
    conn = imported_conn
    _ensure_watchlist_table(conn)
    artist = _add_artist(conn, "Phantom Default", monitored=1, spotify_id="phantom-sp")
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    stats = reconcile_artist_watchlist(db, profile_id=1)

    assert stats["monitor_flags_changed"] >= 1
    assert db.watchlist_added == []
    assert conn.execute(
        "SELECT monitored FROM lib2_artists WHERE id=?", (artist,),
    ).fetchone()[0] == 0


def test_artist_reconcile_name_match_tolerates_double_spaces(imported_conn):
    """A12: the name-fallback match must use core.library2.importer
    .normalize_name (collapses internal whitespace), not an ad-hoc
    strip().casefold() — the same "Odetari w" bug class at a new spot. A
    lib2 artist name with a stray double space must still match its
    single-spaced Watchlist row instead of being wrongly demonitored."""
    conn = imported_conn
    _ensure_watchlist_table(conn)
    artist = _add_artist(conn, "Foo  Bar", monitored=1, spotify_id=None)
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, profile_id) VALUES(?, 1)",
        ("Foo Bar",),
    )
    conn.commit()
    db = _FakeDB(conn.execute("PRAGMA database_list").fetchone()[2])

    stats = reconcile_artist_watchlist(db, profile_id=1)

    assert stats["monitor_flags_changed"] == 0
    assert conn.execute(
        "SELECT monitored FROM lib2_artists WHERE id=?", (artist,),
    ).fetchone()[0] == 1
