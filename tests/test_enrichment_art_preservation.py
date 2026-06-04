"""End-to-end proof that enhance_file_metadata never destroys embedded art.

Bug #764: the enrichment rewrite clears cover art up front and saves the file
regardless of whether new art gets re-embedded. These tests run the REAL
``enhance_file_metadata`` against a REAL FLAC that has embedded art, and assert
the art is still on disk after every failure path — and that the happy path
(new art embedded) correctly REPLACES it. This exercises the actual wiring
(snapshot -> clear -> rewrite -> restore/save), not just the helper functions.

Only the external collaborators are stubbed (config, source-metadata extraction,
the art fetch, source-id embed, verification) — the clear/snapshot/restore/save
sequence under test runs for real through mutagen.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

pytest.importorskip("mutagen")
from mutagen.flac import FLAC, Picture  # noqa: E402

import core.metadata.enrichment as enrichment  # noqa: E402

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _Cfg:
    """Config stub: returns the caller's default for every key, so
    metadata_enhancement.enabled / embed_album_art resolve True."""

    def get(self, key, default=None):
        return default


def _make_flac_with_art(path):
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
    audio = FLAC(path)
    pic = Picture()
    pic.data = _PNG
    pic.type = 3
    pic.mime = "image/png"
    pic.width = 1
    pic.height = 1
    pic.depth = 24
    audio.add_picture(pic)
    audio.save()


@pytest.fixture
def flac_path():
    fd, path = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    _make_flac_with_art(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def _disk_art(path):
    """Return the embedded picture bytes on disk, or None."""
    pics = FLAC(path).pictures
    return pics[0].data if pics else None


def _run(flac_path, *, metadata, embed_side_effect):
    """Drive enhance_file_metadata with stubbed collaborators."""
    with patch.object(enrichment, "get_config_manager", return_value=_Cfg()), \
         patch.object(enrichment, "strip_all_non_audio_tags"), \
         patch.object(enrichment, "extract_source_metadata", return_value=metadata), \
         patch.object(enrichment, "embed_source_ids"), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch.object(enrichment, "embed_album_art_metadata", side_effect=embed_side_effect):
        return enrichment.enhance_file_metadata(
            flac_path, context={}, artist={"name": "Coldplay"}, album_info={},
        )


# ── failure paths: art MUST survive ──────────────────────────────────────


def test_art_survives_when_source_metadata_missing(flac_path):
    # extract_source_metadata returns None -> early return path.
    assert _disk_art(flac_path) == _PNG
    result = _run(flac_path, metadata=None, embed_side_effect=lambda *a, **k: False)
    assert result is True
    assert _disk_art(flac_path) == _PNG  # art preserved on disk


def test_art_survives_when_embed_produces_nothing(flac_path):
    # Metadata is fine, but the art fetch fails -> embed is a no-op (returns
    # False, adds no picture). The original art must remain.
    def embed_noop(audio_file, metadata):
        return False  # mirrors "no art URL" / "download failed"

    result = _run(flac_path, metadata={"title": "Yellow", "artist": "Coldplay"},
                  embed_side_effect=embed_noop)
    assert result is True
    assert _disk_art(flac_path) == _PNG


def test_art_survives_when_embed_raises(flac_path):
    # A hard crash mid-enrichment must not leave the file art-less.
    def embed_boom(audio_file, metadata):
        raise RuntimeError("art backend exploded")

    result = _run(flac_path, metadata={"title": "Yellow", "artist": "Coldplay"},
                  embed_side_effect=embed_boom)
    assert result is False  # enrichment reported failure
    assert _disk_art(flac_path) == _PNG  # ...but art was restored on disk


# ── happy path: new art REPLACES old, no duplication ──────────────────────


def test_new_art_replaces_old_when_embed_succeeds(flac_path):
    new_bytes = _PNG + b"BRANDNEW"

    def embed_real(audio_file, metadata):
        # Simulate a successful fetch+embed: add the new picture in-place.
        pic = Picture()
        pic.data = new_bytes
        pic.type = 3
        pic.mime = "image/png"
        audio_file.add_picture(pic)
        return True

    result = _run(flac_path, metadata={"title": "Yellow", "artist": "Coldplay"},
                  embed_side_effect=embed_real)
    assert result is True
    on_disk = FLAC(flac_path).pictures
    # Exactly one picture, and it's the NEW art — restore must not have
    # re-added the old one on top.
    assert len(on_disk) == 1
    assert on_disk[0].data == new_bytes
