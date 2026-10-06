"""#1565: deezer matching picked the wrong song or version.

deezer's free-text search ranks karaoke, key-shifted and reprise tracks above
the original and, for "Auli'i Cravalho How Far I'll Go", leaves the original
(136340808) out entirely, while the field-scoped track:"..." search lists it
first. the shapes below are the live results captured for that query.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.deezer_client import DeezerClient
from core.metadata.song_search import with_song_first_pass

ARTIST = "Auli'i Cravalho"
TITLE = "How Far I'll Go"


def _t(tid, name, artist, secs):
    return SimpleNamespace(id=str(tid), name=name, artists=[artist], album='Moana',
                           duration_ms=secs * 1000, image_url=None)


ORIGINAL = _t(136340808, TITLE, ARTIST, 163)
REPRISE = _t(136340812, "How Far I'll Go (Reprise)", ARTIST, 87)
KARAOKE = [_t(1434887672 + i, "How Far I'll Go (原曲歌手:Auli'i Cravalho)", '歌っちゃ王', 164)
           for i in range(5)]


class _FakeDeezer(DeezerClient):
    """free text never has the original; the field-scoped search does."""

    def __init__(self):
        self.calls = []

    def search_tracks(self, query='', limit=20, *, track=None, artist=None, **kw):
        self.calls.append(('scoped' if track else 'text', query or track))
        if track:
            return [ORIGINAL, REPRISE]
        return [REPRISE, *KARAOKE]


# ── discovery: the first query also asks search_song ─────────────────────────

def test_first_query_merges_the_song_search():
    client = _FakeDeezer()
    source = with_song_first_pass(client, TITLE, ARTIST)
    first = source.search_tracks(f"{ARTIST} {TITLE}", limit=10)
    assert ORIGINAL in first
    # later queries are plain again, no extra api calls
    calls_before = len(client.calls)
    source.search_tracks(TITLE, limit=10)
    assert len(client.calls) == calls_before + 1


def test_other_sources_are_untouched():
    itunes = SimpleNamespace(search_tracks=lambda q, limit=10: [])
    assert with_song_first_pass(itunes, TITLE, ARTIST) is itunes
    assert with_song_first_pass(_FakeDeezer(), '', ARTIST).__class__ is _FakeDeezer


@pytest.fixture()
def real_scorer():
    from core.discovery import scoring
    from core.matching_engine import MusicMatchingEngine
    previous = scoring.matching_engine
    scoring.init(MusicMatchingEngine())
    yield scoring._discovery_score_candidates
    scoring.init(previous)


def _match(client, scorer, duration_ms=0):
    from core.discovery.matching import MBMatchDeps, match_mb_track
    deps = MBMatchDeps(
        matching_engine=SimpleNamespace(
            generate_download_queries=lambda t: [f"{ARTIST} {TITLE}", TITLE]),
        score_candidates=scorer,
        spotify_client_getter=lambda: None,
        itunes_client_getter=lambda: client,
        prefer_spotify_getter=lambda: False,
        min_confidence=0.7,
    )
    return match_mb_track({'track_name': TITLE, 'artist_name': ARTIST,
                           'duration_ms': duration_ms}, deps)


def test_identification_saves_the_original_not_the_reprise(real_scorer):
    # no duration to rule the 87s reprise out: before this, the reprise
    # scored 0.82 off the free-text results and was saved
    out = _match(_FakeDeezer(), real_scorer)
    assert out['id'] == '136340808'


def test_free_text_alone_still_picks_the_reprise(real_scorer):
    # the old behavior, pinned so the test above proves the fix
    class _TextOnly(_FakeDeezer):
        def search_tracks(self, query='', limit=20, *, track=None, artist=None, **kw):
            return [REPRISE, *KARAOKE]

    out = _match(_TextOnly(), real_scorer)
    assert out['id'] == '136340812'


# ── re-identify: the default query searches for the track itself ─────────────

def test_reidentify_default_query_finds_the_original():
    from core.imports.rematch_search import search_release_candidates
    client = _FakeDeezer()
    rows = search_release_candidates('deezer', f"{TITLE} {ARTIST}",
                                     client_factory=lambda s: client,
                                     title=TITLE, artist=ARTIST)
    # the field-scoped hit comes first, the free-text rows still follow
    assert rows[0]['track_id'] == '136340808'


def test_reidentify_edited_query_stays_free_text():
    from core.imports.rematch_search import search_release_candidates
    client = _FakeDeezer()
    search_release_candidates('deezer', 'something else',
                              client_factory=lambda s: client)
    assert client.calls == [('text', 'something else')]


# ── enrichment: the title decides among the artist's hits ────────────────────

def _hit(tid, title, artist=ARTIST):
    return {'id': tid, 'title': title, 'artist': {'id': 9, 'name': artist}}


def test_pick_prefers_the_exact_title_over_an_earlier_reprise():
    pick = DeezerClient._pick_track_by_artist(
        [_hit(2, "How Far I'll Go (Reprise)"), _hit(1, TITLE)], ARTIST, TITLE)
    assert pick['id'] == 1


def test_pick_keeps_a_version_the_request_names():
    pick = DeezerClient._pick_track_by_artist(
        [_hit(1, TITLE), _hit(2, "How Far I'll Go (Reprise)")], ARTIST,
        "How Far I'll Go (Reprise)")
    assert pick['id'] == 2


def test_pick_accepts_harmless_extras():
    pick = DeezerClient._pick_track_by_artist(
        [_hit(1, 'Get Lucky (Live)', 'Daft Punk'),
         _hit(2, 'Get Lucky (feat. Pharrell Williams)', 'Daft Punk')],
        'Daft Punk', 'Get Lucky')
    assert pick['id'] == 2


def test_pick_refuses_only_other_versions():
    # the artist's only hit is a live cut: nothing, so search_track tries
    # its plain search instead of stamping the live version
    assert DeezerClient._pick_track_by_artist(
        [_hit(1, 'Get Lucky (Live)', 'Daft Punk')], 'Daft Punk', 'Get Lucky') is None


def test_search_track_falls_back_when_the_phrase_only_finds_a_reprise(monkeypatch):
    monkeypatch.setattr('core.deezer_client.get_metadata_cache',
                        lambda: SimpleNamespace(store_entity=lambda *a, **k: None))
    monkeypatch.setattr('core.deezer_throttle.wait_for_slot', lambda: None)
    c = DeezerClient.__new__(DeezerClient)
    responses = [[_hit(2, "How Far I'll Go (Reprise)")], [_hit(1, TITLE)]]
    c._search_track_raw = lambda q: responses.pop(0)
    assert c.search_track(ARTIST, TITLE)['id'] == 1


# ── worker title check: brackets before the dash tail ────────────────────────

def test_worker_normalizer_strips_brackets_before_the_dash():
    from core.deezer_worker import DeezerWorker
    w = DeezerWorker.__new__(DeezerWorker)
    assert w._normalize_name('Get Lucky (Radio Edit - feat. Pharrell Williams)') == 'get lucky'
    assert w._normalize_name('Get Lucky - Radio Edit') == 'get lucky'


# ── fix / rematch keep the real source ───────────────────────────────────────

def test_search_labels_fallback_results():
    from api.source_playlists import _is_spotify_id
    assert _is_spotify_id('1K0VQGwAHUSavDdPZ2aTAY')
    assert not _is_spotify_id('136340808')
    assert not _is_spotify_id('')


def test_fix_and_rematch_read_the_source():
    from api.mirrored_playlists import _picked_source
    assert _picked_source({'id': '136340808', 'source': 'deezer'}) == 'deezer'
    assert _picked_source({'id': 'x'}) == 'spotify'   # older clients
