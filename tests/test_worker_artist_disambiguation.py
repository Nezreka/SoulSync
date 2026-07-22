"""Per-worker same-name artist disambiguation (#868).

Each enrichment worker, when several same-name candidates clear the name gate,
must pick the one whose catalog overlaps the albums the library actually owns —
not whichever the source ranked first. Covers Spotify (also the Spotify-Free
path, same client surface), iTunes, Deezer, and MusicBrainz.
"""

from __future__ import annotations

import types


# A query-aware fake DB: owned-albums query → owned titles; the source_id_conflict
# query (SELECT name FROM artists ...) → no conflict.
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, owned):
        self._owned = owned

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if 'FROM albums' in sql:
            return _Cur([(t,) for t in self._owned])
        return _Cur([])  # conflict check → none

    def cursor(self):
        return self

    def commit(self):
        pass

    def close(self):
        pass


class _DB:
    def __init__(self, owned):
        self._owned = owned

    def _get_connection(self):
        return _Conn(self._owned)


# The owned library: the RIGHT "Rone" made these.
OWNED = ['Tohu Bohu', 'Creatures', 'Mirapolis']
WRONG = types.SimpleNamespace(id='wrong_rone', name='Rone')
RIGHT = types.SimpleNamespace(id='right_rone', name='Rone')
ALBUMS = {
    'wrong_rone': [{'title': 'Mixtape Vol 1'}, {'title': 'Random Single'}],
    'right_rone': [{'title': 'Tohu Bohu (Deluxe)'}, {'title': 'Creatures'}],
}


def test_spotify_picks_artist_overlapping_owned_catalog():
    from core.spotify_worker import SpotifyWorker
    w = object.__new__(SpotifyWorker)
    w.db = _DB(OWNED)
    w.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
    w.client = types.SimpleNamespace(
        search_artists=lambda name, limit=5: [WRONG, RIGHT],   # wrong ranked first
        get_artist_albums=lambda aid: ALBUMS.get(aid, []),
    )
    captured = {}
    w._get_existing_id = lambda *a: None
    w._mark_status = lambda *a: None
    w._name_similarity = lambda a, b: 1.0       # both "Rone" clear the gate
    w._is_spotify_id = lambda i: True
    w._update_artist = lambda artist_id, obj: captured.update(id=obj.id)

    w._process_artist({'id': 5, 'name': 'Rone'})
    assert captured.get('id') == 'right_rone'
    assert w.stats['matched'] == 1


def test_itunes_picks_artist_overlapping_owned_catalog():
    from core.itunes_worker import iTunesWorker
    w = object.__new__(iTunesWorker)
    w.db = _DB(OWNED)
    w.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
    w.client = types.SimpleNamespace(
        search_artists=lambda name, limit=5: [WRONG, RIGHT],
        get_artist_albums=lambda aid: ALBUMS.get(aid, []),
    )
    captured = {}
    w._get_existing_id = lambda *a: None
    w._mark_status = lambda *a: None
    w._is_itunes_id = lambda i: True
    w._update_artist = lambda artist_id, obj: captured.update(id=obj.id)

    w._process_artist({'id': 5, 'name': 'Rone'})
    assert captured.get('id') == 'right_rone'
    assert w.stats['matched'] == 1


def test_deezer_picks_artist_overlapping_owned_catalog():
    from core.deezer_worker import DeezerWorker
    w = object.__new__(DeezerWorker)
    w.db = _DB(OWNED)
    w.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
    w.client = types.SimpleNamespace(
        search_artists=lambda name, limit=5: [WRONG, RIGHT],
        get_artist_albums_list=lambda aid: ALBUMS.get(aid, []),
        get_artist_info=lambda aid: {'id': aid, 'name': 'Rone'},
    )
    captured = {}
    w._get_existing_id = lambda *a: None
    w._mark_status = lambda *a: None
    w._update_artist = lambda artist_id, data: captured.update(id=data.get('id'))

    w._process_artist(5, 'Rone')
    assert captured.get('id') == 'right_rone'
    assert w.stats['matched'] == 1


def _mb_service(owned_release_groups):
    from core.musicbrainz_service import MusicBrainzService
    s = object.__new__(MusicBrainzService)
    s._check_cache = lambda *a, **k: None
    s._save_to_cache = lambda *a, **k: None
    s._calculate_similarity = lambda a, b: 1.0
    s.mb_client = types.SimpleNamespace(
        search_artist=lambda name, limit=5: [
            {'id': 'wrong_mbid', 'name': 'Rone', 'score': 100},  # ranked first
            {'id': 'right_mbid', 'name': 'Rone', 'score': 90},
        ],
        get_artist=lambda mbid, includes=None: owned_release_groups.get(mbid),
    )
    return s


def test_musicbrainz_picks_artist_overlapping_owned_catalog():
    rg = {
        'wrong_mbid': {'release-groups': [{'title': 'Other Stuff'}]},
        'right_mbid': {'release-groups': [{'title': 'Tohu Bohu'}, {'title': 'Creatures'}]},
    }
    result = _mb_service(rg).match_artist('Rone', owned_titles=OWNED)
    assert result is not None
    assert result['mbid'] == 'right_mbid'


def test_musicbrainz_without_owned_titles_keeps_legacy_behavior():
    """No owned titles → highest-confidence (name-order first) candidate, unchanged."""
    rg = {'wrong_mbid': {'release-groups': []}, 'right_mbid': {'release-groups': []}}
    result = _mb_service(rg).match_artist('Rone')  # no owned_titles
    assert result['mbid'] == 'wrong_mbid'  # the top-confidence pick


def test_musicbrainz_cache_hit_used_when_catalog_overlaps():
    """A cached mbid whose catalog overlaps the owned albums is trusted (no re-resolve)."""
    rg = {'cached_mbid': {'release-groups': [{'title': 'Tohu Bohu'}, {'title': 'Creatures'}]}}
    s = _mb_service(rg)
    s._check_cache = lambda *a, **k: {'musicbrainz_id': 'cached_mbid', 'confidence': 95}
    s.mb_client.search_artist = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not re-search when the cached match fits the library"))
    result = s.match_artist('Rone', owned_titles=OWNED)
    assert result['mbid'] == 'cached_mbid'
    assert result['cached'] is True


def test_musicbrainz_stale_cache_is_bypassed_and_re_resolved():
    """A cached mbid with ZERO owned-catalog overlap (wrong same-name artist) is
    bypassed → fresh disambiguated resolve. This is what makes a re-match work for
    MB despite the 90-day cache TTL (#868)."""
    rg = {
        'cached_wrong': {'release-groups': [{'title': 'Unrelated'}]},   # the stale cache
        'wrong_mbid': {'release-groups': [{'title': 'Other Stuff'}]},
        'right_mbid': {'release-groups': [{'title': 'Tohu Bohu'}, {'title': 'Creatures'}]},
    }
    s = _mb_service(rg)
    s._check_cache = lambda *a, **k: {'musicbrainz_id': 'cached_wrong', 'confidence': 95}
    result = s.match_artist('Rone', owned_titles=OWNED)
    assert result['mbid'] == 'right_mbid'   # re-resolved + disambiguated
    assert result.get('cached') is not True
