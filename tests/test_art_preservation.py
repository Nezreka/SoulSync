"""Tests for embedded-cover-art preservation across the enrichment rewrite.

Regression for #764 (continuation of #755): importing a file destroyed its
embedded album art whenever the re-embed step couldn't produce new art
(no source metadata, no art URL, download failed, rejected by the min-size
guard, or art embedding disabled). ``enhance_file_metadata`` clears pictures
up front and saves regardless; these helpers snapshot the art first and put
it back iff the file would otherwise be saved with none.

Uses real mutagen objects (a minimal valid FLAC + a minimal MP3) so the
snapshot/restore round-trips through the actual Picture/APIC APIs the
enricher uses — not a mock of them.
"""

from __future__ import annotations

import os
import tempfile

import pytest

mutagen = pytest.importorskip("mutagen")

from mutagen.flac import FLAC, Picture  # noqa: E402
from mutagen.id3 import APIC, ID3, TIT2  # noqa: E402

from core.metadata.common import get_mutagen_symbols  # noqa: E402
from core.metadata.art_preservation import (  # noqa: E402
    has_embedded_art,
    restore_embedded_art,
    snapshot_embedded_art,
)

SYMBOLS = get_mutagen_symbols()

# 1x1 PNG — smallest valid image bytes for a real Picture/APIC payload.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _minimal_flac_bytes() -> bytes:
    # 4-byte magic + last STREAMINFO block (34 bytes). Mirrors the fixture
    # used by tests/test_tag_writer_multi_artist.py.
    return (
        b"fLaC"
        + b"\x80\x00\x00\x22"
        + b"\x00\x10\x00\x10"
        + b"\x00\x00\x00\x00\x00\x00"
        + b"\x0a\xc4\x42\xf0\x00\x00\x00\x00"
        + b"\x00" * 16
    )


@pytest.fixture
def flac_with_art():
    fd, path = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(_minimal_flac_bytes())
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
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


class _ID3Holder:
    """Stand-in for an mutagen MP3 object: exposes a real ``ID3`` tag block
    (the only thing the snapshot/restore helpers touch) without needing a
    syncable MPEG frame on disk. ``__setitem__`` mirrors mutagen's mapping
    sugar used by the MP4 restore branch — unused here but kept faithful."""

    def __init__(self):
        self.tags = ID3()
        self.tags.add(TIT2(encoding=3, text=["original"]))
        self.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=_PNG))

    def __setitem__(self, key, value):
        self.tags[key] = value


@pytest.fixture
def mp3_with_art():
    return _ID3Holder()


# ── FLAC ────────────────────────────────────────────────────────────────


def test_flac_art_restored_after_clear(flac_with_art):
    audio = FLAC(flac_with_art)
    assert has_embedded_art(audio, SYMBOLS)

    snap = snapshot_embedded_art(audio, SYMBOLS)
    assert snap  # something captured

    # Simulate the enrichment rewrite: nuke the art, fail to re-embed.
    audio.clear_pictures()
    assert not has_embedded_art(audio, SYMBOLS)

    restored = restore_embedded_art(audio, SYMBOLS, snap)
    assert restored is True
    assert has_embedded_art(audio, SYMBOLS)
    assert audio.pictures[0].data == _PNG


def test_flac_restore_is_noop_when_new_art_present(flac_with_art):
    # Happy path: re-embed succeeded, so restore must NOT touch the file.
    audio = FLAC(flac_with_art)
    snap = snapshot_embedded_art(audio, SYMBOLS)

    audio.clear_pictures()
    new = Picture()
    new.data = _PNG + b"NEWART"
    new.type = 3
    new.mime = "image/png"
    audio.add_picture(new)

    restored = restore_embedded_art(audio, SYMBOLS, snap)
    assert restored is False
    assert len(audio.pictures) == 1
    assert audio.pictures[0].data == _PNG + b"NEWART"  # not clobbered/duplicated


def test_flac_no_art_snapshot_empty():
    fd, path = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(_minimal_flac_bytes())
        audio = FLAC(path)
        assert snapshot_embedded_art(audio, SYMBOLS) == []
        # Restoring an empty snapshot is a no-op.
        assert restore_embedded_art(audio, SYMBOLS, []) is False
    finally:
        os.remove(path)


# ── MP3 / ID3 ─────────────────────────────────────────────────────────────


def test_mp3_apic_restored_after_tags_cleared(mp3_with_art):
    audio = mp3_with_art
    assert has_embedded_art(audio, SYMBOLS)
    snap = snapshot_embedded_art(audio, SYMBOLS)
    assert snap

    # The enricher does audio_file.tags.clear() then rewrites tags.
    audio.tags.clear()
    assert not has_embedded_art(audio, SYMBOLS)

    restored = restore_embedded_art(audio, SYMBOLS, snap)
    assert restored is True
    apics = audio.tags.getall("APIC")
    assert apics and apics[0].data == _PNG


def test_mp3_restore_noop_when_new_apic_present(mp3_with_art):
    audio = mp3_with_art
    snap = snapshot_embedded_art(audio, SYMBOLS)
    audio.tags.clear()
    audio.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=_PNG + b"NEW"))

    assert restore_embedded_art(audio, SYMBOLS, snap) is False
    apics = audio.tags.getall("APIC")
    assert len(apics) == 1 and apics[0].data == _PNG + b"NEW"
