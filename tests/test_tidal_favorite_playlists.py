"""Tidal followed-playlist parsing (Specialmed's report: the V2 owners.id
filter lists only playlists the user CREATED, so followed/editorial
playlists never appeared). The v1 favorites rows are parsed by
TidalClient._favorite_playlist_from_item — pinned here hermetically, no
network."""

from __future__ import annotations

from core.tidal_client import TidalClient


def test_wrapped_favorites_row_parses():
    playlist = TidalClient._favorite_playlist_from_item({
        'created': '2026-08-01T00:00:00.000+0000',
        'item': {
            'uuid': 'aaaa-bbbb',
            'title': 'TOP 100 Germany',
            'description': 'The hits',
            'numberOfTracks': 100,
            'squareImage': '12ab-34cd-56ef',
            'publicPlaylist': True,
            'creator': {'id': 0},
        },
    })
    assert playlist is not None
    assert playlist.id == 'aaaa-bbbb'
    assert playlist.name == 'TOP 100 Germany'
    assert playlist.track_count == 100
    # Editorial rows (creator id 0 / nameless) are labeled Tidal.
    assert playlist.owner == 'Tidal'
    assert playlist.image_url == 'https://resources.tidal.com/images/12ab/34cd/56ef/640x640.jpg'
    assert playlist.tracks == []  # on-demand, like the owned listing


def test_flat_row_and_named_creator():
    playlist = TidalClient._favorite_playlist_from_item({
        'uuid': 'cccc-dddd',
        'title': 'Friend Mix',
        'creator': {'id': 777, 'name': 'somefriend'},
    })
    assert playlist is not None
    assert playlist.owner == 'somefriend'
    assert playlist.track_count == 0
    # No image GUID → the attribute is never set (the endpoint reads it
    # via getattr(p, 'image_url', None), like the owned listing's rows).
    assert getattr(playlist, 'image_url', None) is None


def test_row_without_uuid_is_skipped():
    assert TidalClient._favorite_playlist_from_item({'item': {'title': 'ghost'}}) is None
