"""Deezer playlist tracks must carry the REAL album track_position, not their
playlist index — otherwise the downloaded file is tagged with the wrong track
number (e.g. 'Apologize' from Shock Value tagged track 1 instead of 16)."""

from __future__ import annotations

from core.deezer_client import resolve_album_track_positions


class _Resp:
    def __init__(self, data, ok=True):
        self._d, self.ok = data, ok

    def json(self):
        return self._d


class _Session:
    """Fake requests session returning /album/<id>/tracks payloads."""

    def __init__(self, by_album, fail_for=()):
        self.by_album, self.fail_for, self.calls = by_album, set(fail_for), []

    def get(self, url, params=None, timeout=None):
        aid = url.rstrip('/').split('/')[-2]   # …/album/<aid>/tracks
        self.calls.append(aid)
        if aid in self.fail_for:
            return _Resp(None, ok=False)
        return _Resp({'data': self.by_album.get(aid, [])})


def test_maps_track_id_to_real_album_position():
    sess = _Session({'119606': [
        {'id': 100, 'track_position': 16}, {'id': 101, 'track_position': 2}]})
    pos = resolve_album_track_positions(sess, 'https://api.deezer.com', {'119606'}, sleep_s=0)
    assert pos == {'100': 16, '101': 2}           # real positions, not 1/2 enumerate


def test_cache_first_skips_the_network():
    class _Cache:
        def __init__(self): self.stored = {}
        def get_entity(self, src, kind, aid):
            return {'data': [{'id': 7, 'track_position': 9}]} if kind == 'album_tracks' else None
        def store_entity(self, *a, **k): pass
    sess = _Session({})
    pos = resolve_album_track_positions(sess, 'https://api.deezer.com', {'42'}, cache=_Cache(), sleep_s=0)
    assert pos == {'7': 9} and sess.calls == []   # served from cache, no HTTP


def test_failed_album_is_simply_absent_not_fatal():
    sess = _Session({'1': [{'id': 5, 'track_position': 3}]}, fail_for={'2'})
    pos = resolve_album_track_positions(sess, 'https://api.deezer.com', {'1', '2'}, sleep_s=0)
    assert pos == {'5': 3}                          # album 2 failed → just missing


def test_zero_position_is_ignored():
    # Deezer sometimes returns 0/None for odd entries — don't poison the map with them
    sess = _Session({'1': [{'id': 5, 'track_position': 0}, {'id': 6}, {'id': 7, 'track_position': 4}]})
    pos = resolve_album_track_positions(sess, 'https://api.deezer.com', {'1'}, sleep_s=0)
    assert pos == {'7': 4}
