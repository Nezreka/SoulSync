"""collab albums get every album artist in the file, not just one.

TPE2 / albumartist hold one display string, so navidrome filed watch the
throne under one artist. with write_multi_artist on, the full list now also
goes where navidrome reads it: txxx:album artists (id3) and albumartists
(vorbis). mp4 has no tag navidrome reads for it, so mp4 is left alone.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mutagen")
from mutagen.flac import FLAC  # noqa: E402

import core.metadata.artwork as artwork  # noqa: E402
import core.metadata.enrichment as enrichment  # noqa: E402
from core.metadata import source as src_module  # noqa: E402
from core.metadata.source import _album_artist_names  # noqa: E402
from tests.test_enrichment_art_preservation import _make_flac_with_art  # noqa: E402


def _cfg(write_multi):
    values = {"metadata_enhancement.tags.write_multi_artist": write_multi,
              "metadata_enhancement.embed_album_art": False}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    return cfg


def test_names_only_for_a_real_collab():
    assert _album_artist_names([{"name": "JAY-Z"}, {"name": "Kanye West"}]) == ["JAY-Z", "Kanye West"]
    assert _album_artist_names(["JAY-Z", "Kanye West", "JAY-Z"]) == ["JAY-Z", "Kanye West"]
    # one artist, or a placeholder, is not a collab
    assert _album_artist_names([{"name": "Radiohead"}]) == []
    assert _album_artist_names([{"name": "Radiohead"}, {"name": "Unknown Artist"}]) == []
    assert _album_artist_names(None) == []


def _extract(album_artists, track_artists):
    context = {
        "original_search_result": {"title": "No Church in the Wild",
                                   "artists": [{"name": a} for a in track_artists]},
        "spotify_album": {"name": "Watch the Throne", "artists": [{"name": a} for a in album_artists]},
        "source": "spotify",
    }
    with patch.object(src_module, "get_config_manager", return_value=_cfg(True)):
        return src_module.extract_source_metadata(
            context, {"name": track_artists[0]}, {"album_name": "Watch the Throne"})


def test_album_context_with_two_artists_carries_the_list():
    meta = _extract(["JAY-Z", "Kanye West"], ["JAY-Z", "Kanye West", "Frank Ocean"])
    assert meta["_album_artists_list"] == ["JAY-Z", "Kanye West"]


def test_a_feature_does_not_make_it_a_collab_album():
    """track artists aren't album artists."""
    meta = _extract(["Calvin Harris"], ["Calvin Harris", "Rihanna"])
    assert meta["_album_artists_list"] == []


@pytest.fixture
def flac_path(tmp_path):
    path = str(tmp_path / "wtt.flac")
    _make_flac_with_art(path)
    return path


def _write(flac_path, write_multi, album_artists):
    metadata = {"title": "No Church in the Wild", "artist": "JAY-Z, Kanye West",
                "album_artist": "JAY-Z", "album": "Watch the Throne", "track_number": 1,
                "_artists_list": ["JAY-Z", "Kanye West"], "_album_artists_list": album_artists,
                "album_art_url": ""}
    with patch.object(enrichment, "get_config_manager", return_value=_cfg(write_multi)), \
            patch.object(artwork, "get_config_manager", return_value=_cfg(write_multi)), \
            patch.object(enrichment, "strip_all_non_audio_tags"), \
            patch.object(enrichment, "extract_source_metadata", return_value=dict(metadata)), \
            patch.object(enrichment, "embed_source_ids"), \
            patch.object(enrichment, "verify_metadata_written", return_value=True):
        enrichment.enhance_file_metadata(flac_path, context={"track_info": {}},
                                         artist={"name": "JAY-Z"}, album_info={})
    return FLAC(flac_path)


def test_real_flac_gets_albumartists_with_the_setting_on(flac_path):
    audio = _write(flac_path, True, ["JAY-Z", "Kanye West"])
    assert audio.get("albumartists") == ["JAY-Z", "Kanye West"]
    # the display string is untouched
    assert audio.get("albumartist") == ["JAY-Z"]


def test_setting_off_writes_no_albumartists(flac_path):
    assert _write(flac_path, False, ["JAY-Z", "Kanye West"]).get("albumartists") is None


def test_single_album_artist_writes_no_albumartists(flac_path):
    assert _write(flac_path, True, []).get("albumartists") is None


def _make_mp3(path):
    """a few silent mpeg-1 layer 3 frames (128k, 44.1k), enough for mutagen."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    with open(path, "wb") as f:
        f.write(frame * 20)


def test_real_mp3_gets_the_frame_navidrome_reads(tmp_path):
    """navidrome's mappings.yaml: albumartists aliases [txxx:album artists,
    albumartists]."""
    from mutagen.id3 import ID3
    path = str(tmp_path / "wtt.mp3")
    _make_mp3(path)
    metadata = {"title": "No Church in the Wild", "artist": "JAY-Z, Kanye West",
                "album_artist": "JAY-Z", "album": "Watch the Throne", "track_number": 1,
                "_artists_list": ["JAY-Z", "Kanye West"],
                "_album_artists_list": ["JAY-Z", "Kanye West"], "album_art_url": ""}
    with patch.object(enrichment, "get_config_manager", return_value=_cfg(True)), \
            patch.object(artwork, "get_config_manager", return_value=_cfg(True)), \
            patch.object(enrichment, "strip_all_non_audio_tags"), \
            patch.object(enrichment, "extract_source_metadata", return_value=dict(metadata)), \
            patch.object(enrichment, "embed_source_ids"), \
            patch.object(enrichment, "verify_metadata_written", return_value=True):
        enrichment.enhance_file_metadata(path, context={"track_info": {}},
                                         artist={"name": "JAY-Z"}, album_info={})
    tags = ID3(path)
    assert tags.getall("TXXX:Album Artists")[0].text == ["JAY-Z", "Kanye West"]
    assert tags.getall("TPE2")[0].text == ["JAY-Z"]
