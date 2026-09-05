"""SYNC-01: the chosen quality profile has to travel with a new wish.

Both writers that create wishlist rows out of a *source* — the playlist sync
and the watchlist scan — used to hand `add_to_wishlist` an explicit
`quality_profile_id` and stopped on this branch. Without it the row persists
the global default, and no ordinary reconcile puts it right: the wanted
projection is already current, so `reconcile_track_wishlist` skips the row and
the wrong profile drives every later download decision for that track.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _RecordingDB:
    def __init__(self):
        self.calls = []

    def add_to_wishlist(self, **kwargs):
        self.calls.append(kwargs)
        return True


@pytest.fixture
def scanner():
    from core.watchlist_scanner import WatchlistScanner

    return WatchlistScanner(spotify_client=object())


def _watchlist_artist(**overrides):
    from database.music_database import WatchlistArtist
    from datetime import datetime

    base = dict(id=1, spotify_artist_id="sp-artist", artist_name="Artist",
                date_added=datetime.now(), profile_id=1)
    base.update(overrides)
    fields = {f: v for f, v in base.items()
              if f in WatchlistArtist.__dataclass_fields__}
    artist = WatchlistArtist(**fields)
    for key, value in base.items():
        if key not in WatchlistArtist.__dataclass_fields__:
            setattr(artist, key, value)
    return artist


def test_the_watchlist_artists_quality_profile_reaches_the_wishlist_row(
        scanner, monkeypatch):
    db = _RecordingDB()
    monkeypatch.setattr(type(scanner), "database", property(lambda _self: db))
    artist = _watchlist_artist(quality_profile_id=4)

    track = {"id": "sp-t", "name": "T", "artists": [{"name": "Artist", "id": "sp-artist"}]}
    album = {"name": "Alb", "id": "sp-alb", "release_date": "2024-01-01",
             "images": [], "album_type": "album", "total_tracks": 10,
             "artists": [{"name": "Artist", "id": "sp-artist"}]}

    assert scanner.add_track_to_wishlist(track, album, artist) is True
    assert db.calls, "the scanner never reached add_to_wishlist"
    assert db.calls[0]["quality_profile_id"] == 4
    # The user profile is a different namespace and must not be confused with it.
    assert db.calls[0]["profile_id"] == 1


def test_no_artist_profile_leaves_the_default_resolution_to_the_database(
        scanner, monkeypatch):
    db = _RecordingDB()
    monkeypatch.setattr(type(scanner), "database", property(lambda _self: db))
    artist = _watchlist_artist(quality_profile_id=None)

    scanner.add_track_to_wishlist(
        {"id": "sp-t", "name": "T", "artists": []},
        {"name": "Alb", "id": "sp-alb", "release_date": "", "images": [],
         "album_type": "album", "total_tracks": 1, "artists": []},
        artist)

    assert db.calls[0]["quality_profile_id"] is None


def test_playlist_sync_hands_on_the_source_tracks_quality_profile():
    """The sync-service half of SYNC-01. The call is deep inside
    `sync_playlist`, so pin the property that broke: the source track's
    quality profile is passed on, not dropped."""
    import ast
    import inspect

    import services.sync_service as sync_service

    tree = ast.parse(inspect.getsource(sync_service))
    passing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", "") != "add_spotify_track_to_wishlist":
            continue
        kwargs = {kw.arg for kw in node.keywords}
        passing.append("quality_profile_id" in kwargs)
    assert passing, "the playlist sync no longer adds tracks to the wishlist"
    assert all(passing), (
        "a playlist-sync wishlist add drops the source quality profile")
