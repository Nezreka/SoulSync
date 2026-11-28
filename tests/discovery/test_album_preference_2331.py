"""#2331: the album a playlist entry names is part of which track it is.

a beethoven piece sits on countless compilations under the same title and
artist. a spotify playlist entry from "the 50 greatest pieces of classical
music" means that copy, so identification, the library match and the cache
all prefer it, and only fall back to another copy when the album isn't there.
a source with no album gets exactly what it got before.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.discovery import album_pref
from core.discovery import playlist as dp
from core.discovery import scoring
from core.discovery.direct_match import direct_source_match
from core.matching_engine import MusicMatchingEngine
from database.music_database import DatabaseTrack, MusicDatabase
from tests.discovery.test_discovery_playlist import _build_deps, _playlist, _track

TITLE = "Symphony No. 5 in C Minor, Op. 67: I. Allegro con brio"
ARTIST = "Ludwig van Beethoven"
WANTED = "The 50 Greatest Pieces of Classical Music"
OTHERS = [
    "Beethoven: Symphonies Nos. 5 & 7",
    "Classical Music for Studying",
    "Beethoven: The Complete Symphonies",
    "100 Must-Have Classical Masterpieces",
]


@dataclass
class _Result:
    id: str
    name: str
    album: str
    artists: list = field(default_factory=lambda: [ARTIST])
    duration_ms: int = 450000
    image_url: str = ''
    release_date: str = '2009-01-01'


def _copies(wanted_last=True):
    rows = [_Result(id=f'other-{i}', name=TITLE, album=a) for i, a in enumerate(OTHERS)]
    wanted = _Result(id='wanted', name=TITLE, album=WANTED)
    return rows + [wanted] if wanted_last else [wanted] + rows


@pytest.fixture(autouse=True)
def _real_engine():
    old = scoring.matching_engine
    scoring.init(MusicMatchingEngine())
    yield
    scoring.matching_engine = old


def _real_score(deps):
    deps.discovery_score_candidates = scoring._discovery_score_candidates
    return deps


# ── album names ──────────────────────────────────────────────────────────────

def test_album_names_compare_spelling_aside():
    assert album_pref.same_album(WANTED, "the 50 greatest pieces of classical music")
    assert album_pref.same_album("Abbey Road (Remastered 2019)", "Abbey Road")
    assert album_pref.same_album({'name': "Björk Hits"}, "Bjork Hits")
    assert not album_pref.same_album(WANTED, OTHERS[0])
    assert not album_pref.same_album('', WANTED)


def test_cached_match_from_another_album_does_not_fit():
    assert not album_pref.cached_match_fits_album(WANTED, {'album': {'name': OTHERS[1]}})
    assert album_pref.cached_match_fits_album(WANTED, {'album': {'name': WANTED}})
    # no album on either side says nothing
    assert album_pref.cached_match_fits_album('', {'album': {'name': OTHERS[1]}})
    assert album_pref.cached_match_fits_album(WANTED, {'album': ''})


def test_album_query_skips_singles_and_missing_albums():
    assert album_pref.album_query(TITLE, ARTIST, WANTED).endswith(WANTED)
    assert album_pref.album_query("Hello", "Adele", "Hello") == ''
    assert album_pref.album_query("Hello", "Adele", '') == ''


def test_album_copy_never_beats_a_clearly_better_match():
    good = _Result(id='good', name=TITLE, album=OTHERS[0])
    weak = _Result(id='weak', name='Something Else', album=WANTED)

    def score(rs):
        best = max(rs, key=lambda r: 0.95 if r is good else 0.5)
        return best, (0.95 if best is good else 0.5)

    match, conf, on_album = album_pref.best_with_album(score, WANTED, [good, weak], 0.7)
    assert match is good and conf == 0.95 and not on_album


# ── mirrored playlist discovery ──────────────────────────────────────────────

def _mirrored(album_name, results, **kw):
    t = _track(track_id=1, name=TITLE, artist=ARTIST, duration_ms=450000)
    t['album_name'] = album_name
    deps = _build_deps(tracks_by_playlist={'p1': [t]}, discovery_source='itunes',
                       spotify_auth=False, itunes_results=results, **kw)
    return _real_score(deps)


def test_classical_compilation_picks_the_playlists_album():
    deps = _mirrored(WANTED, _copies(wanted_last=True))
    dp.run_playlist_discovery_worker([_playlist('p1', source='file')], deps=deps)

    (_, extra), = deps._db.extra_data_writes
    assert extra['matched_data']['id'] == 'wanted'
    assert extra['matched_data']['album']['name'] == WANTED
    # the album was asked for by name first
    assert WANTED in deps._itunes.search_calls[0][0]


def test_album_not_in_results_falls_back_to_the_best_other_copy():
    deps = _mirrored("Some Album Nobody Has", _copies()[:-1])
    dp.run_playlist_discovery_worker([_playlist('p1', source='file')], deps=deps)

    (_, extra), = deps._db.extra_data_writes
    assert extra['matched_data']['id'] == 'other-0'
    assert extra['wing_it_fallback'] is False


def test_no_album_on_the_source_is_unchanged():
    deps = _mirrored('', _copies(wanted_last=True))
    dp.run_playlist_discovery_worker([_playlist('p1', source='file')], deps=deps)

    (_, extra), = deps._db.extra_data_writes
    # same first-best as before the change, and no album query
    assert extra['matched_data']['id'] == 'other-0'
    assert all(WANTED not in q for q, _ in deps._itunes.search_calls)


def test_pop_single_is_unchanged():
    hit = _Result(id='pop', name='Blinding Lights', album='After Hours', artists=['The Weeknd'],
                  duration_ms=200000)
    t = _track(track_id=1, name='Blinding Lights', artist='The Weeknd', duration_ms=200000)
    t['album_name'] = 'After Hours'
    deps = _real_score(_build_deps(tracks_by_playlist={'p1': [t]}, discovery_source='itunes',
                                   spotify_auth=False, itunes_results=[hit]))
    dp.run_playlist_discovery_worker([_playlist('p1', source='file')], deps=deps)

    (_, extra), = deps._db.extra_data_writes
    assert extra['matched_data']['id'] == 'pop'
    assert extra['matched_data']['album']['name'] == 'After Hours'


def test_cached_copy_from_another_album_is_searched_again():
    cached = {'id': 'other-2', 'name': TITLE, 'artists': [ARTIST],
              'album': {'name': OTHERS[2]}, 'confidence': 0.95}
    deps = _mirrored(WANTED, _copies(), cache_match=cached)
    dp.run_playlist_discovery_worker([_playlist('p1', source='file')], deps=deps)

    (_, extra), = deps._db.extra_data_writes
    assert extra['matched_data']['id'] == 'wanted'


def test_cached_copy_on_the_same_album_is_used():
    cached = {'id': 'wanted', 'name': TITLE, 'artists': [ARTIST],
              'album': {'name': WANTED}, 'confidence': 0.95}
    deps = _mirrored(WANTED, _copies(), cache_match=cached)
    dp.run_playlist_discovery_worker([_playlist('p1', source='file')], deps=deps)

    (_, extra), = deps._db.extra_data_writes
    assert extra['matched_data']['id'] == 'wanted'
    assert deps._itunes.search_calls == []


# ── the shared search the other discovery workers use ────────────────────────

class _Fallback:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search_tracks(self, query, limit=10):
        self.queries.append(query)
        return self.results

    def get_track_details(self, track_id):
        return None


def _ns(album):
    import types
    return types.SimpleNamespace(id='x', name=TITLE, artists=[ARTIST], album=album,
                                 duration_ms=450000)


def test_shared_search_prefers_the_named_album():
    client = _Fallback(_copies(wanted_last=True))
    result = scoring._search_spotify_for_tidal_track(_ns(WANTED), use_spotify=False,
                                                     itunes_client=client)
    assert result['id'] == 'wanted'
    assert result['album']['name'] == WANTED


def test_shared_search_without_album_is_unchanged():
    client = _Fallback(_copies(wanted_last=True))
    result = scoring._search_spotify_for_tidal_track(_ns(''), use_spotify=False,
                                                     itunes_client=client)
    assert result['id'] == 'other-0'
    assert all(WANTED not in q for q in client.queries)


# ── spotify (no auth): the playlist's own id is the exact track ──────────────

def test_spotify_no_auth_uses_the_track_id():
    from core.spotify_client import SpotifyClient

    raw = {'id': '0flxRcIYyhr9teLBjouwQ8', 'name': TITLE, 'duration_ms': 450000,
           'artists': [{'name': ARTIST}],
           'album': {'name': WANTED, 'images': [], 'release_date': '2009-01-01',
                     'album_type': 'compilation', 'total_tracks': 50}}

    class _Free(SpotifyClient):
        def __init__(self):
            self.lookups = []

        def get_track_details(self, track_id, allow_fallback=True):
            self.lookups.append((track_id, allow_fallback))
            return dict(raw)  # the no-auth reader's raw track, no raw_data wrapper

    free = _Free()
    match = direct_source_match('spotify', 'spotify', False, raw['id'], fallback_client=free)
    assert match.album == WANTED
    assert free.lookups == [(raw['id'], False)]
    # a non-spotify metadata client never gets a spotify id
    assert direct_source_match('spotify', 'spotify', False, raw['id'],
                               fallback_client=_Fallback([])) is None


# ── library match ────────────────────────────────────────────────────────────

def _lib_track(i, album, title=TITLE):
    t = DatabaseTrack(id=i, album_id=i, artist_id=1, title=title, file_path=f'/m/{i}.flac')
    t.artist_name = ARTIST
    t.album_title = album
    return t


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def test_library_prefers_the_copy_on_the_playlists_album(db):
    pool = [_lib_track(i, a) for i, a in enumerate(OTHERS)] + [_lib_track(9, WANTED)]
    track, conf = db.check_track_exists(TITLE, ARTIST, confidence_threshold=0.7,
                                        candidate_tracks=pool, prefer_album=WANTED)
    assert track.album_title == WANTED and conf >= 0.7


def test_library_album_copy_wins_a_near_tie(db, monkeypatch):
    pool = [_lib_track(1, OTHERS[0]), _lib_track(2, WANTED)]
    scores = {1: 0.97, 2: 0.94}
    monkeypatch.setattr(db, '_calculate_track_confidence', lambda t, a, trk: scores[trk.id])
    track, conf = db.check_track_exists(TITLE, ARTIST, confidence_threshold=0.7,
                                        candidate_tracks=pool, prefer_album=WANTED)
    assert track.id == 2 and conf == 0.94


def test_library_album_copy_never_beats_a_clearly_better_one(db, monkeypatch):
    pool = [_lib_track(1, OTHERS[0]), _lib_track(2, WANTED)]
    scores = {1: 0.97, 2: 0.72}
    monkeypatch.setattr(db, '_calculate_track_confidence', lambda t, a, trk: scores[trk.id])
    track, _ = db.check_track_exists(TITLE, ARTIST, confidence_threshold=0.7,
                                     candidate_tracks=pool, prefer_album=WANTED)
    assert track.id == 1


def test_library_album_copy_under_the_threshold_never_blocks_a_match(db, monkeypatch):
    # album copy first, just under the bar; the other copy clears it
    pool = [_lib_track(2, WANTED), _lib_track(1, OTHERS[0])]
    scores = {1: 0.72, 2: 0.69}
    monkeypatch.setattr(db, '_calculate_track_confidence', lambda t, a, trk: scores[trk.id])
    track, conf = db.check_track_exists(TITLE, ARTIST, confidence_threshold=0.7,
                                        candidate_tracks=pool, prefer_album=WANTED)
    assert track.id == 1 and conf == 0.72


def test_library_without_album_keeps_the_best_score(db, monkeypatch):
    pool = [_lib_track(1, OTHERS[0]), _lib_track(2, WANTED)]
    scores = {1: 0.97, 2: 0.94}
    monkeypatch.setattr(db, '_calculate_track_confidence', lambda t, a, trk: scores[trk.id])
    track, _ = db.check_track_exists(TITLE, ARTIST, confidence_threshold=0.7,
                                     candidate_tracks=pool)
    assert track.id == 1
