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


def _wire(db=None, file=None, mb=None, cache=None, flagged=None, verify=None):
    """Wire a resolve_fn from fakes. ``flagged``/``verify`` default to "nothing is
    flagged, everything verifies" so the existing waterfall-order tests below don't
    have to know about the #903-follow-up gates at all — only the new gate-specific
    tests pass non-default fakes for them."""
    recorded = {}
    store = dict(cache or {})
    fn = build_resolve_fn(
        db_fn=lambda a, t: (db or {}).get((a, t)),
        file_fn=lambda a, t: (file or {}).get((a, t)),
        mb_fn=lambda a, t: (mb or {}).get((a, t)),
        cache_lookup=lambda k: store.get(k),
        cache_record=lambda k, m: recorded.__setitem__(k, m) or True,
        mbid_flagged_fn=flagged if flagged is not None else (lambda a, t: False),
        verify_fn=verify if verify is not None else (lambda m, t: True),
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


# ── #903 follow-up: DB/file rungs are gated by the mbid_mismatch repair job's pending
# findings, plus a live MusicBrainz title cross-check — either gate failing makes the
# rung a miss so the waterfall falls through instead of exporting the wrong recording ──

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
        flagged=lambda a, t: True,
    )
    assert fn("A", "T") == ("99999999-8888-7777-6666-555555555555", SRC_MUSICBRAINZ)


def test_file_hit_flagged_by_pending_finding_falls_through_to_musicbrainz():
    """DB rung misses outright; the file rung's MBID is flagged -> falls through to the
    live MusicBrainz rung."""
    fn, _ = _wire(
        db={},
        file={("A", "T"): MBID},
        mb={("A", "T"): OTHER_MBID},
        flagged=lambda a, t: True,
    )
    assert fn("A", "T") == (OTHER_MBID, SRC_MUSICBRAINZ)


def test_flagged_predicate_is_per_track_not_global():
    fn, _ = _wire(
        db={("A", "T"): MBID, ("A", "Other"): MBID},
        flagged=lambda a, t: t == "T",
    )
    assert fn("A", "T") == (None, None)
    assert fn("A", "Other") == (MBID, SRC_DB)


def test_flagged_fn_called_once_across_db_and_file_rung_for_same_track():
    """Both the DB and file rungs resolve to (different) MBIDs for the same track, and
    both get rejected by the flagged predicate -> the per-run memo means the predicate is
    only actually invoked once for ('A', 'T'), not once per rung."""
    calls = []

    def flagged(a, t):
        calls.append((a, t))
        return True

    fn, _ = _wire(db={("A", "T"): MBID}, file={("A", "T"): OTHER_MBID}, flagged=flagged)
    assert fn("A", "T") == (None, None)
    assert calls == [("A", "T")]


def test_verify_fn_mismatch_falls_through_to_next_rung():
    fn, _ = _wire(
        db={("A", "T"): MBID},
        mb={("A", "T"): OTHER_MBID},
        verify=lambda m, t: False,
    )
    assert fn("A", "T") == (OTHER_MBID, SRC_MUSICBRAINZ)


def test_verify_fn_match_accepts_the_rung():
    fn, _ = _wire(db={("A", "T"): MBID}, verify=lambda m, t: True)
    assert fn("A", "T") == (MBID, SRC_DB)


def test_verify_fn_exception_fails_open_and_accepts_the_rung():
    """Network trouble in the verifier must never degrade the export -> accept."""
    def boom(m, t):
        raise RuntimeError("musicbrainz flaked")
    fn, _ = _wire(db={("A", "T"): MBID}, verify=boom)
    assert fn("A", "T") == (MBID, SRC_DB)


def test_verify_fn_memoized_at_most_once_per_mbid_title_pair():
    calls = []

    def verify(m, t):
        calls.append((m, t))
        return True

    fn, _ = _wire(db={("A", "T"): MBID}, verify=verify)
    fn("A", "T")
    fn("A", "T")
    assert calls == [(MBID, "T")]


def test_mbid_flagged_by_repair_finding_real_sql(tmp_path, monkeypatch):
    """Run the ACTUAL query against a real (temp) tracks/artists/repair_findings schema —
    a pending mbid_mismatch finding keyed on the track's id must flag it; a resolved one,
    or a finding for a different track, must not."""
    import sqlite3
    import types
    import core.exports.export_sources as es

    dbfile = tmp_path / "lib.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        "CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE tracks (id TEXT, artist_id TEXT, title TEXT, "
        "musicbrainz_recording_id TEXT, file_path TEXT);"
        "CREATE TABLE repair_findings (finding_type TEXT, status TEXT, "
        "entity_type TEXT, entity_id TEXT, file_path TEXT);"
        "INSERT INTO artists VALUES ('a1','Fall Out Boy');"
        "INSERT INTO tracks VALUES "
        "('t1','a1','Thnks fr th Mmrs','bad-mbid','/music/track1.mp3'),"
        "('t2','a1','Sugar, We''re Goin Down','ok-mbid','/music/track2.mp3');"
        "INSERT INTO repair_findings VALUES "
        "('mbid_mismatch','pending','track','t1','/music/track1.mp3'),"
        "('mbid_mismatch','resolved','track','t2','/music/track2.mp3');"
    )
    con.commit()
    con.close()

    fake_db = types.SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(dbfile)))
    monkeypatch.setattr("database.music_database.get_database", lambda: fake_db)

    assert es.mbid_flagged_by_repair_finding("Fall Out Boy", "Thnks fr th Mmrs") is True
    # resolved (not pending) finding -> not flagged
    assert es.mbid_flagged_by_repair_finding("Fall Out Boy", "Sugar, We're Goin Down") is False
    # no matching track at all -> not flagged
    assert es.mbid_flagged_by_repair_finding("Fall Out Boy", "Unknown Song") is False


def test_verify_recording_title_no_client_accepts(monkeypatch):
    import core.exports.export_sources as es
    monkeypatch.setattr(es, "_get_mb_service", lambda: None)
    assert es.verify_recording_title(MBID, "Anything") is True


def test_verify_recording_title_matches_and_mismatches(monkeypatch):
    import types
    import core.exports.export_sources as es

    def _svc(recording_title):
        client = types.SimpleNamespace(
            get_recording=lambda mbid, includes=None: {"title": recording_title})
        return types.SimpleNamespace(mb_client=client)

    monkeypatch.setattr(es, "_get_mb_service", lambda: _svc("Thnks fr th Mmrs"))
    assert es.verify_recording_title(MBID, "Thnks Fr Th Mmrs") is True   # case-insensitive match

    monkeypatch.setattr(es, "_get_mb_service", lambda: _svc("Don't You Know Who I Think I Am?"))
    assert es.verify_recording_title(MBID, "Thnks fr th Mmrs") is False  # real mis-tag case (#903)


def test_verify_recording_title_recording_not_found_is_a_mismatch(monkeypatch):
    import types
    import core.exports.export_sources as es
    client = types.SimpleNamespace(get_recording=lambda mbid, includes=None: None)
    monkeypatch.setattr(es, "_get_mb_service", lambda: types.SimpleNamespace(mb_client=client))
    assert es.verify_recording_title(MBID, "Anything") is False


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
