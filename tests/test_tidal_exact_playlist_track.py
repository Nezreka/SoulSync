"""Regression tests for exact playlist-track downloads from Tidal.

A Tidal playlist row already carries the provider's own track id. Text search
cannot tell two Tidal versions apart when the title, artist, album and
duration are identical (clean vs explicit, original album vs compilation), so
the download must use the id the playlist already named.
"""

import asyncio
import sys
import types


# Stub tidalapi before importing the module: it touches tidalapi at import
# time and the unit tests must not require the package.
if "tidalapi" not in sys.modules:
    _fake = types.ModuleType("tidalapi")

    class _FakeQuality:
        low_96k = "LOW"
        low_320k = "HIGH"
        high_lossless = "LOSSLESS"
        hi_res = "HI_RES"
        hi_res_lossless = "HI_RES_LOSSLESS"

    _fake.Quality = _FakeQuality
    _fake.media = types.SimpleNamespace(Track=object)
    sys.modules["tidalapi"] = _fake


from core.downloads.track_hint import hint_from_track, track_hint_context  # noqa: E402
from core.quality.source_map import quality_from_tidal_tier  # noqa: E402
from core.tidal_download_client import (  # noqa: E402
    QUALITY_MAP,
    TidalDownloadClient,
    _v2_to_track_result,
)

CLEAN = {
    "data": {
        "attributes": {
            "title": "Disconnect",
            "version": None,
            "isrc": "GBUM72306588",
            "duration": "PT2M45S",
            "explicit": False,
        }
    }
}
EXPLICIT = {
    "data": {
        "attributes": {
            "title": "Disconnect",
            "version": None,
            "isrc": "GBUM72306155",
            "duration": "PT2M45S",
            "explicit": True,
        }
    }
}
HINT = {"title": "Disconnect", "artist": "Becky Hill", "album": "Disconnect", "duration_ms": 165000}


def _build(payload, track_id="303656585", hint=HINT):
    key = "lossless"
    return _v2_to_track_result(
        payload,
        track_id,
        QUALITY_MAP[key],
        quality_from_tidal_tier(key),
        hint,
    )


def _hint(**overrides):
    track = {
        "name": "Disconnect",
        "artists": [{"name": "Becky Hill"}],
        "album": {"name": "Disconnect"},
        "source_track_id": "303656585",
        "source_info": {"mirrored_playlist_id": 1},
    }
    track.update(overrides)
    return track


def test_exact_candidate_uses_the_playlist_credits_and_ids():
    result = _build(CLEAN)

    assert result is not None
    assert result.filename == "303656585||Becky Hill - Disconnect"
    assert (result.artist, result.title, result.album) == ("Becky Hill", "Disconnect", "Disconnect")
    assert result.duration == 165000
    assert result._source_metadata["explicit"] is False
    assert result._source_metadata["isrc"] == "GBUM72306588"


def test_exact_candidate_keeps_the_explicit_flag():
    result = _build(EXPLICIT, "303658687")

    assert result is not None
    assert result._source_metadata["explicit"] is True
    assert result._source_metadata["isrc"] == "GBUM72306155"


def test_search_returns_only_the_exact_playlist_track():
    exact = _build(CLEAN)

    client = object.__new__(TidalDownloadClient)
    client.is_available = lambda: True
    client._fetch_track_result_by_id = lambda track_id, hint=None: exact

    with track_hint_context({"tidal_id": "303656585"}):
        tracks, albums = asyncio.run(client.search("Becky Hill Disconnect"))

    assert tracks == [exact]
    assert albums == []


def test_hint_trusts_a_tidal_id_only_from_a_tidal_playlist(monkeypatch):
    import core.downloads.track_hint as th

    th._MIRROR_SOURCE_CACHE.clear()
    monkeypatch.setattr(
        th,
        "_mirrored_playlist_source",
        lambda mid: "tidal" if int(mid) == 1 else "deezer",
    )

    assert hint_from_track(_hint())["tidal_id"] == "303656585"
    assert hint_from_track(_hint(source_info={"mirrored_playlist_id": 2}))["tidal_id"] is None
    # Some wishlist rows only carry the pipeline's auto_mirror_<id> form.
    assert hint_from_track(_hint(source_info={"playlist_id": "auto_mirror_1"}))["tidal_id"] == "303656585"
    # A Spotify id is not a Tidal id.
    assert hint_from_track(_hint(source_track_id="4uLU6hMCjMI75M1A2tKUQC"))["tidal_id"] is None


def test_hint_reads_the_native_id_from_a_formatted_wishlist_task(monkeypatch):
    import core.downloads.track_hint as th

    th._MIRROR_SOURCE_CACHE.clear()
    monkeypatch.setattr(th, "_mirrored_playlist_source", lambda mid: "tidal")

    formatted = {
        "id": "deezer-metadata-id",
        "name": "Disconnect",
        "artists": [{"name": "Becky Hill"}],
        "album": {"name": "Disconnect"},
        "duration_ms": 165000,
        "source_info": {"mirrored_playlist_id": 1},
        "track_data": {"source_track_id": "303656585", "duration_ms": 165000},
    }
    assert hint_from_track(formatted)["tidal_id"] == "303656585"


def test_hint_reads_the_source_from_a_direct_playlist_batch(monkeypatch):
    import core.downloads.track_hint as th

    th._MIRROR_SOURCE_CACHE.clear()
    monkeypatch.setattr(th, "_mirrored_playlist_source", lambda mid: "")

    track = _hint(source_info={"playlist_id": "some-playlist", "source": "tidal"})
    assert hint_from_track(track)["tidal_id"] == "303656585"


def test_two_native_ids_sharing_a_metadata_id_stay_distinct():
    from core.sync.wishlist_readd import build_original_tracks_map

    rows = [
        {"id": "deezer-metadata-id", "source_track_id": "303656585", "name": "Disconnect"},
        {"id": "deezer-metadata-id", "source_track_id": "303658687", "name": "Disconnect"},
    ]
    assert set(build_original_tracks_map(rows)) == {"303656585", "303658687"}
