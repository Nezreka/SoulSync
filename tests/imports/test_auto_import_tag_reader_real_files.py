"""Integration tests for ``_read_file_tags`` against real audio files
written with mutagen.

The unit tests for the matcher use dict fixtures — they prove the
algorithm handles the right shapes once tags are read. These tests
prove the tag READER itself extracts the right values from real
files, including the Picard tags (``musicbrainz_trackid``, ``isrc``)
that the new fast paths depend on.

Without this layer, a mutagen normalisation quirk (different easy-
mode key for FLAC vs MP3 vs M4A, version-specific schema changes)
could silently break the fast paths in production while every unit
test passes.

Files are written + read in-memory via mutagen so no binary fixture
ships in the repo.
"""

from __future__ import annotations

import os
import struct
import tempfile

import pytest

from core.auto_import_worker import _read_file_tags


# ---------------------------------------------------------------------------
# Helpers — write a minimal valid FLAC with the requested tags
# ---------------------------------------------------------------------------


def _write_minimal_flac(path: str, tags: dict):
    """Create a real FLAC file with mutagen + write the given Vorbis
    comment tags. Mirrors the helper pattern in
    ``test_album_mbid_consistency.py`` so we don't reinvent the FLAC
    bootstrap.

    Note: duration on these test files is whatever mutagen derives
    from the synthesized STREAMINFO — typically near-zero. Tests that
    care about a specific duration use the ``streaminfo_total_samples``
    helper to override.
    """
    from mutagen.flac import FLAC

    fLaC = b'fLaC'
    # Minimum STREAMINFO: 16 bits min/max block size, 24 bits min/max
    # frame size, 20 bits sample rate, 3 bits channels-1, 5 bits
    # bits-per-sample-1, 36 bits total samples, 128 bits md5 sig.
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A  # sample rate / channels packed
    streaminfo[12] = 0x70  # bits-per-sample bits
    # Block header: last_block=1, type=0 (STREAMINFO), length=34
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    with open(path, 'wb') as f:
        f.write(fLaC + block_header + bytes(streaminfo))

    audio = FLAC(path)
    for key, value in tags.items():
        audio[key] = value
    audio.save()


def _write_flac_with_duration(path: str, tags: dict, *, duration_seconds: float):
    """Write a FLAC file then patch STREAMINFO to claim the given
    duration. Used by the duration-reading test — mutagen reports
    audio.info.length from STREAMINFO's total_samples / sample_rate.
    """
    from mutagen.flac import FLAC

    _write_minimal_flac(path, tags)

    # Open + patch the STREAMINFO total_samples to encode the desired
    # duration. STREAMINFO sits at fLaC[4:] + 4-byte block header +
    # 34-byte body. total_samples is a 36-bit field starting at byte
    # 18 (top 4 bits packed with the previous byte's bottom 4 bits).
    audio = FLAC(path)
    audio.info.length = duration_seconds  # mutagen exposes this directly
    # The reader pulls .info.length, so just patching the in-memory
    # representation isn't enough — we need the file on disk to match.
    # Easier: write our own STREAMINFO block with the right values.
    sample_rate = 44100
    total_samples = int(duration_seconds * sample_rate)

    # Read the file, rewrite the STREAMINFO byte range.
    with open(path, 'rb') as f:
        data = bytearray(f.read())

    # STREAMINFO body starts at offset 8 (4-byte 'fLaC' + 4-byte block
    # header). Sample rate is 20 bits starting at bit offset 80 (byte
    # 10). For our purposes, we need to set:
    #   sample_rate = 44100 (bits 80..99)
    #   total_samples = computed (bits 108..143, 36 bits)
    # Easier path: synthesize a fresh STREAMINFO with all fields right.

    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)   # min_blocksize
    streaminfo[2:4] = struct.pack('>H', 4096)   # max_blocksize
    # min/max framesize stay 0 (bytes 4..9)

    # Pack sample_rate (20 bits) | channels-1 (3 bits) | bps-1 (5 bits) |
    # total_samples (36 bits) into bytes 10..17 (64 bits).
    # 44100 << 12 leaves room for channels (3 bits, 0=mono so we set 1=stereo)
    # bps-1 = 15 (16-bit)
    sr = sample_rate          # 20 bits
    ch = 1                    # 3 bits (channels-1: 1 = stereo)
    bps = 15                  # 5 bits (bps-1: 15 = 16bps)
    ts = total_samples        # 36 bits

    packed = (sr << 44) | (ch << 41) | (bps << 36) | ts
    streaminfo[10:18] = packed.to_bytes(8, 'big')

    # MD5 stays 16 bytes of zeroes (bytes 18..33)
    data[8:8 + 34] = streaminfo

    with open(path, 'wb') as f:
        f.write(bytes(data))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reads_picard_style_mbid_and_isrc_from_flac():
    """Picard writes Vorbis comment tags ``musicbrainz_trackid`` and
    ``isrc`` on every tagged FLAC. The reader must extract both via
    mutagen's easy-mode normalisation."""
    pytest.importorskip("mutagen")

    with tempfile.TemporaryDirectory() as td:
        flac_path = os.path.join(td, 'test.flac')
        _write_minimal_flac(flac_path, {
            'TITLE': 'Father Time',
            'ARTIST': 'Kendrick Lamar',
            'ALBUM': 'Mr. Morale & The Big Steppers',
            'TRACKNUMBER': '5',
            'DISCNUMBER': '1',
            'ISRC': 'USUM72202156',
            'MUSICBRAINZ_TRACKID': '8a89a04f-7eba-4c0c-bf0c-5c9d7d54df54',
        })

        tags = _read_file_tags(flac_path)

        assert tags['title'] == 'Father Time'
        assert tags['artist'] == 'Kendrick Lamar'
        assert tags['album'] == 'Mr. Morale & The Big Steppers'
        assert tags['track_number'] == 5
        assert tags['disc_number'] == 1
        # ISRC is upper-cased by reader, stripping done at matcher layer
        assert tags['isrc'] == 'USUM72202156'
        # MBID is lower-cased
        assert tags['mbid'] == '8a89a04f-7eba-4c0c-bf0c-5c9d7d54df54'


def test_reads_duration_from_streaminfo():
    """Duration comes off ``audio.info.length`` (StreamInfo on FLAC),
    NOT from any tag. Reader must convert seconds to ms to match the
    metadata-source convention."""
    pytest.importorskip("mutagen")

    with tempfile.TemporaryDirectory() as td:
        flac_path = os.path.join(td, 'test.flac')
        _write_flac_with_duration(flac_path, {
            'TITLE': 'Test', 'ARTIST': 'Test',
        }, duration_seconds=180.5)

        tags = _read_file_tags(flac_path)

        # 180.5s × 1000 = 180500 ms
        assert tags['duration_ms'] == 180_500


def test_reads_file_with_no_tags():
    """File with valid audio but no Vorbis comment block — reader
    must return empty/default values, not crash. Common for files
    converted from formats that don't carry tags."""
    pytest.importorskip("mutagen")

    with tempfile.TemporaryDirectory() as td:
        flac_path = os.path.join(td, 'test.flac')
        # No tags dict — only mandatory STREAMINFO
        _write_minimal_flac(flac_path, {})

        tags = _read_file_tags(flac_path)

        # Empty defaults across the board, but the structure is
        # complete — no KeyError downstream.
        assert tags['title'] == ''
        assert tags['artist'] == ''
        assert tags['album'] == ''
        assert tags['track_number'] == 0
        assert tags['disc_number'] == 1
        assert tags['isrc'] == ''
        assert tags['mbid'] == ''
        # duration_ms is int (may be 0 for the synthesized minimal flac
        # — pin the SHAPE not the value, separate test pins the actual
        # duration via _write_flac_with_duration)
        assert isinstance(tags['duration_ms'], int)


def test_reader_handles_unreadable_file_gracefully():
    """File that's not actually audio — mutagen raises, reader
    returns the default-empty dict, doesn't crash."""
    with tempfile.NamedTemporaryFile(suffix='.flac', delete=False) as f:
        f.write(b'this is not flac data')
        path = f.name

    try:
        tags = _read_file_tags(path)
        # All defaults, no crash
        assert tags['title'] == ''
        assert tags['mbid'] == ''
        assert tags['duration_ms'] == 0
    finally:
        os.unlink(path)


def test_track_number_with_total_format_parses_correctly():
    """Some tag schemas write track numbers as ``"5/12"`` (track 5 of
    12). Reader must parse just the leading number, not crash on the
    slash."""
    pytest.importorskip("mutagen")

    with tempfile.TemporaryDirectory() as td:
        flac_path = os.path.join(td, 'test.flac')
        _write_minimal_flac(flac_path, {
            'TRACKNUMBER': '5/12',
            'DISCNUMBER': '2/3',
        })

        tags = _read_file_tags(flac_path)
        assert tags['track_number'] == 5
        assert tags['disc_number'] == 2


def test_isrc_with_dashes_preserved_for_matcher_to_normalise():
    """Reader keeps ISRC formatting as-is from the tag — normalisation
    (uppercase + strip dashes) happens at the matcher layer
    (``_file_identifier``). Splitting normalisation across reader +
    matcher is fine; pinning the contract here so no one assumes
    the reader normalises."""
    pytest.importorskip("mutagen")

    with tempfile.TemporaryDirectory() as td:
        flac_path = os.path.join(td, 'test.flac')
        _write_minimal_flac(flac_path, {
            'ISRC': 'us-um7-22-02156',  # mixed case, dashes
        })

        tags = _read_file_tags(flac_path)
        # Reader uppercases; matcher will strip dashes
        assert tags['isrc'] == 'US-UM7-22-02156'
