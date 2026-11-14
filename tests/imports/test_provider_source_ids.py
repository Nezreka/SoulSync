"""What leaves the import pipeline as an id is an id the provider would know.

Nov 13 2026, found in the log ("Deezer: refusing 'track/4348466402::1121691992',
that is not a Deezer id") and then on disk: a download that came from the
wishlist carried its wishlist row key where the track id goes, and one with no
artist match carried the 'explicit_artist' placeholder. Both were written into
the file (DEEZER_TRACK_ID = "4348466402::1121691992", DEEZER_ARTIST_ID =
"explicit_artist"), the composite reached lib2_tracks.spotify_id, and the
Deezer lookup for the track's other credited artists was refused, so wishlist
downloads never got their featured artists tagged.

The wishlist itself still needs the row key to find the row a download came
from, so `get_import_source_ids` is unchanged and the provider-facing readers
use `get_provider_source_ids`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.context_sentinels import provider_id
from core.imports.context import get_import_source_ids, get_provider_source_ids


@pytest.mark.parametrize("raw, clean", [
    ("4348466402::1121691992", "4348466402"),
    ("4348466402", "4348466402"),
    (4348466402, "4348466402"),
    ("  6rqhFgbbKwnb9MLmUQDhG6::2noRn2Aes5aoNVsU6iWThc ", "6rqhFgbbKwnb9MLmUQDhG6"),
    ("b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d", "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"),
    ("explicit_artist", ""), ("Explicit_Album", ""), ("auto_import", ""),
    ("from_sync_modal", ""), ("staging", ""), ("library_v2", ""), ("wishlist_album", ""),
    ("_name_some album", ""), ("lib2-track:55", ""), ("lib2-album:9", ""),
    ("explicit_artist::1121691992", ""), ("::1121691992", ""),
    ("", ""), (None, ""), ("   ", ""),
])
def test_provider_id(raw, clean):
    assert provider_id(raw) == clean


def _wishlist_download():
    """The shape a scheduled wishlist download reaches post-processing in."""
    return {
        "source": "deezer",
        "track_info": {"id": "4348466402::1121691992", "name": "House Music Freak",
                       "artists": [{"name": "Solardo"}]},
        "original_search_result": {"title": "House Music Freak",
                                   "artists": [{"name": "Solardo"}]},
        "artist": {"id": "explicit_artist", "name": "Solardo"},
        "album": {"id": "wishlist_album", "name": "House Music Freak"},
    }


def test_the_wishlist_still_reads_its_own_row_key():
    assert get_import_source_ids(_wishlist_download())["track_id"] == "4348466402::1121691992"


def test_provider_facing_readers_get_the_bare_track_id_and_no_placeholders():
    assert get_provider_source_ids(_wishlist_download()) == {
        "track_id": "4348466402", "artist_id": "", "album_id": ""}


def test_real_ids_pass_through_untouched():
    context = {"source": "deezer",
               "track_info": {"id": "111", "name": "T"},
               "artist": {"id": "222", "name": "A"},
               "album": {"id": "333", "name": "B"}}
    assert get_provider_source_ids(context) == get_import_source_ids(context) == {
        "track_id": "111", "artist_id": "222", "album_id": "333"}


def _cfg():
    values = {"metadata_enhancement.tags.artist_separator": "; "}
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_the_file_is_tagged_with_the_provider_id_and_deezer_is_asked_for_it():
    from core.metadata import source as src

    deezer = SimpleNamespace(get_track_details=MagicMock(return_value={
        "id": "4348466402", "name": "House Music Freak",
        "artists": ["Solardo", "Guest"]}))
    context = _wishlist_download()
    with patch.object(src, "get_config_manager", return_value=_cfg()), \
         patch("core.metadata.get_deezer_client", return_value=deezer):
        meta = src.extract_source_metadata(context, context["artist"], {})
        tags = src._collect_source_ids(meta, _cfg())

    deezer.get_track_details.assert_called_once_with("4348466402")
    assert meta["_artists_list"] == ["Solardo", "Guest"]
    assert meta["source_track_id"] == "4348466402"
    assert meta["source_artist_id"] == "" and meta["source_album_id"] == ""
    assert tags.get("DEEZER_TRACK_ID") == "4348466402"
    assert "DEEZER_ARTIST_ID" not in tags
    assert not any("::" in str(value) or "explicit" in str(value) for value in tags.values())


def test_a_musicbrainz_wishlist_download_keeps_a_valid_recording_id():
    from core.imports.side_effects import _recording_identity

    mbid = "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
    context = {"source": "musicbrainz",
               "track_info": {"id": f"{mbid}::f5093c06-23e3-404f-aeaa-40f72885ee3a", "name": "T"}}
    assert _recording_identity(context)[0] == mbid
