"""A lying Xing/Info header must not shrink an MP3's playing time.

A 192 kbps CBR file of 102 MB announced 17,054 frames (7.4 minutes) in its Info
header while holding 71 minutes of audio. ``mutagen`` trusts the header, so the
audiobook completeness check read "Only 7 of 71 minutes present" and held a
complete book, and the library scan listed it as a seven-minute book.

The fixtures are built byte by byte: MPEG-1 Layer III, 128 kbps, 44.1 kHz,
stereo, 417 bytes per frame, zero-filled apart from the headers.
"""
import struct

import pytest

from core.audio_length import id3v2_size, reliable_length_seconds

FRAME_HEADER = bytes([0xFF, 0xFB, 0x90, 0x00])   # MPEG1 L3, 128 kbps, 44.1 kHz, no padding
FRAME_SIZE = 417
SECONDS_PER_FRAME = 1152 / 44100


def _frame(payload_at_36=b""):
    body = bytearray(FRAME_SIZE - 4)
    if payload_at_36:
        body[32:32 + len(payload_at_36)] = payload_at_36       # offset 36 in the frame
    return FRAME_HEADER + bytes(body)


def _mp3(path, frames, header_tag=None, header_frames=None, id3_bytes=b""):
    """``frames`` real frames; optionally a leading Info/Xing frame announcing ``header_frames``."""
    data = bytearray(id3_bytes)
    if header_tag:
        data += _frame(header_tag + struct.pack(">II", 1, header_frames))   # flags=1: frame count
    for _ in range(frames):
        data += _frame()
    path.write_bytes(bytes(data))
    return path


def _load(path):
    from mutagen import File
    return File(str(path))


def test_info_header_that_understates_the_length_is_corrected(tmp_path):
    f = _mp3(tmp_path / "lie.mp3", frames=2000, header_tag=b"Info", header_frames=100)
    audio = _load(f)
    announced = audio.info.length
    assert announced == pytest.approx(100 * SECONDS_PER_FRAME, abs=0.5)     # mutagen believes the header
    true_seconds = 2001 * SECONDS_PER_FRAME
    assert reliable_length_seconds(audio, f) == pytest.approx(true_seconds, rel=0.02)


def test_an_honest_info_header_is_left_alone(tmp_path):
    f = _mp3(tmp_path / "honest.mp3", frames=2000, header_tag=b"Info", header_frames=2001)
    audio = _load(f)
    assert reliable_length_seconds(audio, f) == pytest.approx(audio.info.length, abs=0.01)


def test_file_without_a_header_is_left_alone(tmp_path):
    f = _mp3(tmp_path / "plain.mp3", frames=2000)
    audio = _load(f)
    assert reliable_length_seconds(audio, f) == pytest.approx(audio.info.length, abs=0.01)


def test_vbr_xing_header_is_never_overridden(tmp_path):
    # "Xing" marks VBR: bytes divided by a nominal bitrate says nothing there
    f = _mp3(tmp_path / "vbr.mp3", frames=2000, header_tag=b"Xing", header_frames=100)
    audio = _load(f)
    assert reliable_length_seconds(audio, f) == pytest.approx(audio.info.length, abs=0.01)


def test_leading_id3v2_tag_is_not_counted_as_audio(tmp_path):
    tag = b"ID3" + bytes([4, 0, 0]) + bytes([0, 0, 0x40, 0x00]) + bytes(8192)   # 8192-byte tag body
    assert id3v2_size_of(tag) == 10 + 8192
    f = _mp3(tmp_path / "tagged.mp3", frames=2000, header_tag=b"Info", header_frames=100, id3_bytes=tag)
    audio = _load(f)
    assert reliable_length_seconds(audio, f) == pytest.approx(2001 * SECONDS_PER_FRAME, rel=0.02)


def id3v2_size_of(tag_bytes):
    import tempfile
    import os
    fd, name = tempfile.mkstemp()
    os.write(fd, tag_bytes)
    os.close(fd)
    try:
        return id3v2_size(name)
    finally:
        os.remove(name)


def test_non_mp3_and_unreadable_input_fall_back_to_mutagens_length(tmp_path):
    class Info:
        length = 12.5

    class Fake:
        info = Info()

    assert reliable_length_seconds(Fake(), tmp_path / "missing.m4b") == 12.5
    assert reliable_length_seconds(None, tmp_path / "missing.mp3") == 0.0


def test_completeness_check_uses_the_corrected_length(tmp_path):
    from core.audiobook_completeness import measure_duration_seconds
    f = _mp3(tmp_path / "book.mp3", frames=2000, header_tag=b"Info", header_frames=100)
    assert measure_duration_seconds(f) == pytest.approx(2001 * SECONDS_PER_FRAME, rel=0.02)


def test_library_scan_uses_the_corrected_length(tmp_path):
    from core.audiobook_library_metadata import read_metadata
    f = _mp3(tmp_path / "book.mp3", frames=2000, header_tag=b"Info", header_frames=100)
    facts = read_metadata(f, [f], include_sidecars=False)
    assert facts["duration_seconds"] == pytest.approx(2001 * SECONDS_PER_FRAME, rel=0.02)
