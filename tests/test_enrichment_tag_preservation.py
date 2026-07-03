"""Sokhi: tracks occasionally land 'untagged' after a processing failure.

enhance_file_metadata clears the file's tags and saves it UP FRONT (so stale
tags never linger), then does the failure-prone enrichment (external source-id
embed, cover-art fetch) and saves again at the end. The core tags
(album/artist/title/track) come from the already-matched context and are written
to the in-memory object BEFORE those external steps — but the on-disk file is
still the cleared one until the final save.

The #764 fix made the error handler restore ART, but it gated the re-save on
there being original art to restore. So a file with NO embedded art that hit a
mid-enrichment crash had its in-memory core tags thrown away and was left on disk
exactly as the up-front clear saved it: UNTAGGED.

These tests run the REAL enhance_file_metadata against a REAL art-less FLAC and
assert the core tags survive a crash in the external step.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

pytest.importorskip("mutagen")
from mutagen.flac import FLAC  # noqa: E402

import core.metadata.enrichment as enrichment  # noqa: E402


class _Cfg:
    def get(self, key, default=None):
        return default


def _make_flac_no_art(path):
    minimal = (
        b"fLaC"
        + b"\x80\x00\x00\x22"
        + b"\x00\x10\x00\x10"
        + b"\x00\x00\x00\x00\x00\x00"
        + b"\x0a\xc4\x42\xf0\x00\x00\x00\x00"
        + b"\x00" * 16
    )
    with open(path, "wb") as f:
        f.write(minimal)
    FLAC(path).save()   # valid FLAC, no tags, no pictures


@pytest.fixture
def flac_path():
    fd, path = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    _make_flac_no_art(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


_CORE = {"title": "Yellow", "artist": "Coldplay", "album_artist": "Coldplay",
         "album": "Parachutes", "track_number": 1, "total_tracks": 9, "disc_number": 1}


def _run(flac_path, *, metadata, embed_side_effect):
    with patch.object(enrichment, "get_config_manager", return_value=_Cfg()), \
         patch.object(enrichment, "strip_all_non_audio_tags"), \
         patch.object(enrichment, "extract_source_metadata", return_value=metadata), \
         patch.object(enrichment, "embed_source_ids"), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch.object(enrichment, "embed_album_art_metadata", side_effect=embed_side_effect):
        return enrichment.enhance_file_metadata(
            flac_path, context={}, artist={"name": "Coldplay"}, album_info={},
        )


def test_core_tags_survive_when_art_step_raises_on_artless_file(flac_path):
    """The regression: art-less file + a crash in the external art step must NOT
    leave the file untagged — the matched core tags must be on disk."""
    def boom(audio_file, metadata):
        raise RuntimeError("art backend exploded")

    result = _run(flac_path, metadata=dict(_CORE), embed_side_effect=boom)
    assert result is False                       # enrichment reported failure
    f = FLAC(flac_path)
    assert f.get("title") == ["Yellow"]          # ...but core tags persisted
    assert f.get("artist") == ["Coldplay"]
    assert f.get("album") == ["Parachutes"]      # the tag Rockbox buckets on
    assert f.get("tracknumber") == ["1/9"]


def test_core_tags_written_on_happy_path_artless_file(flac_path):
    result = _run(flac_path, metadata=dict(_CORE), embed_side_effect=lambda *a, **k: False)
    assert result is True
    f = FLAC(flac_path)
    assert f.get("album") == ["Parachutes"]
    assert f.get("artist") == ["Coldplay"]
