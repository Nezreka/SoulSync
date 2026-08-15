"""Does this RELEASE satisfy the profile's formats, before we download it?

The import guard (``core/imports/guards.py::check_quality_target``) already
answers this for a file on disk, by probing it. That is ground truth, but it
runs after the bytes have arrived — which is fine for Soulseek, where a
candidate IS a file with a known extension, and useless for torrents, where a
candidate is a release title and we only learn what is inside by fetching it.

#1149 (Zombiehamser): a lossless profile with fallback disabled still enqueued
MP3 and mixed releases over the torrent path, because torrent candidate
selection never consulted a quality profile at all. The releases downloaded,
occupied queue slots, and were rejected at import — the user cleaned up by
hand.

NO NEW SETTING. A profile whose ranked targets are all FLAC and whose
``fallback_enabled`` is off ALREADY means "FLAC only" — that is precisely how
the import guard reads it. The reported bug is that this side never asked. The
schema makes the same argument in reverse about a redundant master toggle
(``core/quality/schema.py``): a second way to say the same thing is a second
thing to keep in sync, and they drift.

THE ASYMMETRY THAT MATTERS: a title is a hint, a file list is evidence. So
``unknown`` is a distinct verdict from ``lossy`` and a strict profile rejects
it, rather than the old behaviour of quietly calling an unreadable title
'mp3' and ranking it anyway.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional, Set, Tuple

from utils.logging_config import get_logger

logger = get_logger("quality.release_format")

# Extension -> canonical format name. Matches the vocabulary QualityTarget
# uses ('flac', 'mp3', 'aac', ...) so a profile's targets can be compared
# directly against what a release contains.
_EXT_FORMAT = {
    '.flac': 'flac',
    '.wav': 'wav',
    '.aiff': 'aiff',
    '.aif': 'aiff',
    '.alac': 'alac',
    '.ape': 'ape',
    '.wv': 'wavpack',
    '.mp3': 'mp3',
    '.m4a': 'aac',
    '.mp4': 'aac',
    '.aac': 'aac',
    '.ogg': 'ogg',
    '.oga': 'ogg',
    '.opus': 'opus',
    '.wma': 'wma',
}

LOSSLESS_FORMATS = frozenset({'flac', 'wav', 'aiff', 'alac', 'ape', 'wavpack'})

AUDIO_EXTENSIONS = frozenset(_EXT_FORMAT)

# Title markers, longest/most specific first. Order matters: '24bit' implies
# lossless, and a title saying both FLAC and MP3 is a mixed release, which we
# want to notice rather than resolve to whichever matched first.
_TITLE_MARKERS = (
    ('flac', 'flac'),
    ('alac', 'alac'),
    ('wavpack', 'wavpack'),
    ('ape', 'ape'),
    ('aiff', 'aiff'),
    ('opus', 'opus'),
    ('m4a', 'aac'),
    ('aac', 'aac'),
    ('ogg', 'ogg'),
    ('vorbis', 'ogg'),
    ('wma', 'wma'),
    ('mp3', 'mp3'),
)

# 'Lossless' / '24bit' / 'Hi-Res' assert losslessness without naming a codec.
# In practice on music trackers that means FLAC, but we record it as a
# lossless CLAIM rather than as FLAC specifically.
_LOSSLESS_HINT = re.compile(
    r'\b(?:lossless|24[\s\-_]?bit|hi[\s\-_]?res|hires|web[\s\-_]?flac)\b', re.I)

# Bare bitrates: "V0", "320", "320kbps", "V2" all mean lossy MP3.
_LOSSY_HINT = re.compile(r'\b(?:v0|v2|320|256|192|128)\s*(?:kbps|k)?\b', re.I)


def formats_in_title(title: str) -> Set[str]:
    """Every format a release TITLE claims. Possibly empty (unknown), possibly
    more than one (a mixed release advertising both)."""
    if not title:
        return set()
    lower = str(title).lower()
    found = {fmt for marker, fmt in _TITLE_MARKERS if marker in lower}
    if not found and _LOSSLESS_HINT.search(lower):
        # A lossless claim with no codec named. Treated as FLAC because that
        # is what it means on every music tracker in practice, and because
        # the file list (when we have one) will correct us.
        found.add('flac')
    if not found and _LOSSY_HINT.search(lower):
        found.add('mp3')
    return found


def formats_in_files(file_names: Iterable[str]) -> Set[str]:
    """Every audio format present in a release's FILE LIST.

    Evidence, not a hint. Non-audio entries (art, logs, cue sheets, .nfo) are
    ignored — a release is not lossy because it ships a JPEG.
    """
    formats: Set[str] = set()
    for name in file_names or ():
        ext = os.path.splitext(str(name or ''))[1].lower()
        fmt = _EXT_FORMAT.get(ext)
        if fmt:
            formats.add(fmt)
    return formats


def allowed_formats_from_profile(profile: dict) -> Optional[Set[str]]:
    """The formats a profile will accept, or None for "anything".

    None is returned when the profile has no ranked targets, or when
    ``fallback_enabled`` is on — both of which already mean "accept anything"
    everywhere else in the pipeline (see the import guard). Keeping that
    equivalence is the whole reason this needs no new setting.
    """
    if not isinstance(profile, dict):
        return None
    try:
        from core.quality.selection import targets_from_profile
        targets, fallback_enabled = targets_from_profile(profile)
    except Exception as e:                        # pragma: no cover - defensive
        logger.debug("allowed_formats_from_profile: %s", e)
        return None
    if fallback_enabled or not targets:
        return None
    formats = {str(t.format).lower() for t in targets if getattr(t, 'format', None)}
    return formats or None


def evaluate_release(
    allowed: Optional[Set[str]],
    title: str = '',
    file_names: Optional[Iterable[str]] = None,
    *,
    allow_mixed: bool = False,
) -> Tuple[bool, str]:
    """``(accepted, reason)`` for one release against a profile's formats.

    ``allowed`` of None accepts everything — the profile is not strict, so
    this must not become a second, stricter filter that users never asked for.

    ``file_names`` is preferred over ``title`` whenever it is available and
    contains audio, because it is evidence rather than a claim.

    An UNDETERMINED release is rejected under a strict profile. That is the
    behaviour #1149 asked for and the reverse of what the code did before:
    a title it could not read was labelled 'mp3' and left in the running.
    """
    if not allowed:
        return True, 'profile accepts any format'

    file_formats = formats_in_files(file_names or ())
    if file_formats:
        source = 'file list'
        found = file_formats
    else:
        source = 'title'
        found = formats_in_title(title)

    if not found:
        return False, (
            f"format undetermined from {source}; a strict profile rejects "
            f"rather than guess"
        )

    wanted = found & allowed
    if not wanted:
        return False, (
            f"{source} says {_join(found)}; profile allows {_join(allowed)}"
        )

    disallowed = found - allowed
    if disallowed and not allow_mixed:
        # A mixed release satisfies the profile for part of its content and
        # violates it for the rest. Importing it means hand-cleaning the
        # remainder, which is the cost #1149 is about.
        return False, (
            f"mixed release: {source} says {_join(found)}, "
            f"and {_join(disallowed)} is not allowed"
        )

    return True, f"{source} says {_join(wanted)}"


def _join(values: Iterable[str]) -> str:
    return '/'.join(sorted(values)) or 'nothing'
