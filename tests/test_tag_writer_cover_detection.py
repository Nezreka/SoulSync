"""issues.md T-07: one truth about "does this file carry embedded cover art".

``read_file_tags`` feeds the Library-v2 tag/gap cache, while
``core.metadata.art_apply`` decides whether the Cover Art Filler raises a
finding and whether the apply embeds anything. When the two disagree the UI
shows a "missing cover" gap that no scan reports and no apply can ever close.
"""

from __future__ import annotations

import base64
import shutil
import subprocess

import pytest

from core.metadata.art_apply import file_has_embedded_art
from core.tag_writer import read_file_tags

pytest.importorskip("mutagen")

# 1x1 PNG — the smallest thing that is unambiguously an image.
_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _picture():
    from mutagen.flac import Picture

    picture = Picture()
    picture.type = 3
    picture.mime = "image/png"
    picture.data = _PNG
    return picture


def _encode(path):
    """A tiny real audio file — mutagen can tag but never create one."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available to build an audio fixture")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "quiet",
                "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1",
                str(path),
            ],
            check=True,
        )
    except subprocess.CalledProcessError:  # pragma: no cover - build env only
        pytest.skip(f"ffmpeg cannot encode {path.suffix}")
    return path


def test_ogg_metadata_block_picture_counts_as_cover_art(tmp_path):
    from mutagen.oggvorbis import OggVorbis

    path = _encode(tmp_path / "song.ogg")
    audio = OggVorbis(str(path))
    audio["metadata_block_picture"] = [
        base64.b64encode(_picture().write()).decode("ascii")
    ]
    audio.save()

    # art_apply already sees it; the tag reader must agree, or the gap cell
    # reports a "missing cover" no scan raises and no apply can close.
    assert file_has_embedded_art(str(path)) is True
    assert read_file_tags(str(path))["has_cover_art"] is True


def test_ogg_without_picture_still_reports_no_cover(tmp_path):
    path = _encode(tmp_path / "bare.ogg")

    assert file_has_embedded_art(str(path)) is False
    assert read_file_tags(str(path))["has_cover_art"] is False


def test_flac_cover_detection_agrees_with_art_apply(tmp_path):
    from mutagen.flac import FLAC

    path = _encode(tmp_path / "song.flac")
    assert read_file_tags(str(path))["has_cover_art"] is False

    audio = FLAC(str(path))
    audio.add_picture(_picture())
    audio.save()

    assert file_has_embedded_art(str(path)) is True
    assert read_file_tags(str(path))["has_cover_art"] is True
