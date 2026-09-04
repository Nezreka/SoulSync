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


def duration_reference_for_context(expected_ms: Any, context: Any) -> Any:
    """The duration the integrity check should measure against.

    Normally that's the source's own duration. But when we deliberately took a
    different version than the source asked for (Settings → prefer a version),
    the source's number describes another recording — an extended mix runs
    minutes past the radio edit Spotify listed, so checking against it would
    quarantine every file the setting went and found on purpose.

    The download stamps the length the peer advertised on the context. That
    still catches a truncated transfer, which is what the check is for. A
    stamped None means the peer advertised nothing, so there's no honest
    reference and the duration leg is skipped (size + parse legs still run).

    Pure helper. No I/O. Returns ``expected_ms`` untouched when no version was
    swapped, so the ordinary path is unaffected.
    """
    if isinstance(context, dict) and '_preferred_version_duration_ms' in context:
        return context.get('_preferred_version_duration_ms') or None
    return expected_ms


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


#: Below this fraction of raw PCM, a lossless file is worth LOOKING at. It is a
#: trigger, NOT a verdict, and the difference matters: dense material (pop, rock,
#: rap) sits at 40-75% and preview clips sat at 10-28%, so it is tempting to
#: treat the gap as proof. It is not. Quiet or sparse music compresses just as
#: hard as a fake - measured on real encodes, ambient came out at 6.5% and very
#: soft material at 24.7%, both complete files. Rejecting on this number alone
#: quarantines them. Everything below confirms with a decode before acting.
LOSSLESS_MIN_DENSITY = 0.30

#: Codecs that carry whole samples. An MP4 container holds either, so the
#: CONTAINER cannot answer this - only the codec can.
_LOSSLESS_MP4_CODECS = ("alac",)

#: Concrete mutagen types for lossless formats. Names, because that is what
#: MutagenFile hands back. WAVE/AIFF are uncompressed so they can never trip a
#: density trigger; they are listed for correctness, not effect.
_LOSSLESS_TYPES = ("FLAC", "WAVE", "AIFF", "WavPack", "TrueAudio", "MonkeysAudio")


def is_lossless_audio(audio) -> bool:
    """True when this file is a lossless format.

    The density check below is meaningless for lossy audio - a 128kbps MP3 is
    SUPPOSED to be 9% of raw PCM - so getting this wrong quarantines healthy
    files. Two traps, both found by testing real files rather than reasoning:

    * bits_per_sample is NOT the discriminator. A lossy AAC in an .m4a reports
      bits_per_sample=16, exactly like lossless ALAC in the same container.
      Only the codec string separates them.
    * The extension is not either: .m4a is both.
    """
    info = getattr(audio, "info", None)
    if info is None:
        return False
    cls = type(audio).__name__
    if cls in _LOSSLESS_TYPES:
        return True
    if cls == "MP4":
        codec = str(getattr(info, "codec", "") or "").lower()
        return any(codec.startswith(c) for c in _LOSSLESS_MP4_CODECS)
    return False


def raw_pcm_bitrate(sample_rate, bits_per_sample, channels) -> int:
    """Uncompressed bits per second, or 0 when any dimension is unknown."""
    try:
        sr, bits, ch = int(sample_rate or 0), int(bits_per_sample or 0), int(channels or 0)
    except (TypeError, ValueError):
        return 0
    return sr * bits * ch if sr > 0 and bits > 0 and ch > 0 else 0


def is_fake_lossless_bitrate(size_bytes, claimed_seconds, sample_rate, bits_per_sample,
                             channels, min_ratio: float = LOSSLESS_MIN_DENSITY) -> bool:
    """True when a 'lossless' file's data is FAR too small for its claimed length - the
    fingerprint of a preview clip padded (or truncated) to the full duration, so every
    length header reads 'full' and only the size gives it away.

    Size is the one thing a header cannot lie about, which makes this a very cheap
    SUSPICION - but not a verdict on its own: quiet or sparse music is thin for the
    same reason a preview is. Callers must confirm before quarantining.
    Conservative: 0 / bad inputs return False (never flag on unknowns)."""
    try:
        sz, secs = float(size_bytes or 0), float(claimed_seconds or 0)
    except (TypeError, ValueError):
        return False
    raw = raw_pcm_bitrate(sample_rate, bits_per_sample, channels)
    if sz <= 0 or secs <= 0 or raw <= 0:
        return False
    return (sz * 8 / secs) < raw * min_ratio


def _confirm_broken_audio(file_path: str) -> Optional[str]:
    """Decode once and say what is actually wrong, or None when the audio is fine.

    Imported lazily: core.imports.silence shells out to ffmpeg, and this module is
    imported in places that never decode anything.

    Fails OPEN. Without ffmpeg there is no way to tell a preview from a quiet
    recording, and quarantining someone's ambient album because a tool is missing
    is worse than missing a fake. The caller only reaches here for files already
    flagged as thin, so this is the difference between "suspicious" and "proven".
    """
    try:
        from core.imports.silence import detect_broken_audio
    except Exception:   # noqa: BLE001 - a missing guard must not break the check
        logger.debug("audio confirmation unavailable", exc_info=True)
        return None
    try:
        return detect_broken_audio(file_path)
    except Exception:   # noqa: BLE001
        logger.debug("audio confirmation raised for %s", file_path, exc_info=True)
        return None


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

    # --- Check 3: thin lossless file, confirmed by decode ---
    #
    # A preview padded to full length declares the right duration at every layer,
    # so the size/parse/duration legs all pass it. Its BYTES give it away: 30s of
    # audio spread over a 7 minute claim implies a bitrate no lossless codec
    # produces.
    #
    # But thin is ALSO what quiet music looks like - real ambient encodes measured
    # 6.5% - so this only decides which files are worth a decode. The decode is
    # what decides. detect_broken_audio makes one pass that catches both shapes a
    # fake takes: audio that stops early, and audio padded out with silence. A
    # padded preview shows 90s of silence; quiet-but-complete music shows none.
    #
    # Cost stays where it belongs: a healthy library triggers almost nothing.
    if actual_length_s > 0 and is_lossless_audio(audio):
        _raw = raw_pcm_bitrate(getattr(audio.info, "sample_rate", 0),
                               getattr(audio.info, "bits_per_sample", 0),
                               getattr(audio.info, "channels", 0))
        if _raw > 0:
            _density = (size * 8 / actual_length_s) / _raw
            checks["lossless_density"] = round(_density, 4)
            if _density < LOSSLESS_MIN_DENSITY:
                checks["lossless_density_suspicious"] = True
                _broken = _confirm_broken_audio(file_path)
                if _broken:
                    return IntegrityResult(
                        ok=False,
                        reason=f"Lossless file holds only {_density * 100:.0f}% of the data its "
                               f"{actual_length_s:.0f}s runtime needs "
                               f"({size * 8 / actual_length_s / 1000:.0f}kbps) and {_broken} — "
                               "a preview clip or truncated download",
                        checks={**checks, "length_check": "lossless_density_confirmed"},
                    )
                # Thin but the audio is all there: quiet or sparse music. Accepting
                # is the whole reason this confirms instead of trusting the number.
                logger.info(
                    "[Integrity] %s is thin for its runtime (%.0f%% of raw) but the audio "
                    "is complete — accepting (quiet/sparse material)",
                    os.path.basename(file_path), _density * 100,
                )

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
        # The decode is authoritative and ran first. Reaching here means it could
        # NOT run (no ffmpeg), which used to accept anything - the hole that let a
        # 30s clip claiming 215s through on an install without ffmpeg. A zero-length
        # header hides the file's own runtime, so measure the bytes against the
        # EXPECTED one. Unlike the positive-duration density check above, this branch
        # can reject on density alone because there is no runtime measurement left to
        # confirm against.
        if is_lossless_audio(audio):
            _raw = raw_pcm_bitrate(getattr(audio.info, "sample_rate", 0),
                                   getattr(audio.info, "bits_per_sample", 0),
                                   getattr(audio.info, "channels", 0))
            _expected_s = expected_duration_ms / 1000.0 if expected_duration_ms else 0
            if _raw > 0 and _expected_s > 0:
                _d = (size * 8 / _expected_s) / _raw
                checks["lossless_density_vs_expected"] = round(_d, 4)
                if _d < LOSSLESS_MIN_DENSITY:
                    return IntegrityResult(
                        ok=False,
                        reason=f"Lossless file holds only {_d * 100:.0f}% of the data an expected "
                               f"{_expected_s:.0f}s runtime needs "
                               f"({size * 8 / _expected_s / 1000:.0f}kbps) and its header reports "
                               "no length — a preview clip or truncated download",
                        checks={**checks, "mutagen_parse": "zero_length_density_failed"},
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

    # --- Check 4: duration agreement (optional) ---
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
