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


def _wire(db=None, file=None, mb=None, cache=None, flagged=None, track_lookup=None):
    """Wire a resolve_fn from fakes. ``flagged``/``track_lookup`` default to "nothing is
    flagged, no track identity" so the existing waterfall-order tests below don't have to
    know about the repair-finding gate at all — only the gate-specific tests pass
    non-default fakes for them.

    ``flagged`` is ``(track_id, file_path) -> bool`` (matching the real
    ``mbid_flagged_fn`` contract) — ``track_lookup`` is what supplies those ids, keeping
    the DB/file rung fakes (plain artist/title -> mbid dicts) decoupled from track
    identity."""
    recorded = {}
    store = dict(cache or {})
    fn = build_resolve_fn(
        db_fn=lambda a, t: (db or {}).get((a, t)),
        file_fn=lambda a, t: (file or {}).get((a, t)),
        mb_fn=lambda a, t: (mb or {}).get((a, t)),
        cache_lookup=lambda k: store.get(k),
        cache_record=lambda k, m: recorded.__setitem__(k, m) or True,
        mbid_flagged_fn=flagged if flagged is not None else (lambda tid, fp: False),
        track_lookup_fn=track_lookup if track_lookup is not None else (lambda a, t: (None, None)),
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


# ── DB/file rungs are gated by the mbid_mismatch repair job's pending findings — a
# flagged MBID makes the rung a miss so the waterfall falls through instead of exporting
# the wrong recording ──

OTHER_MBID = "11111111-2222-3333-4444-555555555555"


def test_db_hit_flagged_by_pending_finding_falls_through_to_musicbrainz():
    """The repair job already has a pending mbid_mismatch finding for this track -> the
    DB rung's MBID is a miss, so the waterfall goes on to the file rung. Since the file
    rung resolves the SAME (still-flagged) track, that's a miss too -> falls all the way
    to the live MusicBrainz rung, proving both later rungs were actually consulted."""
    fn, _ = _wire(
        db={("A", "T"): MBID},
        file={("A", "T"): OTHER_MBID},
        mb={("A", "T"): "99999999-8888-7777-6666-555555555555"},
        flagged=lambda tid, fp: True,
    )
    assert fn("A", "T") == ("99999999-8888-7777-6666-555555555555", SRC_MUSICBRAINZ)


def test_file_hit_flagged_by_pending_finding_falls_through_to_musicbrainz():
    """DB rung misses outright; the file rung's MBID is flagged -> falls through to the
    live MusicBrainz rung."""
    fn, _ = _wire(
        db={},
        file={("A", "T"): MBID},
        mb={("A", "T"): OTHER_MBID},
        flagged=lambda tid, fp: True,
    )
    assert fn("A", "T") == (OTHER_MBID, SRC_MUSICBRAINZ)


def test_flagged_predicate_is_per_track_not_global():
    """The flagged predicate receives (track_id, file_path) resolved by track_lookup_fn
    — here keyed by title, so only the flagged track is rejected."""
    fn, _ = _wire(
        db={("A", "T"): MBID, ("A", "Other"): MBID},
        track_lookup=lambda a, t: (f"tid-{t}", None),
        flagged=lambda tid, fp: tid == "tid-T",
    )
    assert fn("A", "T") == (None, None)
    assert fn("A", "Other") == (MBID, SRC_DB)


def test_flagged_fn_called_once_across_db_and_file_rung_for_same_track():
    """Both the DB and file rungs resolve to (different) MBIDs for the same track, and
    both get rejected by the flagged predicate -> the per-run memo means BOTH the track
    lookup and the flagged predicate are only actually invoked once for ('A', 'T'), not
    once per rung ."""
    lookup_calls = []
    flagged_calls = []

    def track_lookup(a, t):
        lookup_calls.append((a, t))
        return ("tid-1", "/music/t.mp3")

    def flagged(tid, fp):
        flagged_calls.append((tid, fp))
        return True

    fn, _ = _wire(db={("A", "T"): MBID}, file={("A", "T"): OTHER_MBID},
                  track_lookup=track_lookup, flagged=flagged)
    assert fn("A", "T") == (None, None)
    assert lookup_calls == [("A", "T")]
    assert flagged_calls == [("tid-1", "/music/t.mp3")]


def test_mbid_flagged_by_repair_finding_real_sql(tmp_path, monkeypatch):
    """Run the ACTUAL query against a real (temp) repair_findings schema — a pending
    mbid_mismatch finding keyed on the track's id (or file path) must flag it; a resolved
    one, a finding for a different track, or no id/path at all, must not. Signature is
    (track_id, file_path) — the caller (build_resolve_fn) resolves those once and passes
    them in."""
    import sqlite3
    import types
    import core.exports.export_sources as es

    dbfile = tmp_path / "lib.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        "CREATE TABLE repair_findings (finding_type TEXT, status TEXT, "
        "entity_type TEXT, entity_id TEXT, file_path TEXT);"
        "INSERT INTO repair_findings VALUES "
        "('mbid_mismatch','pending','track','t1','/music/track1.mp3'),"
        "('mbid_mismatch','resolved','track','t2','/music/track2.mp3');"
    )
    con.commit()
    con.close()

    fake_db = types.SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(dbfile)))
    monkeypatch.setattr("database.music_database.get_database", lambda: fake_db)

    assert es.mbid_flagged_by_repair_finding("t1", "/music/track1.mp3") is True
    assert es.mbid_flagged_by_repair_finding("t1", None) is True          # matches by id alone
    assert es.mbid_flagged_by_repair_finding(None, "/music/track1.mp3") is True  # matches by path alone
    # resolved (not pending) finding -> not flagged
    assert es.mbid_flagged_by_repair_finding("t2", "/music/track2.mp3") is False
    # no matching track at all -> not flagged
    assert es.mbid_flagged_by_repair_finding("t3", "/music/unknown.mp3") is False
    assert es.mbid_flagged_by_repair_finding(None, None) is False


def test_default_track_lookup_real_sql(tmp_path, monkeypatch):
    """_default_track_lookup (the default track_lookup_fn) runs the real _db_match text
    match and hands back (track_id, file_path)."""
    import sqlite3
    import types
    import core.exports.export_sources as es

    dbfile = tmp_path / "lib.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        "CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE tracks (id TEXT, artist_id TEXT, title TEXT, "
        "musicbrainz_recording_id TEXT, file_path TEXT);"
        "INSERT INTO artists VALUES ('a1','Fall Out Boy');"
        "INSERT INTO tracks VALUES ('t1','a1','Thnks fr th Mmrs','bad-mbid','/music/t1.mp3');"
    )
    con.commit()
    con.close()

    fake_db = types.SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(dbfile)))
    monkeypatch.setattr("database.music_database.get_database", lambda: fake_db)

    assert es._default_track_lookup("Fall Out Boy", "Thnks fr th Mmrs") == ("t1", "/music/t1.mp3")
    assert es._default_track_lookup("Fall Out Boy", "Unknown Song") == (None, None)


# ── build_resolve_fn() with no kwargs wires the REAL default gate ──

def test_build_resolve_fn_no_kwargs_wires_real_default_gates(monkeypatch):
    import core.exports.export_sources as es

    monkeypatch.setattr(es, "_db_match", lambda a, t: (MBID, "/music/track.mp3", "tid-1"))

    flagged_calls = []

    def fake_flagged(track_id, file_path):
        flagged_calls.append((track_id, file_path))
        return False

    monkeypatch.setattr(es, "mbid_flagged_by_repair_finding", fake_flagged)

    fn = es.build_resolve_fn()   # no kwargs at all -> real db_fn/file_fn/mb_fn AND real gate
    assert fn("A", "T") == (MBID, SRC_DB)
    assert flagged_calls == [("tid-1", "/music/track.mp3")]


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


# ── discovery-cache resolution (#945: use the already-discovered IDs, no API call) ──

import json as _json
from core.exports.export_sources import (
    service_id_from_extra_data,
    resolve_service_track_ids,
)


def _extra(service, tid, discovered=True, provider=None):
    return {'extra_data': _json.dumps({'discovered': discovered,
                                       'provider': provider or service,
                                       'matched_data': {'id': tid}})}


def test_extra_data_id_when_discovered_to_that_service():
    assert service_id_from_extra_data(_extra('deezer', 111), 'deezer') == '111'
    # dict (not str) extra_data also works
    raw = {'extra_data': {'discovered': True, 'provider': 'spotify', 'matched_data': {'id': 'spX'}}}
    assert service_id_from_extra_data(raw, 'spotify') == 'spX'


def test_extra_data_provider_must_match_service():
    # discovered to Spotify, exporting to Deezer → don't reuse the (wrong-service) id
    assert service_id_from_extra_data(_extra('spotify', 111), 'deezer') is None


def test_extra_data_wing_it_fallback_is_not_trusted():
    track = _extra('deezer', 111, provider='wing_it_fallback')
    assert service_id_from_extra_data(track, 'deezer') is None


def test_extra_data_misc_none_cases():
    assert service_id_from_extra_data({}, 'deezer') is None                       # no extra_data
    assert service_id_from_extra_data({'extra_data': 'not json{'}, 'deezer') is None  # bad json
    assert service_id_from_extra_data(_extra('deezer', 111, discovered=False), 'deezer') is None


def test_resolve_waterfall_cache_then_library_then_unmatched():
    tracks = [
        _extra('deezer', 111) | {'artist_name': 'A', 'track_name': 'Cached'},   # cache hit
        {'artist_name': 'A', 'track_name': 'InLib'},                            # library hit (db_fn)
        {'artist_name': 'A', 'track_name': 'Nowhere'},                          # unmatched
    ]
    db_fn = lambda a, t, s: 'lib-222' if t == 'InLib' else None
    out = resolve_service_track_ids(tracks, 'deezer', db_fn=db_fn)
    ids = [r['service_track_id'] for r in out['resolved']]
    assert ids == ['111', 'lib-222', None]
    s = out['stats']
    assert s == {'total': 3, 'resolved': 2, 'unmatched': 1, 'from_cache': 1,
                 'from_library': 1, 'from_search': 0}


# ── backfill: confident live-search match for the un-cached/un-enriched tail (#945) ──

from core.metadata.types import Track as _Track
from core.exports.export_sources import search_service_track_id, BACKFILL_MIN_SCORE


def _cand(name, artist, tid, album_type='album'):
    return _Track(id=tid, name=name, artists=[artist], album='A',
                  duration_ms=200000, album_type=album_type)


def test_backfill_exact_match_returned():
    search = lambda q: [_cand('Not Like Us', 'Kendrick Lamar', 'dz-NLU')]
    assert search_service_track_id('Kendrick Lamar', 'Not Like Us', search_fn=search) == 'dz-NLU'


def test_backfill_wrong_artist_rejected():
    """SAFETY: an exact-title hit by the WRONG artist scores below the floor (no 1.5x exact-
    artist boost) → None, so backfill never adds someone else's same-named track."""
    search = lambda q: [_cand('Not Like Us', 'Some Other Guy', 'wrong-id')]
    assert search_service_track_id('Kendrick Lamar', 'Not Like Us', search_fn=search) is None


def test_backfill_karaoke_cover_rejected():
    """SAFETY: a karaoke/cover version is buried (x0.05) below the floor → None."""
    search = lambda q: [_cand('Not Like Us (Karaoke Version)', 'Karaoke All Stars', 'kar-id')]
    assert search_service_track_id('Kendrick Lamar', 'Not Like Us', search_fn=search) is None


def test_backfill_picks_real_over_cover():
    search = lambda q: [
        _cand('Not Like Us (Karaoke Version)', 'Karaoke All Stars', 'kar-id'),
        _cand('Not Like Us', 'Kendrick Lamar', 'real-id'),
    ]
    assert search_service_track_id('Kendrick Lamar', 'Not Like Us', search_fn=search) == 'real-id'


def test_backfill_empty_and_error_and_no_title():
    assert search_service_track_id('A', 'X', search_fn=lambda q: []) is None
    def boom(q):
        raise RuntimeError('deezer flaked')
    assert search_service_track_id('A', 'X', search_fn=boom) is None      # fail-safe
    assert search_service_track_id('A', '', search_fn=lambda q: [_cand('X', 'A', 'i')]) is None


def test_resolve_waterfall_uses_search_only_when_cache_and_library_miss():
    tracks = [
        _extra('deezer', 111) | {'artist_name': 'A', 'track_name': 'Cached'},
        {'artist_name': 'A', 'track_name': 'InLib'},
        {'artist_name': 'A', 'track_name': 'OnlyOnSvc'},
    ]
    db_fn = lambda a, t, s: 'lib-2' if t == 'InLib' else None
    search_id_fn = lambda a, t: 'srch-3' if t == 'OnlyOnSvc' else None
    out = resolve_service_track_ids(tracks, 'deezer', db_fn=db_fn, search_id_fn=search_id_fn)
    assert [r['service_track_id'] for r in out['resolved']] == ['111', 'lib-2', 'srch-3']
    s = out['stats']
    assert (s['from_cache'], s['from_library'], s['from_search'], s['unmatched']) == (1, 1, 1, 0)


def test_resolve_no_search_fn_leaves_tail_unmatched():
    out = resolve_service_track_ids([{'artist_name': 'A', 'track_name': 'X'}], 'deezer',
                                    db_fn=lambda a, t, s: None)   # search_id_fn omitted
    assert out['resolved'][0]['service_track_id'] is None
    assert out['stats']['unmatched'] == 1 and out['stats']['from_search'] == 0
