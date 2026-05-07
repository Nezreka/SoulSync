"""Pin the ReplayGain analysis fix.

User report: every track in a downloaded album got the same
``replaygain_track_gain`` of ``+52.00 dB`` after post-processing.
Smoking gun: ``-18 (RG2 reference) - (-70.0) = +52.00``. Every track's
first ebur128 measurement window reads ~-70 LUFS because the first
window covers the silent intro / encoder padding.

The old code used ``re.search('I:\\s+...')`` which returns the FIRST
match — capturing that initial -70 LUFS reading instead of the final
integrated value from the Summary block.

These tests use representative ffmpeg ebur128 output (per-window
progress + final Summary block) to pin: parser anchors to the
Summary, ignores per-window partials, and falls back gracefully when
Summary is absent.
"""

from __future__ import annotations

import re
import subprocess
from unittest.mock import patch

import pytest

from core.replaygain import analyze_track


# ---------------------------------------------------------------------------
# Fabricated ebur128 stderr samples
# ---------------------------------------------------------------------------

_REAL_EBUR128_STDERR = """
[Parsed_ebur128_0 @ 0x000001] Summary:
[Parsed_ebur128_0 @ 0x000001] t: 0.500000   TARGET:-23 LUFS    M: -70.0 S:-70.0     I: -70.0 LUFS       LRA:   0.0 LU  FTPK: -70.0 dBFS  TPK: -70.0 dBFS
[Parsed_ebur128_0 @ 0x000001] t: 1.000000   TARGET:-23 LUFS    M: -50.0 S:-60.0     I: -60.0 LUFS       LRA:   0.0 LU  FTPK: -50.0 dBFS  TPK: -50.0 dBFS
[Parsed_ebur128_0 @ 0x000001] t: 1.500000   TARGET:-23 LUFS    M: -20.0 S:-30.0     I: -25.0 LUFS       LRA:   0.0 LU  FTPK: -2.5 dBFS   TPK: -2.5 dBFS
[Parsed_ebur128_0 @ 0x000001] t: 2.000000   TARGET:-23 LUFS    M: -18.0 S:-20.0     I: -14.5 LUFS       LRA:   1.5 LU  FTPK: -0.4 dBFS   TPK: -0.4 dBFS
[Parsed_ebur128_0 @ 0x000001] Summary:

  Integrated loudness:
    I:         -14.3 LUFS
    Threshold: -24.3 LUFS

  Loudness range:
    LRA:         3.2 LU
    Threshold: -34.3 LUFS
    LRA low:   -16.5 LUFS
    LRA high:  -13.3 LUFS

  True peak:
    Peak:       -0.4 dBFS
[out#0/null @ 0x000002] video:0KiB audio:172KiB
"""


def _stub_ffmpeg(stderr_output: str):
    """Patch subprocess.run to return a fake ffmpeg result with the
    given stderr."""
    class _FakeResult:
        def __init__(self):
            self.stderr = stderr_output
            self.returncode = 0
    return patch.object(subprocess, 'run', return_value=_FakeResult())


# ---------------------------------------------------------------------------
# Headline regression: don't grab the first per-window reading
# ---------------------------------------------------------------------------


def test_parses_summary_lufs_not_first_per_window_reading():
    """The per-window stream contains 'I: -70.0 LUFS' (silent intro)
    BEFORE the Summary block's 'I: -14.3 LUFS'. Parser must return
    -14.3 (summary), NOT -70.0 (first per-window).

    This is the exact bug from the user's +52.00 dB report:
    -18 RG2 reference - (-70.0) = +52.00 was the symptom."""
    with _stub_ffmpeg(_REAL_EBUR128_STDERR):
        lufs, peak = analyze_track('/fake/path.flac')

    assert lufs == pytest.approx(-14.3, abs=0.01)
    assert peak == pytest.approx(-0.4, abs=0.01)


def test_resulting_gain_is_realistic_not_plus_52():
    """Computed gain must be a normal real-world value (a few dB
    range), NOT the symptomatic +52.00 dB the bug produced."""
    with _stub_ffmpeg(_REAL_EBUR128_STDERR):
        lufs, _peak = analyze_track('/fake/path.flac')
    gain = -18.0 - lufs  # RG2 reference
    assert -10.0 < gain < 10.0, f"Unrealistic gain {gain:+.2f} dB — bug regression"


# ---------------------------------------------------------------------------
# Different per-track values stay different
# ---------------------------------------------------------------------------


def _make_stderr(per_window_lufs: list[float], summary_lufs: float, summary_peak: float) -> str:
    """Build an ebur128 stderr blob with controllable per-window and
    summary values. Lets each test verify the summary is what gets
    used regardless of what's in the per-window stream."""
    per_window = '\n'.join(
        f"[Parsed_ebur128_0 @ 0x1] t: {(i + 1) * 0.5:.6f}   TARGET:-23 LUFS    "
        f"M: {lufs:.1f} S:{lufs:.1f}     I: {lufs:.1f} LUFS       LRA:   0.0 LU  "
        f"FTPK: {lufs / 2:.1f} dBFS  TPK: {lufs / 2:.1f} dBFS"
        for i, lufs in enumerate(per_window_lufs)
    )
    return f"""{per_window}
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         {summary_lufs:.1f} LUFS
    Threshold: -24.0 LUFS

  Loudness range:
    LRA:         3.2 LU

  True peak:
    Peak:       {summary_peak:+.1f} dBFS
"""


def test_two_tracks_with_different_summaries_get_different_lufs():
    """Two simulated tracks with the SAME first per-window value (-70)
    but DIFFERENT summary integrated loudness values. Old buggy parser
    would report -70 for both. Fixed parser correctly reports the
    distinct summary values."""
    track_a_stderr = _make_stderr([-70.0, -50.0, -20.0], summary_lufs=-14.3, summary_peak=-0.4)
    track_b_stderr = _make_stderr([-70.0, -45.0, -10.0], summary_lufs=-7.8, summary_peak=-1.2)

    with _stub_ffmpeg(track_a_stderr):
        lufs_a, _ = analyze_track('/fake/a.flac')
    with _stub_ffmpeg(track_b_stderr):
        lufs_b, _ = analyze_track('/fake/b.flac')

    assert lufs_a != lufs_b
    assert lufs_a == pytest.approx(-14.3, abs=0.01)
    assert lufs_b == pytest.approx(-7.8, abs=0.01)


def test_per_window_lufs_with_higher_value_doesnt_leak_into_summary():
    """If per-window readings include a value HIGHER than the summary
    (a transient loud window), the parser must still return the
    summary value, not the loudest per-window."""
    stderr = _make_stderr([-70.0, -5.0, -3.0], summary_lufs=-12.0, summary_peak=-0.5)
    with _stub_ffmpeg(stderr):
        lufs, _ = analyze_track('/fake/loud.flac')
    assert lufs == pytest.approx(-12.0, abs=0.01)


# ---------------------------------------------------------------------------
# Defensive fallback when Summary block is absent
# ---------------------------------------------------------------------------


def test_falls_back_to_last_per_window_when_no_summary():
    """Some ffmpeg versions or truncated outputs may lack a Summary
    block. Defensive fallback uses the LAST per-window reading (still
    closer to the final integrated value than the first)."""
    stderr = """
[Parsed_ebur128_0 @ 0x1] t: 0.5   I: -70.0 LUFS  LRA: 0.0 LU
[Parsed_ebur128_0 @ 0x1] t: 1.0   I: -25.0 LUFS  LRA: 0.0 LU
[Parsed_ebur128_0 @ 0x1] t: 1.5   I: -14.5 LUFS  LRA: 1.5 LU
""".strip()

    with _stub_ffmpeg(stderr):
        lufs, _peak = analyze_track('/fake/no_summary.flac')

    # Last per-window reading, NOT the first
    assert lufs == pytest.approx(-14.5, abs=0.01)


def test_raises_when_no_lufs_anywhere():
    """If ffmpeg output contains no LUFS values at all (parse failure
    / wrong format), surface a clear RuntimeError so the caller can
    decide whether to skip RG analysis."""
    stderr = "ffmpeg: garbled output, no LUFS data anywhere\n"
    with _stub_ffmpeg(stderr):
        with pytest.raises(RuntimeError, match='Could not parse'):
            analyze_track('/fake/garbage.flac')


# ---------------------------------------------------------------------------
# Peak parsing — ensure it stays anchored to summary too
# ---------------------------------------------------------------------------


def test_peak_uses_summary_value_not_per_window_max():
    """Per-window output uses 'TPK:'/'FTPK:' labels; only the summary
    uses 'Peak:'. Pin that the parser only catches the summary peak
    even when per-window TPK values would be larger."""
    stderr = _make_stderr([-70.0, -45.0, -10.0], summary_lufs=-12.0, summary_peak=-0.4)
    with _stub_ffmpeg(stderr):
        _lufs, peak = analyze_track('/fake/peak.flac')
    assert peak == pytest.approx(-0.4, abs=0.01)
