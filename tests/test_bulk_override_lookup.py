"""Bulk override lookup for the compare view (#1005).

Pass 0 resolved every source track's override with 2-3 FRESH SQLite
connections (plus an UPDATE+COMMIT per cache hit) — ~15s before a 1500-track
playlist could render. The bulk lookup does two chunked reads and must keep
per-id semantics IDENTICAL to resolve_override_server_id: validated cache hit
first, durable manual match (#787) second, file-path self-heal for stale rows.
"""

from __future__ import annotations

import pytest

from core.sync.match_overrides import (
    build_bulk_override_lookup,
    resolve_override_server_id,
)
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _cache(db, src_id, server_id, server_source="navidrome"):
    assert db.save_sync_match_cache(
        spotify_track_id=src_id, normalized_title="t", normalized_artist="a",
        server_source=server_source, server_track_id=server_id,
        server_track_title="T", confidence=1.0)


def _seed_track(db, tid, path):
    with db._get_connection() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (?, 1, 1, 'T', ?)",
            (tid, path))


def test_bulk_read_matches_per_row_and_bumps_use_count(db):
    _cache(db, "s1", "v1")
    _cache(db, "s2", "v2")

    def _count(sid):
        with db._get_connection() as conn:
            return conn.execute(
                "SELECT use_count FROM sync_match_cache WHERE spotify_track_id=?", (sid,)
            ).fetchone()[0]

    before = _count("s1")
    out = db.read_sync_match_cache_bulk(["s1", "s2", "s3"], "navidrome")
    assert out["s1"]["server_track_id"] == "v1" and out["s2"]["server_track_id"] == "v2"
    assert "s3" not in out
    # per-row semantics preserved: hits bump use_count by one
    assert _count("s1") == before + 1
    # wrong server → no rows
    assert db.read_sync_match_cache_bulk(["s1"], "plex") == {}


def test_bulk_durable_read_prefers_exact_server_source(db):
    db.save_manual_library_match(1, "spotify", "s1", "lib-generic", server_source="")
    db.save_manual_library_match(1, "mirrored", "s1", "lib-navidrome", server_source="navidrome")
    out = db.find_manual_library_matches_bulk(1, ["s1"], "navidrome")
    assert out["s1"]["library_track_id"] == "lib-navidrome"
    # only the ''-scoped row → still found
    out2 = db.find_manual_library_matches_bulk(1, ["s1"], "plex")
    assert out2["s1"]["library_track_id"] == "lib-generic"


def test_bulk_lookup_agrees_with_per_row_resolver(db):
    """The four cases, resolved by BOTH paths, must agree:
    valid cache hit · stale cache -> durable · durable stale -> file-path
    self-heal · no match at all."""
    valid = {"v1", "v2", "v3-new"}
    _cache(db, "hit", "v1")                                   # valid cache hit
    _cache(db, "stale-cache", "GONE")                          # stale cache…
    db.save_manual_library_match(1, "spotify", "stale-cache", "v2",
                                 server_source="navidrome")    # …durable saves it
    db.save_manual_library_match(1, "spotify", "heal", "v3-old",
                                 server_source="navidrome",
                                 library_file_path="/music/A/B/03.flac")
    _seed_track(db, "v3-new", "/music/A/B/03.flac")            # rescan re-keyed it

    sources = [{"source_track_id": s} for s in ("hit", "stale-cache", "heal", "nope")]
    bulk = build_bulk_override_lookup(db, 1, "navidrome", valid, sources)
    for sid in ("hit", "stale-cache", "heal", "nope"):
        per_row = resolve_override_server_id(db, 1, sid, "navidrome", valid,
                                             db.read_sync_match_cache)
        assert bulk(sid) == per_row, sid
    assert bulk("hit") == "v1"
    assert bulk("stale-cache") == "v2"
    assert bulk("heal") == "v3-new"
    assert bulk("nope") is None
    # the self-heal persisted — next lookup is a direct hit
    row = db.get_manual_library_match(1, "spotify", "heal", server_source="navidrome")
    assert row["library_track_id"] == "v3-new"


def test_stub_db_without_bulk_methods_falls_back_per_row():
    class _Stub:
        def read_sync_match_cache(self, sid, source):
            return {"server_track_id": "v9"} if sid == "s" else None
    lookup = build_bulk_override_lookup(_Stub(), 1, "navidrome", {"v9"}, [{"source_track_id": "s"}])
    assert lookup("s") == "v9"
    assert lookup("other") is None
