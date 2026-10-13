"""Decide whether a downloaded audiobook is actually the whole book.

Music can trust a download client's "complete": an album's completeness is a
track count you can check in a second, and a wrong answer costs one track.
An audiobook cannot. A torrent finishes when the files it was ASKED for finish,
which is not the same as the book being whole — a release can be missing its
last three chapters, or be a sample pack, and every file present will play
perfectly. Import it and the failure surfaces hours later, at the point the
listener runs out of book.

The gift that makes this checkable: Audible publishes ``runtime_length_min`` for
every title, so we know how long the book IS. Summing the audio on disk and
comparing is a real measurement, not a heuristic on filenames or file counts.

Books that come up short are STAGED, not failed. The usual reason is a torrent
still fetching, or a release that will be completed by its uploader; the right
answer is to keep the files and look again later, which is what the monitor does
until the staging deadline runs out.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("audiobook_completeness")

# How much of the expected runtime has to be on disk. Not 100%: Audible's
# runtime includes the publisher's own intro/outro and rounds to the minute, and
# a rip that drops a few seconds of silence at a chapter boundary is still the
# whole book. Below this and chapters are genuinely missing.
DEFAULT_TOLERANCE = 0.92

# A release that is LONGER than the book is not automatically wrong — box sets,
# duplicated intros and bonus interviews all overshoot — but far over means the
# folder holds something else entirely, like a whole series.
MAX_OVERSHOOT = 1.60

# How long a short book is kept staged before giving up. Torrents finish late
# and uploaders repair releases, so patience is correct; forever is not.
DEFAULT_STAGING_DAYS = 7

_AUDIO_EXTENSIONS = frozenset({
    ".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4",
})

# Bounds on bytes-per-minute for anything that could be a speech recording, used
# only when NOTHING in the folder can be read and there is no local rate to
# derive. Wide on purpose: this is a sanity check, not a measurement, and it is
# the difference between "cannot verify" and "obviously wrong".
_MIN_BYTES_PER_MINUTE = 60 * 1024
_MAX_BYTES_PER_MINUTE = 40 * 1024 * 1024


def measure_duration_seconds(path: Path) -> Optional[float]:
    """Playing time of one audio file, or None when it cannot be read.

    None is not zero. A file whose header mutagen cannot parse must not be
    counted as silence, or one unreadable chapter would make a complete book
    look short.
    """
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=False)
        length = getattr(getattr(audio, "info", None), "length", None)
        if length is None:
            return None
        length = float(length)
        return length if length > 0 else None
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the duration of %s: %s", path, exc)
        return None


def has_files_but_no_audio(path: Any) -> bool:
    """the download is on disk and none of it is audio: an ebook, a pdf, a
    cover scan. waiting cannot fix that. a path that is missing or empty is
    NOT this, it may still be arriving."""
    root = Path(str(path or ""))
    if not str(path or "") or not root.exists():
        return False
    if root.is_file():
        return root.suffix.lower() not in _AUDIO_EXTENSIONS
    found_any = False
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in _AUDIO_EXTENSIONS:
            return False
        found_any = True
    return found_any


def collect_audio(folder: Path) -> List[Path]:
    """Every audio file under a download, recursively."""
    root = Path(folder)
    if root.is_file():
        return [root] if root.suffix.lower() in _AUDIO_EXTENSIONS else []
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
    )


# Tag names that can name the narrator, by container. TCOM and TXXX:NARRATOR
# are ID3 (plus likely case variants of the TXXX description — ID3 key
# lookups are case-sensitive), \xa9wrt is MP4, the NARRATOR/COMPOSER keys are
# what Vorbis comments and other rippers use, and the ----:com.apple.iTunes
# freeform atoms are where m4b-tool, tone and Audiobookshelf write it in
# MP4. TPE1 is the artist slot: it SHOULD be the author, but a large share
# of rippers put the narrator there instead, so it is read as a weak hint
# only — safe because tags can only ever confirm the edition's narration,
# never contradict it (see narration_confidence). These are the same names
# SoulSync writes itself when it tags a finished book
# (core/audiobook_post_processor.py).
_NARRATOR_TAG_NAMES = (
    "TCOM", "TXXX:NARRATOR", "TXXX:narrator", "TXXX:Narrator", "\xa9wrt",
    "----:com.apple.iTunes:NARRATOR", "----:com.apple.iTunes:narrator",
    "NARRATOR", "narrator", "COMPOSER", "composer", "TPE1",
)

# How many files get their tags read when identifying the narration. The tags
# are almost always uniform across a release; sampling keeps a 200-chapter
# book from paying 200 tag parses for one answer.
_TAG_SAMPLE_LIMIT = 5

# The floor applied when the narration cannot be identified. A complete
# unabridged reading of the same text varies only by speaking rate — roughly
# 130-180 wpm, so the slowest plausible complete narration is still ~0.7x
# the fastest — while sample packs and grossly truncated downloads sit far
# below. 0.6 lets every plausible alternate narration through and still
# catches the egregious cases; it is a plausibility check, not a measurement.
UNKNOWN_NARRATION_TOLERANCE = 0.6


def _extract_tag_strings(value: Any) -> List[str]:
    """Every narrator-ish string a tag value carries.

    ID3 frames expose ``.text`` (a list); MP4/Vorbis tags are plain dicts of
    lists. Taking all of them matters for co-narrated books: first-only
    would drop "Kate Reading" from ["Michael Kramer", "Kate Reading"] and
    miss the edition's narrator. ID3v2.4 multi-value frames stringify with
    embedded null bytes, so each item is split on ``\\x00`` too — otherwise
    the raw bytes leak into the name and into user-facing reasons.
    """
    if value is None:
        return []
    if hasattr(value, "text"):
        value = value.text
    items = value if isinstance(value, (list, tuple)) else [value]
    results: List[str] = []
    for item in items:
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="replace")
        for part in str(item or "").split("\x00"):
            cleaned = part.strip()
            if cleaned and cleaned not in results:
                results.append(cleaned)
    return results


def narrator_from_file_tags(files: Sequence[Path]) -> List[str]:
    """Distinct narrator names found in the embedded tags of the given files.

    Reads at most ``_TAG_SAMPLE_LIMIT`` files. Finding nothing is a normal
    outcome, not an error: most rips tag nothing at all.
    """
    found: List[str] = []
    try:
        from mutagen import File as MutagenFile
    except Exception:                                       # noqa: BLE001
        return found
    for path in list(files)[:_TAG_SAMPLE_LIMIT]:
        try:
            audio = MutagenFile(str(path), easy=False)
            tags = getattr(audio, "tags", None) or {}
            get = getattr(tags, "get", None)
            for name in _NARRATOR_TAG_NAMES:
                try:
                    raw = get(name) if get else None
                except Exception:                           # noqa: BLE001
                    raw = None
                for text in _extract_tag_strings(raw):
                    if text not in found:
                        found.append(text)
        except Exception:                                   # noqa: BLE001
            continue
    return found


def assess(
    folder: str,
    expected_minutes: Optional[int],
    tolerance: float = DEFAULT_TOLERANCE,
    abridged: bool = False,
) -> Dict[str, Any]:
    """Is what is on disk the whole book?

    Returns ``{complete, measured_minutes, expected_minutes, ratio, files,
    unreadable, reason, measured_by}``.

    Deliberately measures PLAYING TIME, never file count. There is no standard
    for how an audiobook is cut up: the same title ships as one m4b, as 20
    chapters, or as 50, and every one of those is complete. Counting files
    could only ever compare a release against another release. Runtime is the
    one thing that describes the BOOK.

    With no expected runtime there is nothing to compare against, so the only
    check left is "is there any audio at all" — a book we know nothing about is
    let through rather than held forever on a comparison that cannot be made.

    ``abridged`` is that same case. Audible publishes the runtime of the
    edition being viewed, which is the unabridged one; an abridged release is
    routinely half of it and would look like a book missing half its chapters
    forever. We do not know how long the abridgement is meant to be, so we do
    not pretend to. A dramatised adaptation (GraphicAudio and the like) is the
    same problem again and rides the same flag: a full-cast re-recording with
    music and effects, sold in parts, whose length has no relation to the
    reading Audible sells.
    """
    files = collect_audio(Path(folder))
    result: Dict[str, Any] = {
        "complete": False,
        "measured_minutes": 0.0,
        "expected_minutes": int(expected_minutes or 0),
        "ratio": 0.0,
        "files": len(files),
        "unreadable": 0,
        "reason": "",
        "measured_by": "duration",
    }

    if not files:
        result["reason"] = "No audio files in the download"
        return result

    total_seconds = 0.0
    readable_bytes = 0
    unreadable: List[Path] = []
    for path in files:
        seconds = measure_duration_seconds(path)
        if seconds is None:
            unreadable.append(path)
        else:
            total_seconds += seconds
            readable_bytes += _safe_size(path)

    result["unreadable"] = len(unreadable)

    if unreadable and total_seconds and readable_bytes:
        # Some readable, some not. The unreadable ones are almost always the
        # same encode as their neighbours, so their length is estimated at the
        # rate measured FROM THIS FOLDER rather than from a constant guess — a
        # 64kbps chapter and a 128kbps one differ by a factor of two, and a
        # hardcoded rate would be wrong for one of them.
        bytes_per_second = readable_bytes / total_seconds
        missing_bytes = sum(_safe_size(p) for p in unreadable)
        if bytes_per_second > 0:
            total_seconds += missing_bytes / bytes_per_second
        result["measured_by"] = "duration+size"
    elif unreadable and not total_seconds:
        # Nothing could be read at all. There is no honest way to measure this,
        # so the only question left is whether the bytes on disk could plausibly
        # be a book of this length. Held only when they obviously could not.
        total_bytes = sum(_safe_size(p) for p in files)
        expected_for_check = int(expected_minutes or 0)
        result["measured_by"] = "size"
        if expected_for_check > 0:
            per_minute = total_bytes / expected_for_check
            if per_minute < _MIN_BYTES_PER_MINUTE:
                result["reason"] = (
                    f"Unreadable audio, and {total_bytes // (1024 * 1024)}MB is far too "
                    f"little for a {expected_for_check}-minute book"
                )
                return result
            if per_minute > _MAX_BYTES_PER_MINUTE:
                result["reason"] = (
                    f"Unreadable audio, and {total_bytes // (1024 * 1024)}MB is far too "
                    f"much for a {expected_for_check}-minute book"
                )
                return result
        result["complete"] = True
        result["reason"] = "Could not read playing time; accepted on file size"
        return result
    measured_minutes = total_seconds / 60.0
    result["measured_minutes"] = round(measured_minutes, 1)

    expected = int(expected_minutes or 0)
    if expected <= 0:
        result["complete"] = True
        result["reason"] = "No published runtime to compare against"
        return result

    if abridged:
        # An abridgement has its own runtime that nobody publishes. Comparing
        # it against the unabridged figure would stage a perfectly good
        # download for a week and then fail it to the wishlist, which would
        # re-grab the same release and do it again.
        result["complete"] = True
        result["measured_by"] = "abridged"
        result["reason"] = (
            f"{int(measured_minutes)} minutes present; abridged releases have no "
            f"published runtime to check against"
        )
        return result

    ratio = measured_minutes / expected
    result["ratio"] = round(ratio, 3)

    if ratio < max(0.0, float(tolerance)):
        short_by = max(0, int(expected - measured_minutes))
        result["reason"] = (
            f"Only {int(measured_minutes)} of {expected} minutes present "
            f"— about {short_by} minutes short"
        )
        return result

    if ratio > MAX_OVERSHOOT:
        result["reason"] = (
            f"{int(measured_minutes)} minutes for a {expected}-minute book "
            f"— this folder holds more than one title"
        )
        return result

    result["complete"] = True
    result["reason"] = f"{int(measured_minutes)} of {expected} minutes present"
    return result


def narration_confidence(
    source_path: str,
    release_title: Optional[str],
    book: Dict[str, Any],
) -> Dict[str, Any]:
    """How sure are we that the download is the catalogue edition's narration?

    Returns ``{"confidence", "detected_narrator", "reason"}`` where confidence
    is "match", "different" or "unknown".

    The edition's runtime describes ONE reading of the book. Measuring a
    download against it is only honest when the download is that reading: a
    different complete narration has its own runtime, and an unidentified one
    has no runtime we can check against. "unknown" is the common case and is
    NOT a failure — most releases never name a narrator — it just means the
    edition's runtime cannot be used as the gate.

    The two signals are asymmetric on purpose. The release title is advertised
    at grab time and already filtered by the search's narrator matching, so it
    may declare "different". File tags are ripper-written and notoriously
    sloppy — copy-pasted across releases, the author sitting in the narrator
    slot — so they may only CONFIRM the edition's narration ("match") and
    never contradict it. Letting a mis-tagged file declare "different" would
    let a truncated download of the edition's own reading sail through, which
    is worse than holding it.

    The asymmetry is not a claim that "match" from tags is always trustworthy:
    a different reading whose tags were copy-pasted from the edition's own rip
    still reads as "match" and gets the hard gate — the original false hold
    persists for that case. That is no worse than the old code (which held it
    too); the asymmetry only removes the new, worse failure mode.
    """
    from core.audiobook_release_search import (
        narrator_in_release,
        narrator_verdict,
        same_person,
    )

    wanted = [str(n) for n in (book.get("narrator_names") or []) if str(n).strip()]
    result: Dict[str, Any] = {
        "confidence": "unknown",
        "detected_narrator": "",
        "reason": "no narrator could be identified for the download",
    }
    if not wanted:
        result["reason"] = "the catalogue edition names no narrator"
        return result

    # The release title first, then the folder it downloaded as: the title
    # does not always say what the download turns out to be. Checked
    # separately — concatenating them would let a folder name bleed into a
    # narrator name captured from the title.
    for text in (release_title or "", Path(source_path).name):
        verdict = narrator_verdict(text, wanted)
        if verdict == "match":
            result["confidence"] = "match"
            result["reason"] = "the download names the edition's narrator"
            return result
        if verdict == "mismatch":
            named = narrator_in_release(text)
            result["confidence"] = "different"
            result["detected_narrator"] = named
            result["reason"] = (
                f"the download names {named}, not the edition's narrator"
            )
            return result

    tagged = narrator_from_file_tags(collect_audio(Path(source_path)))
    for name in tagged:
        if any(same_person(name, one) for one in wanted):
            result["confidence"] = "match"
            result["reason"] = f"the files are tagged as narrated by {name}"
            return result
    # Anything else the tags say is "unknown", never "different" — see the
    # docstring. A tag naming nobody we recognise (a cast sentinel like
    # "Various", an author in the narrator slot, a sloppy copy-paste) is not
    # evidence of a different reading.
    return result


def _safe_size(path: Path) -> int:
    try:
        return int(os.path.getsize(path))
    except OSError:
        return 0


def staging_expired(started_at: float, days: Optional[float] = None) -> bool:
    """Whether a short book has been waiting long enough to give up on.

    Torrents finish late and uploaders repair releases, so waiting is right;
    waiting forever means a broken release occupies a wishlist row for good.
    """
    limit = DEFAULT_STAGING_DAYS if days is None else float(days)
    if limit <= 0:
        return False
    return (time.time() - float(started_at or 0)) > (limit * 86400)


def tolerance_from_settings() -> float:
    """How complete is complete enough, from settings."""
    try:
        from core.settings import config_manager

        value = float(config_manager.get("audiobooks.completeness_tolerance",
                                         DEFAULT_TOLERANCE))
    except Exception:                                       # noqa: BLE001
        return DEFAULT_TOLERANCE
    # A tolerance outside this range is a typo, not an intention: 0 would import
    # anything and >1 could never be satisfied.
    return value if 0.1 <= value <= 1.0 else DEFAULT_TOLERANCE


def staging_days_from_settings() -> float:
    try:
        from core.settings import config_manager

        return max(0.0, float(config_manager.get("audiobooks.staging_days",
                                                 DEFAULT_STAGING_DAYS)))
    except Exception:                                       # noqa: BLE001
        return float(DEFAULT_STAGING_DAYS)
