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

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from utils.logging_config import get_logger


logger = get_logger("imports.file_integrity")

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
        return IntegrityResult(
            ok=False,
            reason="Mutagen reports zero-length audio — file has no playable "
                   "audio data",
            checks={**checks, "mutagen_parse": "zero_length"},
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
    checks["length_tolerance_s"] = length_tolerance_s

    drift_s = abs(actual_length_s - expected_length_s)
    checks["length_drift_s"] = drift_s

    if drift_s > length_tolerance_s:
        return IntegrityResult(
            ok=False,
            reason=f"Duration mismatch: file is {actual_length_s:.1f}s, "
                   f"expected {expected_length_s:.1f}s "
                   f"(drift {drift_s:.1f}s > tolerance {length_tolerance_s:.1f}s) — "
                   "likely truncated download or wrong file matched",
            checks=checks,
        )

    checks["length_check"] = "passed"
    return IntegrityResult(ok=True, checks=checks)
