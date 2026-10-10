"""Discogs detail dates survive client, typed-provider and cache boundaries."""
import pytest
from core.metadata.types import Album as TypedAlbum
from core.discogs_client import Album, Track, DiscogsClient
from core.metadata.cache import MetadataCache


@pytest.mark.parametrize("released,year,expected", [
    ("2008-02-11", 2008, "2008-02-11"),
    ("2008-02-00", 2008, "2008-02"),
    ("2008-00-00", 2008, "2008"),
    ("2008-02-11", 0, "2008-02-11"),
    (None, 2008, "2008"),
    ("", 0, ""),
    ("unknown", 2008, "2008"),
    ("2008-02-31", 2008, "2008"),
])
def test_discogs_converters_preserve_available_date_precision(released, year, expected):
    raw = {"id": 7598551, "title": "Thriller 25 Super Deluxe Edition", "year": year,
           "released": released, "artists": [{"name": "Michael Jackson"}]}
    assert TypedAlbum.from_discogs_dict(raw).release_date == expected
    assert Album.from_discogs_release(raw).release_date == expected
    assert Track.from_discogs_track({"title": "Thriller", "position": "4"}, raw).release_date == (expected or None)


def test_discogs_album_cache_retains_full_released_date():
    cache = MetadataCache()
    raw = {"id": 7598551, "title": "Thriller 25 Super Deluxe Edition", "year": 2008,
           "released": "2008-02-11", "artists": [{"name": "Michael Jackson"}]}
    cache.store_entity("discogs", "album", "r7598551", raw)
    assert cache.get_entity_detail("discogs", "album", "r7598551")["release_date"] == "2008-02-11"


def test_discogs_tracklist_retains_the_owning_release_date(monkeypatch):
    client = DiscogsClient(token="test")
    monkeypatch.setattr(client, "_api_get", lambda *args, **kwargs: {
        "id": 7598551, "title": "Thriller 25 Super Deluxe Edition", "year": 2008,
        "released": "2008-02-11", "artists": [{"name": "Michael Jackson"}],
        "tracklist": [{"title": "Thriller", "position": "4", "type_": "track"}],
    })
    result = client.get_album_tracks("r7598551")
    assert result["items"][0]["album"]["release_date"] == "2008-02-11"
