"""§52.8: WatchlistScanner.add_track_to_wishlist must materialize the lib2
Artist/Release/Track for a detected missing track, not just look up the
artist-level profile (the old, narrower behavior it replaces)."""

from __future__ import annotations

import types

from core.watchlist_scanner import WatchlistScanner


class _FakeDB:
    def __init__(self):
        self.add_calls = []

    def add_to_wishlist(self, **kwargs):
        self.add_calls.append(kwargs)
        return True


def _build_scanner():
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = _FakeDB()
    return scanner


def _artist(profile_id=7):
    return types.SimpleNamespace(
        artist_name="Some Artist",
        spotify_artist_id="sp-artist",
        profile_id=profile_id,
    )


_TRACK = {
    "id": "sp-track-1",
    "name": "Some Track",
    "artists": [{"name": "Some Artist", "id": "sp-artist"}],
    "track_number": 1,
    "disc_number": 1,
}
_ALBUM = {
    "id": "sp-album-1",
    "name": "Some Album",
    "release_date": "2024-01-01",
    "images": [],
    "album_type": "album",
    "total_tracks": 10,
    "artists": [{"name": "Some Artist"}],
}


def test_uses_materialized_effective_profile_when_available(monkeypatch):
    scanner = _build_scanner()
    calls = []

    def _fake_materialize(spotify_track_data, **kwargs):
        calls.append((spotify_track_data, kwargs))
        return {"quality_profile": {"id": 42}}

    monkeypatch.setattr(
        "core.library2.materialize.materialize_wishlist_intent", _fake_materialize)

    assert scanner.add_track_to_wishlist(_TRACK, _ALBUM, _artist(profile_id=7)) is True

    assert len(calls) == 1
    payload, kwargs = calls[0]
    assert payload["name"] == "Some Track"
    assert payload["artists"][0]["name"] == "Some Artist"
    assert kwargs["profile_id"] == 7

    add_call = scanner._database.add_calls[0]
    assert add_call["quality_profile_id"] == 42


def test_falls_back_to_artist_only_lookup_when_materialize_returns_none(monkeypatch):
    scanner = _build_scanner()
    monkeypatch.setattr(
        "core.library2.materialize.materialize_wishlist_intent",
        lambda payload, **kwargs: None)
    monkeypatch.setattr(
        "core.library2.profile_lookup.lib2_quality_profile_for_artist",
        lambda database, artist_name: 99)

    assert scanner.add_track_to_wishlist(_TRACK, _ALBUM, _artist()) is True

    add_call = scanner._database.add_calls[0]
    assert add_call["quality_profile_id"] == 99


def test_falls_back_to_artist_only_lookup_when_materialize_raises(monkeypatch):
    scanner = _build_scanner()

    def _boom(payload, **kwargs):
        raise RuntimeError("lib2 unavailable")

    monkeypatch.setattr(
        "core.library2.materialize.materialize_wishlist_intent", _boom)
    monkeypatch.setattr(
        "core.library2.profile_lookup.lib2_quality_profile_for_artist",
        lambda database, artist_name: 13)

    assert scanner.add_track_to_wishlist(_TRACK, _ALBUM, _artist()) is True

    add_call = scanner._database.add_calls[0]
    assert add_call["quality_profile_id"] == 13
