"""Amazon (T2Tunes) search-response parsing across upstream generations (#1033).

The t2tunes.site backend migrated around 2026-07: the typesense search shape
{results:[{hits:[{document:{...}}]}]} became GraphQL
{data:{searchTracks:{edges:[{node:{...}}]}}} with renamed fields, which made
every Amazon search (and therefore every Amazon download) come back empty.
The album metadata shape also moved artistName -> artist.name /
primaryArtistName and the release date -> originalReleaseDate/streetDate.

Fixtures below are trimmed from live t2tunes.site responses captured while
diagnosing the issue. Both generations must parse so self-hosted instances
running the old backend keep working.
"""

from __future__ import annotations

from core.amazon_client import (
    AmazonClient,
    _meta_artist_name,
    _meta_release_date,
)

GRAPHQL_SEARCH = {
    "data": {
        "searchTracks": {
            "edgeCount": 2,
            "edges": [
                {"node": {
                    "id": "B08QDQF8CL",
                    "title": "Hello",
                    "duration": 296,
                    "trackNumber": 1,
                    "releaseDate": "2015-10-23T00:00:00.000Z",
                    "album": {"id": "B08QDQN6M6", "title": "25",
                              "releaseDate": "2015-11-20T00:00:00.000Z"},
                    "contributingArtists": {
                        "concatenatedName": "Adele",
                        "edges": [{"role": "PRIMARY",
                                   "node": {"id": "B0014DEFTE", "name": "Adele"}}],
                    },
                }},
                {"node": {
                    "id": "B0TRACK2",
                    "title": "Skyfall",
                    "duration": 286,
                    "album": {"id": "B0ALBUM2", "title": "Skyfall"},
                    "contributingArtists": {"concatenatedName": "",
                                            "edges": [{"node": {"name": "Adele"}}]},
                }},
            ],
            "pageInfo": {"hasNextPage": True, "token": "tcs-..."},
        }
    }
}

LEGACY_SEARCH = {
    "results": [{
        "hits": [{
            "document": {
                "asin": "B08QDQF8CL", "title": "Hello", "artistName": "Adele",
                "__type": "track", "albumName": "25", "albumAsin": "B08QDQN6M6",
                "duration": 296, "isrc": "GBBKS1500214",
            }
        }]
    }]
}

NEW_ALBUM_META = {
    "asin": "B08QDQN6M6",
    "title": "25",
    "trackCount": 11,
    "image": "https://m.media-amazon.com/images/I/41f8b3u48ML.jpg",
    "artist": {"asin": "B0014DEFTE", "name": "Adele"},
    "primaryArtistName": "Adele",
    "originalReleaseDate": "2015-11-20T00:00:00.000Z",
    "label": "XL Recordings",
}


def test_graphql_shape_parses_tracks():
    items = list(AmazonClient._iter_search_items(GRAPHQL_SEARCH))
    assert len(items) == 2
    hello = items[0]
    assert hello.asin == "B08QDQF8CL"
    assert hello.title == "Hello"
    assert hello.artist_name == "Adele"
    assert hello.is_track and not hello.is_album
    assert hello.album_name == "25"
    assert hello.album_asin == "B08QDQN6M6"
    assert hello.duration_seconds == 296


def test_graphql_artist_falls_back_to_edges():
    items = list(AmazonClient._iter_search_items(GRAPHQL_SEARCH))
    assert items[1].artist_name == "Adele"     # concatenatedName empty -> edge node


def test_legacy_shape_still_parses():
    items = list(AmazonClient._iter_search_items(LEGACY_SEARCH))
    assert len(items) == 1
    assert items[0].asin == "B08QDQF8CL"
    assert items[0].artist_name == "Adele"
    assert items[0].isrc == "GBBKS1500214"
    assert items[0].is_track


def test_empty_and_malformed_shapes_yield_nothing():
    assert list(AmazonClient._iter_search_items({})) == []
    assert list(AmazonClient._iter_search_items({"data": {"searchTracks": {}}})) == []
    assert list(AmazonClient._iter_search_items({"data": {"searchTracks": {"edges": [{}]}}})) == []


def test_meta_artist_name_across_generations():
    assert _meta_artist_name({"artistName": "Adele"}) == "Adele"          # legacy
    assert _meta_artist_name(NEW_ALBUM_META) == "Adele"                    # new
    assert _meta_artist_name({"artist": {"name": "Adele"}}) == "Adele"
    assert _meta_artist_name({}) == ""


def test_meta_release_date_across_generations():
    assert _meta_release_date({"releaseDate": "2015-11-20"}) == "2015-11-20"
    assert _meta_release_date(NEW_ALBUM_META) == "2015-11-20"              # ISO trimmed
    assert _meta_release_date({"streetDate": "2015-11-20T00:00:00.000Z"}) == "2015-11-20"
    assert _meta_release_date({}) == ""


def test_search_tracks_end_to_end_with_graphql_shape(monkeypatch):
    import core.amazon_client as mod
    monkeypatch.setattr(mod, "_rate_limit", lambda: None)

    client = AmazonClient.__new__(AmazonClient)
    client.country = "US"
    client.preferred_codec = "flac"
    monkeypatch.setattr(client, "_get_json", lambda path, params=None: GRAPHQL_SEARCH)
    monkeypatch.setattr(client, "_fetch_album_metas",
                        lambda asins: {"B08QDQN6M6": NEW_ALBUM_META})

    tracks = client.search_tracks("adele hello", limit=5)
    assert [t.name for t in tracks] == ["Hello", "Skyfall"]
    assert tracks[0].artists == ["Adele"]
    assert tracks[0].duration_ms == 296000
    assert tracks[0].release_date == "2015-11-20"      # backfilled from album meta
    assert tracks[0].image_url == NEW_ALBUM_META["image"]


def test_meta_release_date_converts_epochs():
    assert _meta_release_date({"originalReleaseDate": 1572516000}) == "2019-10-31"
    assert _meta_release_date({"originalReleaseDate": "1572516000"}) == "2019-10-31"
    assert _meta_release_date({"originalReleaseDate": 1572516000000}) == "2019-10-31"  # millis
