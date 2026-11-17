"""release type by the store rule, not track count alone.

discord report (nov 16): pink floyd's wish you were here (5 tracks, 44 min)
showed as an EP. every converter that had no explicit type guessed 1-3 tracks
a single and 4-6 an ep regardless of length. the rule the stores use: 1-3
tracks under 30 minutes a single, 4-6 under 30 an ep, else an album, and a
provider's own explicit type always wins.
"""

from __future__ import annotations

from core.metadata.release_type import (
    itunes_release_type,
    release_type_from_shape,
    title_says_ep,
    tracklist_runtime_ms,
)

MIN = 60 * 1000
# wish you were here: 13:32, 7:30, 5:24, 5:21, 12:29 (44 min)
WYWH_MS = [812000, 450000, 324000, 321000, 749000]
# close to the edge: 18:42, 10:09, 8:57 (38 min)
CTTE_MS = [1122000, 609000, 537000]
# a real ep: 5 tracks of 4 minutes
SHORT_EP_MS = [4 * MIN] * 5


def _spotify_tracks(durations):
    return [{"id": f"t{i}", "name": f"Track {i}", "track_number": i + 1,
             "duration_ms": ms, "artists": [{"name": "Pink Floyd"}]}
            for i, ms in enumerate(durations)]


# -- the rule ----------------------------------------------------------------

def test_store_rule_by_count_and_runtime():
    assert release_type_from_shape(5, sum(WYWH_MS)) == "album"
    assert release_type_from_shape(3, sum(CTTE_MS)) == "album"
    assert release_type_from_shape(5, sum(SHORT_EP_MS)) == "ep"
    assert release_type_from_shape(2, 7 * MIN) == "single"
    assert release_type_from_shape(9, 20 * MIN) == "album"
    # runtime unknown: count alone, unknown count: album
    assert release_type_from_shape(5) == "ep"
    assert release_type_from_shape(2) == "single"
    assert release_type_from_shape(0) == "album"
    assert release_type_from_shape(None, 50 * MIN) == "album"


def test_runtime_needs_the_whole_tracklist():
    assert tracklist_runtime_ms(_spotify_tracks(WYWH_MS)) == sum(WYWH_MS)
    # a partial tracklist would read short and call an album an ep
    assert tracklist_runtime_ms(_spotify_tracks(WYWH_MS[:3]), 5) is None
    assert tracklist_runtime_ms(_spotify_tracks([1000, 0, 1000])) is None
    assert tracklist_runtime_ms([]) is None
    # deezer seconds and discogs clocks only when the caller says so
    assert tracklist_runtime_ms([{"duration": 300}], seconds=True) == 300000
    assert tracklist_runtime_ms([{"duration": "13:32"}], seconds=True) == 812000
    assert tracklist_runtime_ms([{"duration": 300}]) is None


def test_title_says_ep():
    assert title_says_ep("The Heat EP")
    assert title_says_ep("Hope For Something EP (Remixes)")
    assert title_says_ep("The Def Leppard E.P.")
    assert not title_says_ep("Wish You Were Here")
    assert not title_says_ep("Deep Sleep")


# -- itunes: apple names its own singles and eps --------------------------------

def test_itunes_reads_apples_own_suffix():
    assert itunes_release_type("Wish You Were Here", "Album", 5) == "album"
    assert itunes_release_type("Hi Scores - EP", "Album", 6) == "ep"
    assert itunes_release_type("Money - Single", "Album", 1) == "single"
    assert itunes_release_type("Take Five - Single (Previously Unreleased)", "Album", 1) == "single"
    assert itunes_release_type("Best Of", "Compilation", 20) == "compilation"
    # no name to read: the store rule
    assert itunes_release_type("", "Album", 5, sum(WYWH_MS)) == "album"


def test_itunes_client_album_and_track_shapes():
    from core.itunes_client import Album, Track

    raw = {"collectionId": 1, "collectionName": "Wish You Were Here", "trackCount": 5,
           "collectionType": "Album", "artistName": "Pink Floyd"}
    assert Album.from_itunes_album(raw).album_type == "album"
    ep = dict(raw, collectionName="Hi Scores - EP", trackCount=6)
    assert Album.from_itunes_album(ep).album_type == "ep"
    track = {"trackId": 9, "trackName": "Have a Cigar", "collectionName": "Wish You Were Here",
             "trackCount": 5, "trackTimeMillis": 324000}
    assert Track.from_itunes_track(track).album_type == "album"


def test_typed_itunes_converter():
    from core.metadata.types import Album

    raw = {"collectionId": 1, "collectionName": "Wish You Were Here", "trackCount": 5}
    assert Album.from_itunes_dict(raw).album_type == "album"
    assert Album.from_itunes_dict(dict(raw, collectionName="Thing - EP")).album_type == "ep"
    # the itunes client's normalized dict: its raw rides along in _raw_data
    normalized = {"id": "1", "name": "Wish You Were Here", "total_tracks": 5,
                  "album_type": "album", "_raw_data": raw}
    assert Album.from_itunes_dict(normalized).album_type == "album"


# -- deezer / spotify typed converters -----------------------------------------

def test_deezer_record_type_is_trusted_album_included():
    from core.metadata.types import Album

    raw = {"id": 1, "title": "Wish You Were Here", "record_type": "album", "nb_tracks": 5,
           "artist": {"id": 2, "name": "Pink Floyd"}}
    assert Album.from_deezer_dict(raw).album_type == "album"
    assert Album.from_deezer_dict(dict(raw, record_type="ep", nb_tracks=9)).album_type == "ep"


def test_deezer_normalized_filler_goes_by_runtime():
    from core.metadata.types import Album

    normalized = {"id": 1, "title": "Wish You Were Here", "album_type": "album",
                  "total_tracks": 5, "artist": {"name": "Pink Floyd"}}
    assert Album.from_deezer_dict(dict(normalized, duration=2656)).album_type == "album"
    assert Album.from_deezer_dict(dict(normalized, duration=1200)).album_type == "ep"
    # no runtime anywhere: count alone, as before
    assert Album.from_deezer_dict(normalized).album_type == "ep"


def test_spotify_filler_album_goes_by_tracklist_runtime():
    from core.metadata.types import Album

    raw = {"id": "sp1", "name": "Wish You Were Here", "album_type": "album",
           "total_tracks": 5, "artists": [{"id": "a", "name": "Pink Floyd"}],
           "tracks": {"items": _spotify_tracks(WYWH_MS)}}
    assert Album.from_spotify_dict(raw).album_type == "album"
    short = dict(raw, tracks={"items": _spotify_tracks(SHORT_EP_MS)})
    assert Album.from_spotify_dict(short).album_type == "ep"
    # an explicit type wins over the runtime
    assert Album.from_spotify_dict(dict(raw, album_type="single")).album_type == "single"


def test_album_tracks_payload_uses_the_runtime():
    from core.metadata.album_tracks import _build_album_tracks_payload

    raw = {"id": "x1", "name": "Wish You Were Here", "total_tracks": 5,
           "artists": [{"name": "Pink Floyd"}], "album_type": "album"}
    out = _build_album_tracks_payload(raw, {"items": _spotify_tracks(WYWH_MS)}, "spotify", "x1")
    assert out["album"]["album_type"] == "album"
    assert out["album"]["duration_ms"] == sum(WYWH_MS)
    out = _build_album_tracks_payload(raw, {"items": _spotify_tracks(SHORT_EP_MS)}, "spotify", "x1")
    assert out["album"]["album_type"] == "ep"


def test_discogs_tracklist_durations():
    from core.discogs_client import Album

    release = {"id": 5, "title": "Pink Floyd - Wish You Were Here",
               "tracklist": [{"type_": "track", "duration": f"{ms // 60000}:{ms // 1000 % 60:02d}"}
                             for ms in WYWH_MS]}
    assert Album.from_discogs_release(release).album_type == "album"
    # discogs' own format word wins
    ep = dict(release, formats=[{"name": "Vinyl", "descriptions": ["EP"]}])
    assert Album.from_discogs_release(ep).album_type == "ep"


# -- folders, watchlist, wishlist seeding ------------------------------------------

def test_albumtype_folder_uses_the_runtime():
    from core.imports.paths import get_album_type_display

    assert get_album_type_display("album", 5, "deezer", total_ms=sum(WYWH_MS)) == "Album"
    assert get_album_type_display("album", 3, "deezer", total_ms=sum(CTTE_MS)) == "Album"
    assert get_album_type_display("album", 5, "deezer", total_ms=sum(SHORT_EP_MS)) == "EP"
    assert get_album_type_display("album", 5, "deezer") == "EP"


def test_watchlist_release_types_use_the_runtime():
    from types import SimpleNamespace

    from core.watchlist_scanner import WatchlistScanner

    scanner = WatchlistScanner.__new__(WatchlistScanner)
    artist = SimpleNamespace(include_albums=True, include_eps=False, include_singles=False)
    assert scanner._should_include_release(5, artist, sum(WYWH_MS))
    assert not scanner._should_include_release(5, artist, sum(SHORT_EP_MS))
    assert not scanner._should_include_release(5, artist)


def test_wishlist_payload_without_a_type():
    from core.library2.importer import _album_type_from_payload

    album = {"name": "Wish You Were Here", "tracks": {"items": _spotify_tracks(WYWH_MS)}}
    assert _album_type_from_payload(album, 5) == "album"
    assert _album_type_from_payload({"name": "Short"}, 5) == "ep"
    assert _album_type_from_payload({"album_type": "single"}, 5) == "single"
