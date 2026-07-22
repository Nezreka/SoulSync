"""Replacement length guard (sella's HiFi incident).

The upgrade/enhance flow os.remove()s the library file and moves the
incoming one in. sella's real 220s tracks got replaced by 30s HiFi
preview clips whose headers (and AcoustID) all read full length. This
guard is the last line: compare the REAL decoded lengths and refuse a
replacement that plays materially shorter, no matter what the headers say.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from core.imports import pipeline


def _wav(path: Path, duration_s: float, sample_rate: int = 8000) -> Path:
    n = int(duration_s * sample_rate)
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate,
                      sample_rate * 2, 2, 16)
    data = struct.pack("<4sI", b"data", n * 2) + (b"\x00\x00" * n)
    riff = struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(data), b"WAVE")
    path.write_bytes(riff + fmt + data)
    return path


def test_rejects_a_short_clip_replacing_a_full_track(tmp_path):
    existing = _wav(tmp_path / "library.wav", 220.0)
    incoming = _wav(tmp_path / "clip.wav", 30.0)
    assert pipeline._replacement_length_is_safe(str(existing), str(incoming)) is False


def test_allows_a_same_length_upgrade(tmp_path):
    existing = _wav(tmp_path / "library.wav", 220.0)
    incoming = _wav(tmp_path / "upgrade.wav", 219.0)
    assert pipeline._replacement_length_is_safe(str(existing), str(incoming)) is True


def test_allows_a_longer_replacement(tmp_path):
    existing = _wav(tmp_path / "library.wav", 200.0)
    incoming = _wav(tmp_path / "remaster.wav", 240.0)
    assert pipeline._replacement_length_is_safe(str(existing), str(incoming)) is True


def test_unknown_length_never_blocks(tmp_path, monkeypatch):
    # either side undeterminable → allow (other gates own that case)
    existing = _wav(tmp_path / "library.wav", 220.0)
    incoming = tmp_path / "mystery.flac"
    incoming.write_bytes(b"not really audio")
    monkeypatch.setattr(pipeline, "probe_decoded_duration", lambda *_a, **_k: 0.0)
    assert pipeline._replacement_length_is_safe(str(existing), str(incoming)) is True


def test_zero_length_header_falls_back_to_decode(tmp_path, monkeypatch):
    # the HiFi shape: mutagen length 0, real audio 30s via decode
    existing = _wav(tmp_path / "library.wav", 220.0)
    incoming = tmp_path / "hifi.flac"
    incoming.write_bytes(b"fake flac")
    monkeypatch.setattr(pipeline, "probe_decoded_duration",
                        lambda p, *a, **k: 30.0 if "hifi" in str(p) else 0.0)
    # existing resolves via its real WAV header (220s), incoming via decode (30s)
    assert pipeline._replacement_length_is_safe(str(existing), str(incoming)) is False


def test_the_guard_is_wired_into_the_replace_path():
    src = Path(pipeline.__file__).read_text(encoding="utf-8", errors="replace")
    # called before any os.remove(final_path) in the destination-exists block
    assert "_replacement_length_is_safe(final_path, file_path)" in src
    assert "Refusing to replace" in src
