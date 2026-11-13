"""#2066 (Ktzenjammer): watching Hypno5e downloaded Amadou & Mariam's catalogue.

Deezer's ids are whole numbers, and its api does not reject anything else: it
reads the leading digits. Hypno5e's Spotify id starts "5b...", so
``/artist/5bPOuhK95xuq1U.../albums`` is answered as artist 5, Amadou & Mariam.
An id from another source must never reach Deezer, so the one function every
Deezer call goes through refuses it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.deezer_client import DeezerClient, _deezer_id_is_usable


@pytest.fixture
def client():
    c = DeezerClient.__new__(DeezerClient)
    c.session = MagicMock()
    c._access_token = None
    return c


@pytest.mark.parametrize("endpoint", [
    "artist/5bPOuhK95xuq1Uabcdefgh/albums",       # a spotify id that starts with a digit
    "/artist/5bPOuhK95xuq1Uabcdefgh",
    "artist/f0eed6bf-9b1b-4454-8595-ef9d990883d8/top",   # a musicbrainz id
    "album/3x-not-a-number/tracks",
    "track/12abc",
    "playlist/37i9dQZF1DXcBWIGoYBM5M",
    "artist//albums",
    "artist/None/albums",
])
def test_an_id_that_is_not_deezers_never_leaves_the_app(client, endpoint):
    assert _deezer_id_is_usable(endpoint) is False
    assert client._api_get(endpoint) is None
    client.session.get.assert_not_called()


@pytest.mark.parametrize("endpoint", [
    "artist/54305/albums", "/artist/5", "album/302127/tracks", "track/3135556",
    "playlist/908622995", "track/isrc:GBAAA9000001", "/track/isrc:GBAAA9000001",
    "album/upc:602537347377",
    # nothing addressed by id: left alone
    "search/artist", "genre", "chart/0/playlists", "user/me/artists", "search/track",
])
def test_deezers_own_ids_and_id_free_endpoints_pass(endpoint):
    assert _deezer_id_is_usable(endpoint) is True


def test_the_artist_albums_call_asks_nothing_for_a_spotify_id(client):
    assert client.get_artist_albums_list("5bPOuhK95xuq1Uabcdefgh") == []
    client.session.get.assert_not_called()
