"""#1901: paste a link or id for any source, not only MusicBrainz and Deezer.

Ktzenjammer had eleven artists called "1905" on Spotify and no way to pick the
right one from names alone, while its id sat in the address bar. The detector
must spot every source's link shapes, never mistake an ordinary word for an id,
and a lookup that misses must fall back to the normal search.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.library.direct_id import extract_direct_id, parse_direct_ref

SPOTIFY_ID = "66oNCpEovvkyBASCRLaI93"


# ── the detector ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("service,query,expected", [
    # spotify: web link (with intl prefix), uri, bare id
    ("spotify", f"https://open.spotify.com/artist/{SPOTIFY_ID}?si=abc", ("artist", SPOTIFY_ID)),
    ("spotify", f"https://open.spotify.com/intl-de/album/{SPOTIFY_ID}", ("album", SPOTIFY_ID)),
    ("spotify", f"spotify:track:{SPOTIFY_ID}", ("track", SPOTIFY_ID)),
    ("spotify", SPOTIFY_ID, (None, SPOTIFY_ID)),
    # apple: an album link with ?i= is that song
    ("itunes", "https://music.apple.com/us/artist/1905/1234567", ("artist", "1234567")),
    ("itunes", "https://music.apple.com/us/album/some-album/111222?i=333444", ("track", "333444")),
    ("itunes", "https://music.apple.com/gb/song/a-song/555666", ("track", "555666")),
    ("itunes", "https://itunes.apple.com/us/album/x/id987654", ("album", "987654")),
    ("itunes", "987654", (None, "987654")),
    # discogs: album ids are tagged so a master and a release can't be confused
    ("discogs", "https://www.discogs.com/artist/123456-1905", ("artist", "123456")),
    ("discogs", "https://www.discogs.com/release/789-Some-Album", ("album", "r789")),
    ("discogs", "https://www.discogs.com/master/4321-Some-Album", ("album", "m4321")),
    ("discogs", "[m4321]", ("album", "m4321")),
    ("discogs", "[a123456]", ("artist", "123456")),
    # tidal: an album/track link is the track
    ("tidal", "https://tidal.com/browse/artist/7804", ("artist", "7804")),
    ("tidal", "https://listen.tidal.com/album/123/track/456", ("track", "456")),
    # qobuz: open/play links and the store's interpreter (artist) pages
    ("qobuz", "https://open.qobuz.com/album/0060253780968", ("album", "0060253780968")),
    ("qobuz", "https://www.qobuz.com/us-en/interpreter/1905/1234", ("artist", "1234")),
    ("qobuz", "https://www.qobuz.com/gb-en/album/x/abc123xyz", ("album", "abc123xyz")),
    # amazon: asins, and trackAsin makes it the track
    ("amazon", "https://music.amazon.com/albums/B0ABCDEF12", ("album", "B0ABCDEF12")),
    ("amazon", "https://music.amazon.co.uk/albums/B0ABCDEF12?trackAsin=B0ZZZZZZ99", ("track", "B0ZZZZZZ99")),
    ("amazon", "b0abcdef12", (None, "B0ABCDEF12")),
    ("audiodb", "https://www.theaudiodb.com/artist/111239-1905", ("artist", "111239")),
    ("genius", "12345", (None, "12345")),
    ("jiosaavn", "https://www.jiosaavn.com/album/some-album/AbC_dEf12", ("album", "AbC_dEf12")),
    ("jiosaavn", "https://www.jiosaavn.com/song/x/Zy-9", ("track", "Zy-9")),
    # last.fm and bandcamp are addressed by url
    ("lastfm", "https://www.last.fm/music/1905", ("artist", "https://www.last.fm/music/1905")),
    ("lastfm", "https://www.last.fm/de/music/1905/Some+Album", ("album", "https://www.last.fm/music/1905/Some+Album")),
    ("lastfm", "https://www.last.fm/music/1905/_/Song", ("track", "https://www.last.fm/music/1905/_/Song")),
    ("bandcamp", "https://band.bandcamp.com/album/some-album?from=x", ("album", "https://band.bandcamp.com/album/some-album")),
])
def test_every_source_link_is_recognised(service, query, expected):
    assert parse_direct_ref(service, "artist", query) == expected
    assert extract_direct_id(service, "artist", query) == expected[1]


@pytest.mark.parametrize("service,query", [
    ("spotify", "1905"),
    ("spotify", "Supercalifragilisticex"),      # 22 letters, no digit: a word
    ("spotify", "12345"),
    ("itunes", "1905 band"),
    ("itunes", "12"),
    ("discogs", "Some Album"),
    ("tidal", "https://tidal.com/browse/playlist/abc"),
    ("amazon", "B0ABC"),
    ("genius", "https://genius.com/artists/1905"),   # page name, not id
    ("jiosaavn", "12345"),                          # no bare ids
    ("lastfm", "1905"),
    ("bandcamp", "https://bandcamp.com/discover"),
    ("hydrabase", "12345"),
])
def test_ordinary_words_are_never_ids(service, query):
    assert parse_direct_ref(service, "artist", query) is None
    assert extract_direct_id(service, "artist", query) is None


# ── the lookups, through _search_service ─────────────────────────────────────

def _wire(monkeypatch, attr, client):
    import core.library.service_search as ss
    monkeypatch.setattr(ss, attr, SimpleNamespace(client=client))
    return ss


def test_spotify_artist_id_returns_that_artist_and_never_the_fallback(monkeypatch):
    client = MagicMock()
    client.get_artist.return_value = {
        "id": SPOTIFY_ID, "name": "1905", "genres": ["rock"], "images": [{"url": "img"}],
    }
    ss = _wire(monkeypatch, "spotify_enrichment_worker", client)
    rows = ss._search_service("spotify", "artist", f"https://open.spotify.com/artist/{SPOTIFY_ID}")
    assert rows == [{
        "id": SPOTIFY_ID, "name": "1905", "image": "img",
        "extra": "Direct ID match · rock", "provider": "spotify",
    }]
    client.get_artist.assert_called_once_with(SPOTIFY_ID, allow_fallback=False)
    client.search_artists.assert_not_called()


def test_spotify_track_link_while_matching_an_album_gives_its_album(monkeypatch):
    client = MagicMock()
    client.get_track_details.return_value = {"id": "t", "album": {"id": "AL"}}
    client.get_album.return_value = {"id": "AL", "name": "Album", "artists": [{"name": "1905"}],
                                     "release_date": "2019-03-01", "images": []}
    ss = _wire(monkeypatch, "spotify_enrichment_worker", client)
    rows = ss._search_service("spotify", "album", f"https://open.spotify.com/track/{SPOTIFY_ID}")
    assert rows[0]["id"] == "AL"
    assert rows[0]["extra"] == "Direct ID match · 1905 · 2019"
    client.get_album.assert_called_once_with("AL", allow_fallback=False)


def test_an_id_that_does_not_resolve_falls_back_to_the_search(monkeypatch):
    client = MagicMock()
    client.get_artist.return_value = None
    client.search_artists.return_value = [SimpleNamespace(
        id=SPOTIFY_ID, name="1905 (search)", image_url=None, genres=[])]
    ss = _wire(monkeypatch, "spotify_enrichment_worker", client)
    rows = ss._search_service("spotify", "artist", SPOTIFY_ID)
    assert rows[0]["name"] == "1905 (search)"
    client.search_artists.assert_called_once()


def test_a_lookup_that_raises_is_a_miss_not_an_error(monkeypatch):
    client = MagicMock()
    client.get_artist.side_effect = RuntimeError("401")
    client.search_artists.return_value = []
    ss = _wire(monkeypatch, "spotify_enrichment_worker", client)
    assert ss._search_service("spotify", "artist", SPOTIFY_ID) == []
    client.search_artists.assert_called_once()


def test_discogs_release_link_keeps_the_release_tag(monkeypatch):
    client = MagicMock()
    client.get_album.return_value = {"id": "789", "name": "Some Album", "artists": ["1905"],
                                     "release_date": "2001", "image_url": "c"}
    ss = _wire(monkeypatch, "discogs_worker", client)
    rows = ss._search_service("discogs", "album", "https://www.discogs.com/release/789-Some-Album")
    # the tag is what tells a release from a master that shares its number
    assert rows[0]["id"] == "r789"
    client.get_album.assert_called_once_with("r789", include_tracks=False)


def test_a_link_to_the_wrong_kind_is_not_forced_into_the_match(monkeypatch):
    client = MagicMock()
    client.search_artist.return_value = None
    ss = _wire(monkeypatch, "tidal_enrichment_worker", client)
    # an album link while matching an artist: no direct hit, normal search runs
    assert ss._search_service("tidal", "artist", "https://tidal.com/browse/album/123") == []
    client.get_artist.assert_not_called()
    client.get_album.assert_not_called()


def test_lastfm_stores_lastfms_own_url(monkeypatch):
    client = MagicMock()
    client.get_artist_info.return_value = {
        "name": "1905", "url": "https://www.last.fm/music/1905", "image": []}
    client.get_best_image.return_value = None
    ss = _wire(monkeypatch, "lastfm_worker", client)
    rows = ss._search_service("lastfm", "artist", "https://www.last.fm/de/music/1905")
    assert rows[0]["id"] == "https://www.last.fm/music/1905"
    client.get_artist_info.assert_called_once_with("1905")


def test_amazon_has_no_artist_ids_so_an_asin_searches(monkeypatch):
    client = MagicMock()
    client.search_artists.return_value = []
    ss = _wire(monkeypatch, "amazon_worker", client)
    assert ss._search_service("amazon", "artist", "B0ABCDEF12") == []
    client.get_artist.assert_not_called()
