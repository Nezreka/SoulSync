"""The playlist import's embedded album dict used to carry the PLAYLIST's track
count as the album's `total_tracks` — a 12-track album pulled in from a
1582-track playlist claimed 1582 tracks, which then rode into tags and any
"album has N tracks" logic. The release-dates pass already fetches each
album's payload (which carries `nb_tracks`); the import now uses that,
falling back to the playlist count only when the album lookup failed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.deezer_download_client as ddc
from core.deezer_download_client import DeezerDownloadClient


def _resp(payload):
    return SimpleNamespace(ok=True, json=lambda: payload,
                           raise_for_status=lambda: None)


TRACK = {
    'id': 't1', 'title': 'Song One', 'duration': 200,
    'artist': {'name': 'Some Artist'},
    'album': {'id': '42', 'title': 'The Album', 'cover_medium': ''},
}


def _client(monkeypatch, *, album_payload):
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._session = SimpleNamespace()  # unused: no pagination, no position pass

    def fake_api_get(url, **kw):
        if url.endswith('/playlist/p1/tracks'):
            return _resp({'data': []})  # no more pages
        if url.endswith('/playlist/p1'):
            return _resp({'id': 'p1', 'title': 'P', 'nb_tracks': 1582,
                          'tracks': {'data': [TRACK]},
                          'picture_medium': '', 'creator': {'name': 'me'}})
        if url.endswith('/album/42'):
            return album_payload
        raise AssertionError(f"unexpected url {url}")

    c._api_get = fake_api_get
    monkeypatch.setattr('core.deezer_client.resolve_album_track_positions',
                        lambda *a, **k: {})
    monkeypatch.setattr('core.metadata.cache.get_metadata_cache',
                        lambda: (_ for _ in ()).throw(RuntimeError("no cache")),
                        raising=False)
    return c


def test_embedded_album_uses_the_albums_own_track_count(monkeypatch):
    c = _client(monkeypatch, album_payload=_resp(
        {'release_date': '2020-01-01', 'nb_tracks': 12}))
    out = c.get_playlist_tracks('p1')
    assert out['tracks'][0]['album']['total_tracks'] == 12
    assert out['track_count'] == 1582  # the playlist header keeps its own count


def test_falls_back_to_playlist_count_when_the_album_lookup_failed(monkeypatch):
    c = _client(monkeypatch, album_payload=None)
    out = c.get_playlist_tracks('p1')
    assert out['tracks'][0]['album']['total_tracks'] == 1582
