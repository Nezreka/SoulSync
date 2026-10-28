"""BPM comparison consistency (#1871):

Writing tags did not clear BPM differences. MP4 ``read_file_tags`` never
read the ``tmpo`` atom the writer sets, and the MP3/Vorbis writers stored
``int(bpm)`` — the truncated integer — while ``build_tag_diff`` compared
against the DB's original fractional value, so a track with a fractional
DB BPM was flagged as changed again on every preview, causing repeated
file saves.

The fix: ``read_file_tags`` reads MP4 ``tmpo``, and writers + comparison
both normalize through ``_stored_bpm`` — the rounded integer form a file
tag actually stores (the repair worker's established "rounded, not cut"
rule). After a write, the preview must agree the track is unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest
from mutagen import File as MutagenFile
from mutagen.flac import FLAC

from core.tag_writer import (
    _stored_bpm,
    build_tag_diff,
    read_file_tags,
    write_tags_to_file,
)


def _bpm_changed(file_tags, db_data):
    diff = next(d for d in build_tag_diff(file_tags, db_data)
                if d['file_key'] == 'bpm')
    return diff['changed']


# ──────────────────────────────────────────────────────────────────────
# _stored_bpm — the shared stored-form normalization
# ──────────────────────────────────────────────────────────────────────

def test_stored_bpm_rounds_like_the_repair_worker():
    assert _stored_bpm(127.6) == 128   # rounded, not cut to 127
    assert _stored_bpm(128.7) == 129
    assert _stored_bpm(128.4) == 128
    assert _stored_bpm(128) == 128
    assert _stored_bpm('128.7') == 129


def test_stored_bpm_rejects_non_numbers():
    assert _stored_bpm(None) is None
    assert _stored_bpm('') is None
    assert _stored_bpm('fast') is None
    assert _stored_bpm(float('nan')) is None
    assert _stored_bpm(float('inf')) is None
    assert _stored_bpm(True) is None


def test_stored_bpm_half_values_match_the_repair_workers_rounding():
    # int(round(...)) is Python round-half-even — the exact expression
    # repair_worker._write_bpm_tag already used. Pinned so a future
    # "fix" to half-up does not silently desync the two writers.
    assert _stored_bpm(128.5) == 128
    assert _stored_bpm(129.5) == 130


# ──────────────────────────────────────────────────────────────────────
# build_tag_diff — comparison in stored form
# ──────────────────────────────────────────────────────────────────────

def test_diff_unchanged_when_file_holds_stored_form_of_fractional_db_bpm():
    assert _bpm_changed({'bpm': 129.0}, {'bpm': 128.7}) is False
    assert _bpm_changed({'bpm': 128.0}, {'bpm': 128.4}) is False


def test_diff_unchanged_when_file_tag_is_fractional_and_rounds_the_same():
    # A tag written by another tagger can carry the fractional value
    # itself (Vorbis/ID3 are text); it rounds to the same stored form.
    assert _bpm_changed({'bpm': 128.7}, {'bpm': 128.7}) is False
    assert _bpm_changed({'bpm': '128.7'}, {'bpm': 128.6}) is False


def test_diff_still_flags_a_genuinely_different_bpm():
    # 128 is the OLD truncated store of 128.7 — not the stored form the
    # writer produces now, so it is a real (one-time) change, not a loop.
    assert _bpm_changed({'bpm': 128.0}, {'bpm': 128.7}) is True
    assert _bpm_changed({'bpm': 130.0}, {'bpm': 128.7}) is True
    assert _bpm_changed({'bpm': 120.0}, {'bpm': 128.0}) is True


def test_diff_flags_missing_file_bpm_and_ignores_missing_db_bpm():
    assert _bpm_changed({'bpm': None}, {'bpm': 128}) is True
    assert _bpm_changed({'bpm': None}, {}) is False
    assert _bpm_changed({'bpm': 128.0}, {}) is False


def test_diff_accepts_a_string_db_bpm_in_stored_form():
    assert _bpm_changed({'bpm': 129.0}, {'bpm': '128.7'}) is False
    assert _bpm_changed({'bpm': 128.0}, {'bpm': '128.7'}) is True


def test_diff_half_value_db_bpm_compares_against_its_stored_form():
    # Stored form of 128.5 is 128 (round-half-even, pinned above).
    assert _bpm_changed({'bpm': 128.0}, {'bpm': 128.5}) is False
    assert _bpm_changed({'bpm': 129.0}, {'bpm': 128.5}) is True


def test_diff_db_bpm_zero_is_a_storable_value():
    # tag_writer has no <= 0 guard (those live in repair_worker and in
    # enrichment candidate selection); 0 stores and compares as 0.
    assert _bpm_changed({'bpm': 0.0}, {'bpm': 0}) is False
    assert _bpm_changed({'bpm': None}, {'bpm': 0}) is True


def test_diff_does_not_crash_on_non_finite_bpm():
    # A file tag literally reading 'nan'/'inf' parses to a non-finite
    # float in read_file_tags; the preview must not raise on it.
    assert _bpm_changed({'bpm': float('nan')}, {'bpm': 128.0}) is True
    assert _bpm_changed({'bpm': float('inf')}, {'bpm': 128.0}) is True


def test_diff_ignores_a_db_bpm_the_writer_cannot_store():
    # The writers skip a non-numeric/non-finite DB BPM, so flagging it
    # would re-flag the track forever while every batch write rewrites
    # the file without settling it — the #1871 loop on an edge case.
    for bad in ('fast', 'nan', float('nan'), float('inf'), float('-inf')):
        assert _bpm_changed({'bpm': 128.0}, {'bpm': bad}) is False, bad
        assert _bpm_changed({'bpm': None}, {'bpm': bad}) is False, bad


# ──────────────────────────────────────────────────────────────────────
# Roundtrips on real files — write, re-read, preview must settle
# ──────────────────────────────────────────────────────────────────────

def _make_minimal_flac(path):
    minimal = (
        b'fLaC' + b'\x80\x00\x00\x22' + b'\x00\x10\x00\x10'
        + b'\x00\x00\x00\x00\x00\x00' + b'\x0a\xc4\x42\xf0\x00\x00\x00\x00' + b'\x00' * 16
    )
    with open(path, 'wb') as f:
        f.write(minimal)


@pytest.fixture
def flac_path():
    fd, path = tempfile.mkstemp(suffix='.flac')
    os.close(fd)
    _make_minimal_flac(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def test_flac_write_then_preview_is_unchanged(flac_path):
    result = write_tags_to_file(flac_path, {'bpm': 128.7}, embed_cover=False)
    assert result['success'] is True
    assert FLAC(flac_path).get('bpm') == ['129']  # stored form: rounded
    tags = read_file_tags(flac_path)
    assert tags['bpm'] == 129.0
    assert _bpm_changed(tags, {'bpm': 128.7}) is False


def test_flac_write_of_sub_half_fraction_stores_and_settles(flac_path):
    result = write_tags_to_file(flac_path, {'bpm': 128.4}, embed_cover=False)
    assert result['success'] is True
    assert FLAC(flac_path).get('bpm') == ['128']
    assert _bpm_changed(read_file_tags(flac_path), {'bpm': 128.4}) is False


def test_flac_write_with_an_invalid_bpm_skips_bpm_but_writes_the_rest(flac_path):
    # Deliberate contract (was: int('fast') raised and the WHOLE write
    # failed): a non-numeric BPM is skipped, other fields still land,
    # and callers that care see 'bpm' missing from written_fields —
    # repair_worker._write_bpm_tag checks exactly that.
    result = write_tags_to_file(
        flac_path, {'title': 'Song', 'bpm': 'fast'}, embed_cover=False)
    assert result['success'] is True
    assert 'bpm' not in result['written_fields']
    audio = FLAC(flac_path)
    assert audio.get('title') == ['Song']
    assert audio.get('bpm') is None


def test_flac_write_accepts_a_fractional_string_bpm(flac_path):
    # int('128.7') raised before the fix; the stored form is 129.
    result = write_tags_to_file(flac_path, {'bpm': '128.7'}, embed_cover=False)
    assert result['success'] is True
    assert FLAC(flac_path).get('bpm') == ['129']


ffmpeg_required = pytest.mark.skipif(
    shutil.which('ffmpeg') is None, reason='ffmpeg required to build test audio'
)


def _make_minimal_mp4(path):
    """A parseable MP4 skeleton (ftyp + empty moov) mutagen can tag —
    keeps the tmpo read/write coverage alive on runners without ffmpeg."""
    import struct

    def atom(typ, payload):
        return struct.pack('>I4s', len(payload) + 8, typ) + payload

    data = atom(b'ftyp', b'isom' + struct.pack('>I', 0x200) + b'isomiso2')
    data += atom(b'moov', b'')
    with open(path, 'wb') as f:
        f.write(data)


def test_mp4_tmpo_read_and_write_roundtrip_without_ffmpeg(tmp_path):
    path = tmp_path / 'song.mp4'
    _make_minimal_mp4(str(path))
    result = write_tags_to_file(str(path), {'bpm': 128.7}, embed_cover=False)
    assert result['success'] is True
    assert MutagenFile(str(path)).get('tmpo') == [129]
    tags = read_file_tags(str(path))
    assert tags['bpm'] == 129.0  # was None before the fix: tmpo unread
    assert _bpm_changed(tags, {'bpm': 128.7}) is False


def _make_with_ffmpeg(path, codec_args):
    subprocess.run(
        ['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi',
         '-i', 'sine=frequency=440:duration=1', *codec_args, str(path)],
        check=True,
    )


@ffmpeg_required
def test_mp4_tmpo_is_read_back_and_preview_settles(tmp_path):
    path = tmp_path / 'song.m4a'
    _make_with_ffmpeg(path, ['-c:a', 'aac'])
    result = write_tags_to_file(str(path), {'bpm': 128.7}, embed_cover=False)
    assert result['success'] is True
    assert MutagenFile(str(path)).get('tmpo') == [129]
    tags = read_file_tags(str(path))
    assert tags['bpm'] == 129.0  # was None before the fix: tmpo unread
    assert _bpm_changed(tags, {'bpm': 128.7}) is False


@ffmpeg_required
def test_ogg_vorbis_write_then_preview_is_unchanged(tmp_path):
    path = tmp_path / 'song.ogg'
    _make_with_ffmpeg(path, ['-c:a', 'libvorbis'])
    result = write_tags_to_file(str(path), {'bpm': 128.7}, embed_cover=False)
    assert result['success'] is True
    tags = read_file_tags(str(path))
    assert tags['bpm'] == 129.0
    assert _bpm_changed(tags, {'bpm': 128.7}) is False


@ffmpeg_required
def test_mp3_write_then_preview_is_unchanged(tmp_path):
    path = tmp_path / 'song.mp3'
    _make_with_ffmpeg(path, ['-c:a', 'libmp3lame'])
    result = write_tags_to_file(str(path), {'bpm': 128.7}, embed_cover=False)
    assert result['success'] is True
    tags = read_file_tags(str(path))
    assert tags['bpm'] == 129.0
    assert _bpm_changed(tags, {'bpm': 128.7}) is False


@ffmpeg_required
def test_mp3_with_a_foreign_fractional_tbpm_reads_and_compares_in_stored_form(tmp_path):
    # Another tagger can store TBPM as fractional text; read_file_tags
    # surfaces it as a float and the diff compares its stored form.
    from mutagen.id3 import ID3, TBPM
    path = tmp_path / 'song.mp3'
    _make_with_ffmpeg(path, ['-c:a', 'libmp3lame'])
    audio = MutagenFile(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(TBPM(encoding=3, text=['128.7']))
    audio.save()
    tags = read_file_tags(str(path))
    assert tags['bpm'] == 128.7
    assert _bpm_changed(tags, {'bpm': 128.7}) is False
    assert _bpm_changed(tags, {'bpm': 120.0}) is True


@ffmpeg_required
def test_mp4_write_with_an_invalid_bpm_skips_tmpo_but_writes_the_rest(tmp_path):
    path = tmp_path / 'song.m4a'
    _make_with_ffmpeg(path, ['-c:a', 'aac'])
    result = write_tags_to_file(
        str(path), {'title': 'Song', 'bpm': 'fast'}, embed_cover=False)
    assert result['success'] is True
    assert 'bpm' not in result['written_fields']
    audio = MutagenFile(str(path))
    assert audio.get('\xa9nam') == ['Song']
    assert 'tmpo' not in audio


@ffmpeg_required
def test_mp3_write_with_an_invalid_bpm_skips_tbpm_but_writes_the_rest(tmp_path):
    path = tmp_path / 'song.mp3'
    _make_with_ffmpeg(path, ['-c:a', 'libmp3lame'])
    result = write_tags_to_file(
        str(path), {'title': 'Song', 'bpm': 'fast'}, embed_cover=False)
    assert result['success'] is True
    assert 'bpm' not in result['written_fields']
    audio = MutagenFile(str(path))
    assert str(audio.tags['TIT2'].text[0]) == 'Song'
    assert audio.tags.getall('TBPM') == []


# ──────────────────────────────────────────────────────────────────────
# The enrichment writer (core/metadata/source.py) stores the same form
# ──────────────────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_enrichment_writer_stores_the_rounded_bpm(flac_path):
    """Round-1 review caught source.py truncating with int(bpm_val);
    pin its stored form so a revert cannot slip through silently."""
    from core.metadata import source as ms
    from core.metadata.common import get_mutagen_symbols

    audio = FLAC(flac_path)
    pp = ms._blank_post_process_state()
    pp['deezer_bpm'] = 128.7
    ms._write_embedded_metadata(
        audio, {'genre': ''}, pp, _Cfg(), get_mutagen_symbols())
    audio.save()
    assert FLAC(flac_path).get('BPM') == ['129']


def test_enrichment_writer_skips_a_non_finite_bpm(flac_path):
    from core.metadata import source as ms
    from core.metadata.common import get_mutagen_symbols

    audio = FLAC(flac_path)
    pp = ms._blank_post_process_state()
    pp['deezer_bpm'] = float('inf')  # passes the candidate's > 0 check
    ms._write_embedded_metadata(
        audio, {'genre': ''}, pp, _Cfg(), get_mutagen_symbols())
    audio.save()
    assert FLAC(flac_path).get('BPM') is None


def test_enrichment_writer_falls_back_past_an_unstorable_candidate(flac_path):
    from core.metadata import source as ms
    from core.metadata.common import get_mutagen_symbols

    audio = FLAC(flac_path)
    pp = ms._blank_post_process_state()
    pp['beatport'] = {'bpm': 'inf'}  # passes float(...) > 0, has no stored form
    pp['deezer_bpm'] = 72
    ms._write_embedded_metadata(
        audio, {'genre': ''}, pp, _Cfg(), get_mutagen_symbols())
    audio.save()
    assert FLAC(flac_path).get('BPM') == ['72']


def test_lossy_vorbis_to_mp4_bpm_copy_uses_the_stored_form(tmp_path):
    from core.metadata.common import get_mutagen_symbols
    from core.metadata.lossy_tags import _vorbis_to_mp4
    from mutagen.mp4 import MP4

    path = tmp_path / 'song.mp4'
    _make_minimal_mp4(str(path))
    dest = MP4(str(path))
    dest.add_tags()
    _vorbis_to_mp4({'BPM': ['128.7']}, dest, get_mutagen_symbols())
    assert dest.get('tmpo') == [129]  # was dropped entirely by _int('128.7')
