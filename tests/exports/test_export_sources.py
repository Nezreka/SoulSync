"""Export source wiring (#903): waterfall order + cache write-back.

build_resolve_fn assembles cache -> DB -> file -> MusicBrainz and writes a fresh
(non-cache) hit back to the cache. Pins: a cache hit short-circuits everything and is
NOT re-written; a DB/MB hit IS written back; misses fall through; the resolving label is
returned.
"""

from __future__ import annotations

from core.exports.export_sources import build_resolve_fn
from core.exports.mbid_resolver import SRC_CACHE, SRC_DB, SRC_MUSICBRAINZ

MBID = "e8f9b188-f819-4e43-ab0f-4bd26ce9ff56"


def _wire(db=None, file=None, mb=None, cache=None):
    recorded = {}
    store = dict(cache or {})
    fn = build_resolve_fn(
        db_fn=lambda a, t: (db or {}).get((a, t)),
        file_fn=lambda a, t: (file or {}).get((a, t)),
        mb_fn=lambda a, t: (mb or {}).get((a, t)),
        cache_lookup=lambda k: store.get(k),
        cache_record=lambda k, m: recorded.__setitem__(k, m) or True,
    )
    return fn, recorded


def test_cache_hit_short_circuits_and_is_not_rewritten():
    from core.exports.mbid_resolver import normalize_key
    fn, recorded = _wire(
        cache={normalize_key("A", "T"): MBID},
        db={("A", "T"): "should-not-reach"},
    )
    mbid, label = fn("A", "T")
    assert (mbid, label) == (MBID, SRC_CACHE)
    assert recorded == {}                       # cache hit -> no write-back


def test_db_hit_is_written_back_to_cache():
    from core.exports.mbid_resolver import normalize_key
    fn, recorded = _wire(db={("A", "T"): MBID})
    mbid, label = fn("A", "T")
    assert (mbid, label) == (MBID, SRC_DB)
    assert recorded == {normalize_key("A", "T"): MBID}   # fresh hit cached for next time


def test_falls_through_to_musicbrainz_and_caches():
    fn, recorded = _wire(db={}, file={}, mb={("A", "T"): MBID})
    mbid, label = fn("A", "T")
    assert (mbid, label) == (MBID, SRC_MUSICBRAINZ)
    assert list(recorded.values()) == [MBID]


def test_all_miss_returns_none_and_no_write():
    fn, recorded = _wire()
    assert fn("A", "T") == (None, None)
    assert recorded == {}


# ── service track-id resolver (#945 export to Spotify/Deezer) ──

from core.exports.export_sources import (
    db_service_track_id,
    build_service_resolve_fn,
    _SERVICE_ID_COLUMNS,
)


def test_service_id_column_mapping():
    assert _SERVICE_ID_COLUMNS == {'spotify': 'spotify_track_id', 'deezer': 'deezer_id'}


def test_db_service_track_id_unknown_service_is_none():
    assert db_service_track_id('A', 'X', 'tidal') is None
    assert db_service_track_id('A', 'X', '') is None


def test_db_service_track_id_no_title_is_none():
    assert db_service_track_id('A', '', 'spotify') is None


def test_build_service_resolve_fn_returns_id_and_source(monkeypatch):
    import core.exports.export_sources as es
    monkeypatch.setattr(es, 'db_service_track_id',
                        lambda a, t, s: 'spid-99' if t == 'Hit' else None)
    fn = build_service_resolve_fn('spotify')
    assert fn('Artist', 'Hit') == ('spid-99', 'library')
    assert fn('Artist', 'Miss') == (None, None)


def test_db_service_track_id_real_sql_executes(tmp_path, monkeypatch):
    """Run the ACTUAL query against a real (temp) tracks/artists schema — the broad
    except→None in db_service_track_id would otherwise mask a column/join typo as
    'no match' for every track (#945 verification)."""
    import sqlite3
    import types
    import core.exports.export_sources as es

    dbfile = tmp_path / "lib.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        "CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE tracks (id TEXT, artist_id TEXT, title TEXT, "
        "spotify_track_id TEXT, deezer_id TEXT);"
        "INSERT INTO artists VALUES ('a1','Kendrick Lamar');"
        "INSERT INTO tracks VALUES ('t1','a1','Not Like Us','spid-NLU','dz-NLU');"
    )
    con.commit()
    con.close()

    # fresh connection per call (db_service_track_id closes it in finally)
    fake_db = types.SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(dbfile)))
    monkeypatch.setattr("database.music_database.get_database", lambda: fake_db)

    assert es.db_service_track_id("Kendrick Lamar", "Not Like Us", "spotify") == "spid-NLU"
    assert es.db_service_track_id("kendrick lamar", "not like us", "deezer") == "dz-NLU"  # case-insensitive
    assert es.db_service_track_id("Kendrick Lamar", "Unknown Song", "spotify") is None
