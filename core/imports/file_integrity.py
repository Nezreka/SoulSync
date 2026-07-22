"""Audio file integrity checks for downloaded files.

slskd (and other download sources) sometimes ship broken files: truncated
transfers, corrupted FLAC frames, mp3s with bad headers, or wrong files
that share a name with the target. These slip past the slskd "completed"
status and only get caught later (often by Plex/Jellyfin failing to scan
the file, or by users hearing dead air).

Verification runs after the slskd transfer settles but before the heavy
post-processing work (tagging, copying, server sync). Failed files get
quarantined and the slot is freed for a retry from another candidate.

Three checks, in order from cheapest to most expensive:

1. **File-size sanity** — anything below ~10KB is almost certainly a
   stub, broken transfer, or non-audio masquerading as audio.
2. **Mutagen parse** — catches truncated headers, corrupted streamheaders,
   wrong-format files (mp3 with .flac extension, etc). If mutagen can't
   parse the audio info block, the file won't import cleanly downstream.
3. **Duration agreement** — if the caller provides an expected duration
   (Spotify/MusicBrainz `duration_ms`), the decoded length must agree
   within tolerance. Catches truncated files whose headers parse fine
   but whose audio is incomplete, and "wrong file" cases the slskd
   transfer matched on a similarly-named track.

This is the "tier 1" integrity layer — universal across formats, no
external binary dep. A future tier could verify the FLAC STREAMINFO MD5
by actually decoding the audio (requires `flac` binary or libflac
wrapper); skipped for now since tier 1 catches the vast majority of
real-world corruption.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logging_config import get_logger


logger = get_logger("imports.file_integrity")


def _find_ffmpeg() -> Optional[str]:
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    cand = Path(__file__).parent.parent.parent / 'tools' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    return str(cand) if cand.exists() else None


def _parse_ffmpeg_time(stderr_text: str) -> float:
    """The last ``time=HH:MM:SS.xx`` ffmpeg prints while decoding — the REAL
    decoded length, immune to a faked container/STREAMINFO duration. 0.0 if
    not found."""
    last = 0.0
    for m in re.finditer(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', stderr_text or ''):
        last = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return last


def probe_decoded_duration(file_path: str, timeout: int = 180) -> float:
    """Decode the audio with ffmpeg and return its REAL length in seconds.

    This is the ground truth a HiFi preview can't fake: a 30s clip whose
    container/STREAMINFO claims full length still decodes to 30s. 0.0 when
    ffmpeg is unavailable or on any error — callers treat 0.0 as 'unknown',
    never as 'preview'."""
    ff = _find_ffmpeg()
    if not ff:
        return 0.0
    try:
        proc = subprocess.run(
            [ff, '-hide_banner', '-nostdin', '-i', str(file_path),
             '-map', '0:a:0', '-f', 'null', '-'],
            capture_output=True, text=True, timeout=timeout)
        return _parse_ffmpeg_time(proc.stderr)
    except Exception:   # noqa: BLE001 - probe failure is 'unknown', never a reject
        return 0.0

# Minimum plausible audio file size. A 1-second 64kbps mp3 is ~8KB; a
# 1-second FLAC is much larger. Anything under this is a broken stub.
_MIN_FILE_SIZE_BYTES = 10 * 1024

# Default tolerance for duration agreement. Most legitimate length
# variations (intro silence, encoder padding, live recording trims) sit
# inside 3 seconds. Goes up to 5s if the expected duration is itself
# long (>10 minutes) since absolute drift scales with length.
_DEFAULT_LENGTH_TOLERANCE_S = 3.0
_LENGTH_TOLERANCE_LONG_TRACK_S = 5.0
_LONG_TRACK_THRESHOLD_S = 600.0  # 10 minutes

# A file that runs LONGER than the expected metadata is the opposite of a truncated
# download — it's almost always a different master/version (a remaster with a longer
# outro, an extended fade, an album cut vs the radio edit). The duration check exists to
# catch TRUNCATION (short files) and wildly-wrong matches, so on the auto default we allow
# more drift in the longer direction and keep the tight bound for short files. A wrong-song
# match still trips this — it's usually off by far more than 15s. (#937)
_LONGER_VERSION_TOLERANCE_S = 15.0

# Upper bound for the user-configurable override. Anything past 60s
# means the check is effectively off — cap defends against accidental
# nonsense like 9999 making logs misleading. Users who genuinely want
# to disable the check can set 60.
_MAX_USER_TOLERANCE_S = 60.0


def resolve_duration_tolerance(value: Any) -> Optional[float]:
    """Coerce a user-configured tolerance value to a float override.

    Returns:
        - None when value is missing / 0 / negative / unparseable, so
          callers fall back to the auto-scaled defaults (3s/5s).
        - float in (0, _MAX_USER_TOLERANCE_S] when value is a positive
          numeric string or float — clamped to the upper bound.

    Pure helper. No I/O. Drives the `length_tolerance_s` override on
    `check_audio_integrity`.
    """
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    if parsed > _MAX_USER_TOLERANCE_S:
        return _MAX_USER_TOLERANCE_S
    return parsed


def expected_duration_for_check(expected_ms: Any, is_local_import: bool) -> Optional[int]:
    """The expected duration (ms) to run the duration-agreement leg against,
    or None to skip that leg.

    The duration check exists to catch BROKEN slskd TRANSFERS (truncated /
    wrong-file downloads). A local/manual import is the user's own already-
    tagged file being sorted, not a transfer — duration-agreeing it against a
    re-resolved release is meaningless and produces false quarantines (#804:
    Coldplay "Yellow" album file, 269s, false-rejected against a *single*
    edition's 266s). So for local imports we skip the duration leg; the
    size + mutagen-parse legs still run and catch genuinely broken files.
    """
    if is_local_import:
        return None
    try:
        return int(expected_ms) or None
    except (TypeError, ValueError):
        return None


@dataclass
class IntegrityResult:
    """Outcome of an integrity check.

    `ok` is the single bit the caller cares about. `reason` is the
    human-readable explanation when `ok` is False (suitable for
    quarantine sidecar / log lines / UI). `checks` carries the
    per-check details — useful for debugging and tests.
    """

    ok: bool
    reason: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)


def check_audio_integrity(
    file_path: str,
    expected_duration_ms: Optional[int] = None,
    *,
    length_tolerance_s: Optional[float] = None,
    min_file_size_bytes: int = _MIN_FILE_SIZE_BYTES,
) -> IntegrityResult:
    """Verify a downloaded audio file is not broken.

    Args:
        file_path: Path to the audio file on disk.
        expected_duration_ms: Expected track length from the metadata
            source (Spotify/MB/etc). If None, the duration check is
            skipped and only the size + parse checks run.
        length_tolerance_s: Override the default tolerance for the
            duration check. None uses the auto-scaled default
            (3s for normal tracks, 5s for >10min tracks).
        min_file_size_bytes: Override the minimum size threshold.

    Returns:
        IntegrityResult with `ok`, `reason`, and per-check details.
        Never raises — all errors become `ok=False` with an explanatory
        reason, so callers can rely on a clean boolean.
    """
    import os

    checks: Dict[str, Any] = {}

    # --- Check 1: file size ---
    try:
        size = os.path.getsize(file_path)
    except OSError as exc:
        return IntegrityResult(ok=False, reason=f"Cannot stat file: {exc}",
                               checks={"size": "stat_failed"})

    checks["size_bytes"] = size
    if size < min_file_size_bytes:
        return IntegrityResult(
            ok=False,
            reason=f"File too small ({size} bytes, minimum {min_file_size_bytes}) — "
                   "likely truncated transfer or empty stub",
            checks=checks,
        )

    # --- Check 2: mutagen parse ---
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        # mutagen is a hard dep elsewhere in the codebase, but degrade
        # gracefully if it's somehow missing — pass with a warning
        # rather than failing every download.
        logger.warning("[Integrity] mutagen unavailable — skipping parse check")
        checks["mutagen_parse"] = "unavailable"
        return IntegrityResult(ok=True, checks=checks)

    try:
        audio = MutagenFile(file_path)
    except Exception as exc:
        return IntegrityResult(
            ok=False,
            reason=f"Mutagen could not parse file: {exc}",
            checks={**checks, "mutagen_parse": "exception"},
        )

    if audio is None:
        return IntegrityResult(
            ok=False,
            reason="Mutagen could not identify file format — likely corrupted "
                   "or wrong file extension",
            checks={**checks, "mutagen_parse": "unidentified"},
        )

    if audio.info is None:
        return IntegrityResult(
            ok=False,
            reason="Mutagen parsed file but found no audio info block — "
                   "header damage suspected",
            checks={**checks, "mutagen_parse": "no_info"},
        )

    actual_length_s = float(getattr(audio.info, "length", 0) or 0)
    checks["actual_length_s"] = actual_length_s

    if actual_length_s <= 0:
        # Length 0 is NOT proof of corruption here: the file already passed the
        # size gate, was identified as a real audio format, and has a valid
        # info block. A genuinely empty/truncated/stub file fails one of those
        # earlier checks instead. The real cause of a clean-but-zero-length
        # parse is "length unknown" — fragmented / streamed FLAC carries
        # total_samples=0 in its STREAMINFO even though every audio frame is
        # present and the file plays fine. HiFi is the common trigger: it
        # assembles FLAC from HLS segments and demuxes with `ffmpeg -c copy`,
        # which preserves total_samples=0, so mutagen computes length 0 (#756).
        #
        # This exact zero is ALSO how a HiFi 30s PREVIEW arrives — the faked
        # STREAMINFO reads total_samples=0 while only ~30s of frames exist —
        # and blindly accepting here is how those clips replaced real library
        # files (sella's incident). So when we have an expected duration, DECODE
        # the real length with ffmpeg (the one signal a preview can't fake)
        # before trusting a zero-length file. No expected duration or no ffmpeg:
        # fall back to the old accept (a good streamed FLAC must not be
        # quarantined), and the replace-side length guard is the backstop.
        if expected_duration_ms and expected_duration_ms > 0:
            decoded_s = probe_decoded_duration(file_path)
            checks["decoded_length_s"] = decoded_s
            if decoded_s > 0:
                expected_s = expected_duration_ms / 1000.0
                if decoded_s < expected_s * 0.8:
                    return IntegrityResult(
                        ok=False,
                        reason=f"Decoded audio is only {decoded_s:.0f}s of an "
                               f"expected {expected_s:.0f}s (zero-length header) — "
                               "a preview clip or truncated download",
                        checks={**checks, "mutagen_parse": "zero_length_decoded_short"},
                    )
                logger.info(
                    "[Integrity] %s reports length 0 but decodes to %.0fs (expected "
                    "%.0fs) — accepting (streamed/fragmented FLAC)",
                    os.path.basename(file_path), decoded_s, expected_s,
                )
                return IntegrityResult(
                    ok=True,
                    checks={**checks, "mutagen_parse": "zero_length_decoded_ok",
                            "length_check": "passed_decoded"},
                )
        logger.warning(
            "[Integrity] %s parsed cleanly (%d bytes, format=%s) but reports "
            "length 0 and no decode was possible — treating as unknown length "
            "(likely streamed/fragmented FLAC), not rejecting",
            os.path.basename(file_path), size, type(audio).__name__,
        )
        return IntegrityResult(
            ok=True,
            checks={**checks, "mutagen_parse": "zero_length_unknown",
                    "length_check": "skipped_unknown_length"},
        )

    # --- Check 3: duration agreement (optional) ---
    if expected_duration_ms is None or expected_duration_ms <= 0:
        checks["length_check"] = "skipped"
        return IntegrityResult(ok=True, checks=checks)

    expected_length_s = expected_duration_ms / 1000.0
    checks["expected_length_s"] = expected_length_s

    if length_tolerance_s is None:
        length_tolerance_s = (
            _LENGTH_TOLERANCE_LONG_TRACK_S
            if expected_length_s > _LONG_TRACK_THRESHOLD_S
            else _DEFAULT_LENGTH_TOLERANCE_S
        )
        user_pinned_tolerance = False
    else:
        user_pinned_tolerance = True
    checks["length_tolerance_s"] = length_tolerance_s

    # Positive drift = the file runs LONGER than expected (not truncation). On the auto
    # default, give the longer direction more room so legit longer masters/versions aren't
    # quarantined (#937); a user-pinned tolerance is honoured symmetrically.
    signed_drift_s = actual_length_s - expected_length_s
    drift_s = abs(signed_drift_s)
    checks["length_drift_s"] = drift_s
    effective_tolerance_s = length_tolerance_s
    if signed_drift_s > 0 and not user_pinned_tolerance:
        effective_tolerance_s = max(length_tolerance_s, _LONGER_VERSION_TOLERANCE_S)
    checks["effective_tolerance_s"] = effective_tolerance_s

    if drift_s > effective_tolerance_s:
        runs_long = signed_drift_s > 0
        return IntegrityResult(
            ok=False,
            reason=f"Duration mismatch: file is {actual_length_s:.1f}s, "
                   f"expected {expected_length_s:.1f}s "
                   f"(drift {drift_s:.1f}s > tolerance {effective_tolerance_s:.1f}s) — "
                   + ("runs longer than expected — likely a different version/master or wrong file"
                      if runs_long
                      else "likely truncated download or wrong file matched"),
            checks=checks,
        )

    checks["length_check"] = "passed"
    return IntegrityResult(ok=True, checks=checks)
