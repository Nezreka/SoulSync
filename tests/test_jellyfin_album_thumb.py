"""JellyfinAlbum must expose a cover-image thumb so the library scan stores
albums.thumb_url (mirroring JellyfinArtist). Without it the whole library reads
back with empty album thumbs — blank art in the UI + the Cover Art Filler
flagging every album as "missing cover art".
"""

from __future__ import annotations

from core.jellyfin_client import JellyfinAlbum, JellyfinArtist


class _Client:
    pass


def test_album_has_primary_image_thumb():
    alb = JellyfinAlbum({'Id': 'abc123', 'Name': 'For You'}, _Client())
    assert alb.thumb == '/Items/abc123/Images/Primary'


def test_album_thumb_none_without_id():
    alb = JellyfinAlbum({'Name': 'No Id Album'}, _Client())
    assert alb.thumb is None


def test_album_thumb_matches_artist_shape():
    # Same URL shape the artist uses (already proven to display) — just the
    # album's own item id.
    art = JellyfinArtist({'Id': 'xyz', 'Name': 'A'}, _Client())
    alb = JellyfinAlbum({'Id': 'xyz', 'Name': 'B'}, _Client())
    assert alb.thumb == art.thumb
