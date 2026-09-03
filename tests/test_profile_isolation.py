"""Profile-isolation audit fixes (the deferred profile-system bug list).

1. delete_profile sweeps EVERY table with a profile_id column (schema-derived)
   — the old hardcoded 8-table list orphaned per-profile service credentials,
   notification history, issues, blocklists and more, and silently fell
   further behind with every new profile feature.
2. The video DB gets the same schema-derived sweep for its profile-keyed rows
   (requests, issues), called when a profile is deleted.
3. The mirrored-playlist by-id HTTP routes carry an owner-or-admin gate
   (mirrored_playlist_visible) — the IDOR where any profile could read,
   rename, re-point, pipeline-run, or DELETE another's playlists by id.
4. The socket room joins derive the profile from the SESSION, and playlist-
   keyed push rooms (sync:/discovery:) apply the same owner-or-admin rule.

Routes/sockets are pinned at the source level (importing web_server boots the
app); the DB sweeps run for real on tmp databases.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from database.music_database import MusicDatabase
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_WS = (_ROOT / "web_server.py").read_text(encoding="utf-8")
# the mirrored playlist routes moved to api/mirrored_playlists.py (aug 26 lift)
_MPL = (_ROOT / "api" / "mirrored_playlists.py").read_text(encoding="utf-8")


# ── 1. music delete_profile: schema-derived sweep ────────────────────────────

@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mk_profile(db, name):
    conn = db._get_connection()
    try:
        cur = conn.execute("INSERT INTO profiles (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_delete_profile_sweeps_every_profile_table(mdb):
    pid = _mk_profile(mdb, "kid")
    conn = mdb._get_connection()
    try:
        # rows in tables the OLD hardcoded list never cleaned
        conn.execute("INSERT INTO notification_history (profile_id, type, message) "
                     "VALUES (?, 'info', 'hi')", (pid,))
        conn.execute("INSERT INTO profile_service_credentials (profile_id, service, credential_id) "
                     "VALUES (?, 'spotify', 7)", (pid,))
        conn.execute("INSERT INTO wishlist_ignore (profile_id, track_id) "
                     "VALUES (?, 'trk1')", (pid,))
        # a legacy-list table still works
        conn.execute("INSERT INTO watchlist_artists (profile_id, spotify_artist_id, artist_name) "
                     "VALUES (?, 'a1', 'A')", (pid,))
        # admin's rows and NULL-profile (global) rows must survive
        conn.execute("INSERT INTO notification_history (profile_id, type, message) "
                     "VALUES (1, 'info', 'admin row')")
        conn.commit()
    finally:
        conn.close()

    assert mdb.delete_profile(pid) is True

    conn = mdb._get_connection()
    try:
        for table in ("notification_history", "profile_service_credentials",
                      "wishlist_ignore", "watchlist_artists"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?",
                             (pid,)).fetchone()[0]
            assert n == 0, f"{table} still has rows for the deleted profile"
        assert conn.execute("SELECT COUNT(*) FROM notification_history "
                            "WHERE profile_id = 1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM profiles WHERE id = ?",
                            (pid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_delete_profile_never_touches_admin(mdb):
    assert mdb.delete_profile(1) is False


def test_delete_profile_cascades_mirrored_playlist_tracks(mdb):
    """The sweep deletes the profile's mirrored_playlists rows; their child
    tracks (keyed by playlist_id, no profile_id) must go with them via the
    FK cascade — proven here, not assumed (the pragma is per-connection)."""
    pid = _mk_profile(mdb, "kid2")
    conn = mdb._get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) "
            "VALUES ('spotify', 'sp1', 'Kid Jams', ?)", (pid,))
        plid = cur.lastrowid
        conn.execute(
            "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name) "
            "VALUES (?, 1, 'Song', 'Artist')", (plid,))
        conn.commit()
    finally:
        conn.close()

    assert mdb.delete_profile(pid) is True

    conn = mdb._get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM mirrored_playlists WHERE id = ?",
                            (plid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mirrored_playlist_tracks WHERE playlist_id = ?",
                            (plid,)).fetchone()[0] == 0, \
            "cascade did not fire — orphaned playlist tracks"
    finally:
        conn.close()


# ── 2. video sweep ───────────────────────────────────────────────────────────

def test_video_profile_sweep(tmp_path):
    vdb = VideoDatabase(database_path=str(tmp_path / "video.db"))
    conn = vdb._get_connection()
    try:
        conn.execute("INSERT INTO video_requests (profile_id, kind, tmdb_id, title) "
                     "VALUES (2, 'movie', 1, 'Heat')")
        conn.execute("INSERT INTO video_requests (profile_id, kind, tmdb_id, title) "
                     "VALUES (1, 'movie', 2, 'Ronin')")
        conn.commit()
    finally:
        conn.close()
    removed = vdb.delete_profile_data(2)
    assert removed >= 1
    conn = vdb._get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM video_requests "
                            "WHERE profile_id = 2").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM video_requests "
                            "WHERE profile_id = 1").fetchone()[0] == 1
    finally:
        conn.close()
    assert vdb.delete_profile_data(1) == 0        # admin never swept


# ── 3. HTTP IDOR gate: every by-id mirrored route checks visibility ──────────

_MIRROR_GATE_MARKERS = (
    "mirrored_playlist_visible(",
    # quality-profiles-foundation's equivalent gate: a SQL-scoped fetch/write
    # (profile_id baked into the query) instead of fetch-then-check, with the
    # same admin-bypass semantics as mirrored_playlist_visible.
    "_owned_mirrored_playlist(",
    "_mirror_scope_profile_id(",
)


def _by_id_mirrored_routes():
    return re.findall(
        r"@bp\.route\('/api/mirrored-playlists/<int:playlist_id>[^']*'[^)]*\)\s*\n"
        r"def (\w+)\(playlist_id\):((?:\n(?!@bp\.route).*)*)", _MPL)


def test_every_by_id_mirrored_route_is_gated():
    """Each /api/mirrored-playlists/<int:playlist_id>... route body must call
    an ownership-gate helper before acting. Counted per route so a NEW
    ungated route fails this test the day it's added."""
    routes = _by_id_mirrored_routes()
    assert len(routes) >= 10, f"expected the by-id route family, found {len(routes)}"
    ungated = [name for name, body in routes
               if not any(marker in body for marker in _MIRROR_GATE_MARKERS)]
    assert ungated == [], f"by-id routes missing the ownership gate: {ungated}"


def test_gate_answers_404_not_403():
    """Foreign ids must be unprobeable — same response as nonexistent. Checked
    per gated route (rather than a flat whole-file substring count) so it
    holds regardless of which gate idiom the route uses."""
    routes = _by_id_mirrored_routes()
    assert len(routes) >= 10
    no_404 = [name for name, body in routes
              if any(marker in body for marker in _MIRROR_GATE_MARKERS)
              and '"Playlist not found"' not in body]
    assert no_404 == [], f"gated routes without a 404 response: {no_404}"


# ── 4. socket rooms: session-derived profile + gated playlist rooms ──────────

def test_profile_room_join_is_session_derived():
    m = re.search(r"def handle_profile_join\(data\):((?:\n(?!@socketio).*)*)", _WS)
    assert m, "profile:join handler missing"
    body = m.group(1)
    assert "_ws_session_profile()" in body
    # the payload id may only be honoured for admins
    assert "is_admin and requested" in body


def test_playlist_rooms_are_ownership_gated():
    for handler in ("handle_sync_subscribe", "handle_discovery_subscribe"):
        m = re.search(rf"def {handler}\(data\):((?:\n(?!@socketio).*)*)", _WS)
        assert m, handler
        assert "_playlist_room_allowed(" in m.group(1), handler


def test_room_gate_passes_unowned_key_shapes():
    """The gate must NOT break non-mirrored discovery keys (YouTube hashes,
    Beatport charts, 'mirrored_<n>' composites map to ownership; arbitrary
    strings pass) — pinned at source: the early-return for non-numeric ids."""
    m = re.search(r"def _playlist_room_allowed\([^)]*\)[^:\n]*:((?:\n(?!def |@).*)*)", _WS)
    assert m
    body = m.group(1)
    assert "isdigit()" in body and "mirrored_" in body
    assert "return True" in body
