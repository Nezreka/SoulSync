"""Regression tests for file integrity checks on downloaded audio.

Discord-reported (fresh.dumbledore [VRN]): slskd sometimes hosts broken
files (truncated transfers, corrupted FLAC, wrong file masquerading as
the target). The integrity layer at ``core/imports/file_integrity.py``
catches these before they reach tagging/library sync, using three
universal checks: file-size sanity, mutagen parse, and duration
agreement against the metadata-source-provided expected length.

These tests exercise the module directly with fabricated files (real
mp3 + flac samples generated via mutagen-friendly stubs and a couple of
hand-written WAV/FLAC files) so we don't need ffmpeg or live downloads.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.imports import file_integrity


def _write_minimal_wav(path: Path, duration_s: float = 1.0, sample_rate: int = 8000) -> None:
    """Write a minimal valid WAV file. Mutagen parses WAV via the
    standard wave module wrapper, giving us a real `info.length`
    we can assert against without needing ffmpeg."""
    n_samples = int(duration_s * sample_rate)
    n_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * n_channels * bits_per_sample // 8
    block_align = n_channels * bits_per_sample // 8
    data_size = n_samples * block_align
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ", 16, 1, n_channels, sample_rate, byte_rate, block_align, bits_per_sample,
    )
    data_chunk = struct.pack("<4sI", b"data", data_size) + (b"\x00\x00" * n_samples)
    riff = struct.pack("<4sI4s", b"RIFF", 4 + len(fmt_chunk) + len(data_chunk), b"WAVE")
    path.write_bytes(riff + fmt_chunk + data_chunk)


# ---------------------------------------------------------------------------
# File size check
# ---------------------------------------------------------------------------


def test_rejects_zero_byte_file(tmp_path: Path) -> None:
    """A 0-byte file is the most common slskd-broken case."""
    f = tmp_path / "empty.flac"
    f.write_bytes(b"")

    result = file_integrity.check_audio_integrity(str(f))

    assert result.ok is False
    assert "too small" in result.reason.lower()
    assert result.checks["size_bytes"] == 0


def test_rejects_tiny_stub(tmp_path: Path) -> None:
    """A few hundred bytes can't be a real audio file — slskd dropped a stub."""
    f = tmp_path / "stub.mp3"
    f.write_bytes(b"x" * 500)

    result = file_integrity.check_audio_integrity(str(f))

    assert result.ok is False
    assert "too small" in result.reason.lower()


def test_size_threshold_is_overridable(tmp_path: Path) -> None:
    """Tests / dev workflows can lower the size threshold."""
    f = tmp_path / "small_but_intentional.bin"
    f.write_bytes(b"y" * 100)

    # Should pass the size check at threshold=50, then fail mutagen parse
    # since it's not real audio.
    result = file_integrity.check_audio_integrity(str(f), min_file_size_bytes=50)

    assert result.ok is False
    assert "mutagen" in result.reason.lower() or "could not" in result.reason.lower()


def test_missing_file_returns_clean_failure(tmp_path: Path) -> None:
    """No exception should escape — caller wants a clean boolean."""
    result = file_integrity.check_audio_integrity(str(tmp_path / "ghost.flac"))

    assert result.ok is False
    assert "stat" in result.reason.lower() or "cannot" in result.reason.lower()


# ---------------------------------------------------------------------------
# Mutagen parse check
# ---------------------------------------------------------------------------


def test_rejects_non_audio_file_with_audio_extension(tmp_path: Path) -> None:
    """A text file renamed to .flac (sometimes happens when slskd matches
    a wrong file) should fail the parse check, not slip through."""
    f = tmp_path / "fake.flac"
    # Big enough to clear the size check, but not audio.
    f.write_bytes(b"this is definitely not flac data\n" * 1000)

    result = file_integrity.check_audio_integrity(str(f))

    assert result.ok is False
    # Either mutagen returns None (unidentified) or raises — either is a fail.
    assert "mutagen" in result.reason.lower() or "no info" in result.reason.lower() or "identify" in result.reason.lower()


def test_accepts_valid_wav_with_no_expected_duration(tmp_path: Path) -> None:
    """Real audio with no caller-provided duration should pass — only
    size + parse run."""
    f = tmp_path / "real.wav"
    _write_minimal_wav(f, duration_s=2.0)

    result = file_integrity.check_audio_integrity(str(f))

    assert result.ok is True
    assert result.checks["actual_length_s"] == pytest.approx(2.0, abs=0.1)
    assert result.checks["length_check"] == "skipped"


# ---------------------------------------------------------------------------
# Duration agreement check
# ---------------------------------------------------------------------------


def test_accepts_when_length_within_tolerance(tmp_path: Path) -> None:
    """A 5-second file claiming 5.5 seconds (within 3s tolerance) passes."""
    f = tmp_path / "track.wav"
    _write_minimal_wav(f, duration_s=5.0)

    result = file_integrity.check_audio_integrity(str(f), expected_duration_ms=5500)

    assert result.ok is True
    assert result.checks["length_check"] == "passed"
    assert result.checks["length_drift_s"] == pytest.approx(0.5, abs=0.2)


def test_rejects_truncated_file(tmp_path: Path) -> None:
    """A 2-second file claiming to be a 30-second track is truncated.
    This is the headline slskd case — bytes stopped flowing partway
    through but slskd reported success."""
    f = tmp_path / "truncated.wav"
    _write_minimal_wav(f, duration_s=2.0)

    result = file_integrity.check_audio_integrity(str(f), expected_duration_ms=30_000)

    assert result.ok is False
    assert "duration" in result.reason.lower() or "drift" in result.reason.lower()
    assert result.checks["length_drift_s"] > 3.0


def test_rejects_wrong_file_substituted(tmp_path: Path) -> None:
    """A 10-second clip masquerading as a 3-minute album track. slskd
    matched on a similar filename but the actual content is a snippet."""
    f = tmp_path / "wrong.wav"
    _write_minimal_wav(f, duration_s=10.0)

    result = file_integrity.check_audio_integrity(str(f), expected_duration_ms=180_000)

    assert result.ok is False
    assert result.checks["length_drift_s"] > 100


def test_long_track_uses_wider_tolerance(tmp_path: Path) -> None:
    """Tracks over 10 minutes get 5s tolerance instead of 3s — long
    tracks naturally drift more (intros, outros, encoder padding)."""
    # Write a 12-minute file (720s) but at minimum sample rate to keep
    # the test fast — under 30KB total.
    f = tmp_path / "long.wav"
    _write_minimal_wav(f, duration_s=720.0, sample_rate=8000)

    # Claim 724 seconds — 4s drift, which would fail the 3s default but
    # passes the 5s long-track threshold.
    result = file_integrity.check_audio_integrity(str(f), expected_duration_ms=724_000)

    assert result.ok is True
    assert result.checks["length_tolerance_s"] == pytest.approx(5.0)


def test_caller_can_override_tolerance(tmp_path: Path) -> None:
    """Edge cases (e.g. live recordings, known-flaky sources) can opt
    into a wider tolerance per-call."""
    f = tmp_path / "loose.wav"
    _write_minimal_wav(f, duration_s=5.0)

    # 8-second drift — would fail default 3s, passes explicit 10s.
    result = file_integrity.check_audio_integrity(
        str(f), expected_duration_ms=13_000, length_tolerance_s=10.0,
    )

    assert result.ok is True


def test_zero_expected_duration_skips_length_check(tmp_path: Path) -> None:
    """Some metadata sources don't carry duration — duration check
    must be skipped, not treated as a 0-length match."""
    f = tmp_path / "no_duration.wav"
    _write_minimal_wav(f, duration_s=5.0)

    result = file_integrity.check_audio_integrity(str(f), expected_duration_ms=0)

    assert result.ok is True
    assert result.checks["length_check"] == "skipped"


def test_negative_expected_duration_skips_length_check(tmp_path: Path) -> None:
    """Defensive: bad metadata returning negative duration shouldn't
    crash or false-reject."""
    f = tmp_path / "neg_duration.wav"
    _write_minimal_wav(f, duration_s=5.0)

    result = file_integrity.check_audio_integrity(str(f), expected_duration_ms=-100)

    assert result.ok is True
    assert result.checks["length_check"] == "skipped"


# ---------------------------------------------------------------------------
# Failure-mode robustness
# ---------------------------------------------------------------------------


def test_check_never_raises(tmp_path: Path, monkeypatch) -> None:
    """The integrity check is wrapped in try/except in pipeline.py but
    callers shouldn't have to. Verify that even pathological inputs
    return a clean IntegrityResult."""
    f = tmp_path / "test.wav"
    _write_minimal_wav(f, duration_s=2.0)

    # Force a mutagen import-time failure by stubbing the import.
    # Should NOT raise — should pass gracefully (mutagen unavailable).
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _broken_import(name, *args, **kwargs):
        if name == "mutagen":
            raise ImportError("simulated missing mutagen")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(__builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__,
                        "__import__", _broken_import)

    try:
        result = file_integrity.check_audio_integrity(str(f))
    except Exception as e:
        pytest.fail(f"check_audio_integrity raised: {e}")

    assert result.ok is True
    assert result.checks.get("mutagen_parse") == "unavailable"
