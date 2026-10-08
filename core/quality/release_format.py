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
from typing import Iterable, NamedTuple, Optional, Set, Tuple

from core.quality.lossless import LOSSLESS_FORMATS
from core.quality.model import AudioQuality
from core.quality.source_map import (
    AUDIO_EXTENSIONS as SOURCE_AUDIO_EXTENSIONS,
    format_from_extension,
)

from utils.logging_config import get_logger

logger = get_logger("quality.release_format")

# Re-export the shared extension set for callers that historically imported it
# here, but classify through source_map. That module is the canonical vocabulary
# used by QualityTarget, file probing, and every other source adapter: notably
# AIFF is the ``wav`` PCM tier and DSF/DFF are the ``dsf`` DSD tier.
AUDIO_EXTENSIONS = frozenset(SOURCE_AUDIO_EXTENSIONS)

# Title markers, longest/most specific first. Order matters: '24bit' implies
# lossless, and a title saying both FLAC and MP3 is a mixed release, which we
# want to notice rather than resolve to whichever matched first.
_TITLE_MARKERS = (
    ('dsd1024', 'dsf'),
    ('dsd512', 'dsf'),
    ('dsd256', 'dsf'),
    ('dsd128', 'dsf'),
    ('dsd64', 'dsf'),
    ('dsdiff', 'dsf'),
    ('dsf', 'dsf'),
    ('dff', 'dsf'),
    ('dsd', 'dsf'),
    ('flac24', 'flac'),
    ('tr24', 'flac'),
    ('flac', 'flac'),
    ('alac', 'alac'),
    ('itunes plus', 'aac'),
    ('aiff', 'wav'),
    ('aifc', 'wav'),
    # No 'wave' marker on purpose. 'wav' already has a word boundary on both
    # sides, so it does not fire inside "Heat Wave"; a 'wave' marker did, and
    # turned every album with that word into a mixed flac/wav release a
    # lossless-only profile then refused. Lidarr's codec regex matches WAV and
    # never WAVE for the same reason. 'aif' is left out on the same grounds.
    ('wav', 'wav'),
    ('wavpack', 'wavpack'),
    ('ape', 'ape'),
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

# A bitrate is only evidence when it is labelled, is a well-known VBR preset,
# or is enclosed as a release tag.  Treating every bare ``192`` as MP3 turns
# catalogue numbers (and similarly-shaped title fragments) into invented
# quality metadata.  Lidarr follows the same important rule at the decision
# boundary: unknown stays unknown instead of being promoted to a lossy codec.
_LOSSY_HINT = re.compile(
    r'(?:\b(?:v0|v2)\b|'
    r'\b(?:96|128|160|192|224|256|320|500)\s*(?:kbps|kb/s|kbit/s)\b|'
    r'[\[(]\s*(?:96|128|160|192|224|256|320|500)\s*[\])])',
    re.I,
)

_BIT_DEPTH = re.compile(r'\b(16|24|32)\s*(?:-|_)?\s*bit\b', re.I)
_DEPTH_RATE_PAIR = re.compile(
    r'\b(16|24|32)\s*[-_/]\s*'
    r'(44(?:\.1)?|48|88(?:\.2)?|96|176(?:\.4)?|192|352(?:\.8)?|384)\b',
    re.I,
)
_SAMPLE_RATE_KHZ = re.compile(
    r'\b(44(?:\.1)?|48|88(?:\.2)?|96|176(?:\.4)?|192|352(?:\.8)?|384)'
    r'\s*k(?:hz)?\b',
    re.I,
)
_SAMPLE_RATE_HZ = re.compile(
    r'\b(44100|48000|88200|96000|176400|192000|352800|384000)\s*hz\b',
    re.I,
)
_LABELLED_BITRATE = re.compile(
    r'\b(\d{2,4})\s*(?:kbps|kb/s|kbit/s)\b', re.I,
)
_LOSSY_FORMATS = frozenset({'mp3', 'aac', 'ogg', 'opus', 'wma'})
_LOSSY_BARE_BITRATE = re.compile(
    r'\b(?:mp3|aac|ogg|opus|wma)\s*[-_/ ]*'
    r'(96|128|160|192|224|256|320|500)\b|'
    r'\b(96|128|160|192|224|256|320|500)\s*[-_/ ]*'
    r'(?:mp3|aac|ogg|opus|wma)\b|'
    r'[\[(]\s*(96|128|160|192|224|256|320|500)\s*[\])]',
    re.I,
)

# Codec markers that carry a resolution of their own. ``FLAC24``/``TR24`` are
# how a 24-bit release is labelled when the title never spells out "24bit",
# which read as an unlabelled FLAC here and lost its hi-res bucket.
_HIRES_CODEC_MARKER = re.compile(r'(?<![a-z0-9])(?:flac24|tr24)(?![a-z0-9])', re.I)

# Bitrate claims that are named rather than numeric. iTunes Plus is Apple's
# 256 kbps AAC; Vorbis/Opus quality presets map onto nominal bitrates the same
# way Lidarr's QualityParser reads them. A preset only speaks for a release
# whose codec is known to use it — a bare ``q8`` next to no codec is as mute as
# a bare ``256``.
_ITUNES_PLUS = re.compile(r'(?<![a-z0-9])itunes\s*plus(?![a-z0-9])', re.I)
_VORBIS_QUALITY = re.compile(r'(?<![a-z0-9])q(5|6|7|8|9|10)(?![a-z0-9])', re.I)
_VORBIS_QUALITY_BITRATE = {
    '5': 160, '6': 192, '7': 224, '8': 256, '9': 320, '10': 500,
}
_VORBIS_FORMATS = frozenset({'ogg', 'opus'})


def formats_in_title(title: str) -> Set[str]:
    """Every format a release TITLE claims. Possibly empty (unknown), possibly
    more than one (a mixed release advertising both)."""
    if not title:
        return set()
    lower = str(title).lower()
    found = {
        fmt
        for marker, fmt in _TITLE_MARKERS
        if re.search(rf'(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])', lower)
    }
    if not found and _LOSSLESS_HINT.search(lower):
        # A lossless claim with no codec named. Treated as FLAC because that
        # is what it means on every music tracker in practice, and because
        # the file list (when we have one) will correct us.
        found.add('flac')
    if not found and _LOSSY_HINT.search(lower):
        found.add('mp3')
    return found


def audio_quality_from_release_title(title: str) -> AudioQuality:
    """Parse the quality Prowlarr actually exposes: the release title.

    Prowlarr's public search resource does not contain normalized music codec,
    bitrate, sample-rate, or bit-depth fields.  Music indexers conventionally
    encode those values in titles (``FLAC 24-96``, ``MP3 320kbps``), so this is
    the source-boundary adapter for torrent and Usenet results.

    Parsing is deliberately conservative.  A bare title is ``unknown`` and a
    mixed ``FLAC + MP3`` title is also ``unknown``: choosing one codec would be
    a claim the indexer did not make.  The post-download probe remains the
    authority; these values exist so search/profile ranking can make the best
    decision possible before a grab.
    """
    raw = str(title or '')
    formats = formats_in_title(raw)
    fmt = next(iter(formats)) if len(formats) == 1 else 'unknown'

    pair = _DEPTH_RATE_PAIR.search(raw)
    depth_match = _BIT_DEPTH.search(raw)
    bit_depth = int(pair.group(1)) if pair else (
        int(depth_match.group(1)) if depth_match else None
    )
    if bit_depth is None and _HIRES_CODEC_MARKER.search(raw):
        bit_depth = 24

    sample_rate = None
    if pair:
        sample_rate = _khz_to_hz(pair.group(2))
    else:
        khz = _SAMPLE_RATE_KHZ.search(raw)
        hz = _SAMPLE_RATE_HZ.search(raw)
        if khz:
            sample_rate = _khz_to_hz(khz.group(1))
        elif hz:
            sample_rate = int(hz.group(1))

    bitrate = None
    labelled = _LABELLED_BITRATE.search(raw)
    preset = _VORBIS_QUALITY.search(raw)
    if labelled:
        bitrate = int(labelled.group(1))
    elif fmt == 'aac' and _ITUNES_PLUS.search(raw):
        bitrate = 256
    elif fmt in _VORBIS_FORMATS and preset:
        bitrate = _VORBIS_QUALITY_BITRATE[preset.group(1)]
    elif fmt in _LOSSY_FORMATS:
        bare = _LOSSY_BARE_BITRATE.search(raw)
        if bare:
            bitrate = int(next(value for value in bare.groups() if value))

    return AudioQuality(
        format=fmt,
        bitrate=bitrate,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
    )


def audio_quality_from_release(
    title: str,
    categories: Optional[Iterable[int]] = None,
    file_names: Optional[Iterable[str]] = None,
) -> AudioQuality:
    """Combine title hints with Prowlarr/Newznab's audio leaf category.

    ``3010`` is Audio/MP3 and can therefore fill an unknown codec. ``3040`` is
    only Audio/Lossless: it must NOT be promoted to FLAC because the payload may
    instead be ALAC, APE, WavPack, WAV, etc. Parent ``3000`` and Other/Foreign
    carry no codec information. A category/title contradiction becomes
    ``unknown`` instead of arbitrarily trusting one side. Resolution and
    bitrate still come only from the title because categories do not encode
    them.

    ``file_names`` is the release's file list, and it OUTRANKS both: an
    extension is what the release contains, a title is what the uploader typed.
    A list carrying more than one audio format is ``unknown`` — the same rule a
    mixed title gets — and a list with no audio in it (art, logs, .nfo) says
    nothing, so the title keeps its answer. Precedence lives here so every
    caller that happens to have a file list does not re-derive it.
    """
    quality = audio_quality_from_release_title(title)
    file_formats = formats_in_files(file_names or ())
    if file_formats:
        quality.format = (
            next(iter(file_formats)) if len(file_formats) == 1 else 'unknown'
        )
        return quality
    title_formats = formats_in_title(title)
    has_mp3_category = False
    has_lossless_category = False
    for raw in categories or ():
        try:
            category_id = int(raw)
        except (TypeError, ValueError):
            continue
        if category_id == 3010:
            has_mp3_category = True
        elif category_id == 3040:
            has_lossless_category = True

    if has_mp3_category and has_lossless_category:
        quality.format = 'unknown'
        return quality

    if has_mp3_category and not title_formats:
        # Fills a silent title only; a title that names a codec outranks a
        # category mapping (see evaluate_release for the same rule).
        quality.format = 'mp3'
    elif has_lossless_category and quality.format in _LOSSY_FORMATS:
        quality.format = 'unknown'
    return quality


# A lossless claim has a floor its own bytes have to clear. FLAC of CD audio
# lands around 700-1000 kbit/s and even the most compressible material stays
# well above this; anything under it is a lossy file wearing a lossless title.
# There is deliberately no lossless CEILING: 24/192 multichannel legitimately
# runs into thousands of kbit/s, so a ceiling could only be wrong.
_LOSSLESS_MIN_IMPLIED_KBPS = 400.0

# For a STATED lossy bitrate the tolerance is generous — the measurement covers
# artwork, logs and cue sheets as well as audio, and the duration comes from a
# provider tracklist that may describe a different edition. Doubling the claim
# and adding a fixed allowance means only a release that is nothing like what
# it says (a discography pack answering an album search) trips it. Lidarr can
# afford a tight per-quality ceiling because it knows the exact release
# duration; here the same rule has to survive a wrong edition.
_LOSSY_CEILING_FACTOR = 2.0
_LOSSY_CEILING_ALLOWANCE_KBPS = 64.0


# Release revision — Lidarr's Revision, parsed from the same words. A repack is
# the corrected copy of a rip that was wrong, so it beats the original of the
# SAME quality; it never beats a better format, which is why this is only ever
# a tiebreaker. REAL is matched case-sensitively because "real" is an ordinary
# word in album titles, and the version pattern refuses the trailing digit of
# MP3 for the same reason.
_PROPER_RE = re.compile(r'\bproper\b', re.I)
_REPACK_RE = re.compile(r'\b(?:repack\d?|rerip\d?)\b', re.I)
_VERSION_RE = re.compile(
    r'(?:\d(?<!\bmp3))[-._ ]?v(\d)[-._ ]|\[v(\d)\]|repack(\d)|rerip(\d)', re.I,
)
_REAL_RE = re.compile(r'\bREAL\b')
# Case sensitivity is the whole signal, so it only means anything in a title
# that HAS a case. Many indexers post fully upper cased, where REAL is just the
# ordinary word, and real outranks version in the revision sort.
_HAS_LOWERCASE_RE = re.compile(r'[a-z]')

# Codecs that write their VBR setting as vN. On one of these, "V0" is a bitrate
# and audio_quality_from_release_title already read it as one, so it says
# nothing about how many times the release was fixed.
_PRESET_CODECS = frozenset({'mp3', 'ogg', 'opus'})


class ReleaseRevision(NamedTuple):
    """How many times this release has been corrected."""

    version: int = 1
    real: int = 0
    is_repack: bool = False

    @property
    def rank(self) -> Tuple[int, int]:
        """Sort key. REAL outranks version, exactly as Lidarr compares them."""
        return (self.real, self.version)


def _vn_is_a_bitrate_preset(matched, lowered: str) -> bool:
    """True when a vN match is a LAME/Vorbis quality preset, not a revision.

    repack2 / rerip2 are always a revision, they name the word. A bare v0 or v2
    is only a revision on a release whose codec does not use presets.
    """
    if matched.group(3) or matched.group(4):
        return False
    return bool(formats_in_title(lowered) & _PRESET_CODECS)


def release_revision(title: str) -> ReleaseRevision:
    """PROPER / REPACK / vN / REAL for one release title."""
    raw = str(title or '')
    lowered = raw.lower()

    matched = _VERSION_RE.search(lowered)
    if matched and _vn_is_a_bitrate_preset(matched, lowered):
        # V0 and V2 are mp3 VBR presets, not release versions. reading them as
        # versions put a V0 rip on 0, under the neutral 1 everything else gets,
        # and a V2 rip on 2, so the worse preset beat the better one. we can't
        # tell a real re-upload from a preset here, so say nothing. PROPER and
        # REPACK still speak for an mp3 release.
        matched = None

    # never below the neutral 1. a release is not worse than an unmarked one
    # for carrying a marker we could not read.
    version = max(1, int(next(g for g in matched.groups() if g))) if matched else 1

    is_repack = bool(_REPACK_RE.search(lowered))
    if _PROPER_RE.search(lowered) or is_repack:
        # A bare PROPER is version 2; one that also states a version increments
        # it, so "REPACK2" is the third copy rather than the second.
        version = version + 1 if matched else 2

    return ReleaseRevision(
        version=version,
        real=(
            len(_REAL_RE.findall(raw))
            if _HAS_LOWERCASE_RE.search(raw) else 0
        ),
        is_repack=is_repack,
    )


# Lidarr's NotSampleSpecification. "Sample" alone is not evidence — plenty of
# tracks are called Sample Text — and a small release alone is not either, it
# may be a single. Both together are, and 20 MB is Lidarr's line.
_SAMPLE_MAX_BYTES = 20 * 1024 * 1024
_SAMPLE_RE = re.compile(r'(?<![a-z0-9])sample(?![a-z0-9])', re.I)


def is_sample_release(title: str, size_bytes) -> bool:
    """True when a release says it is a sample and is small enough to be one."""
    if not _SAMPLE_RE.search(str(title or '')):
        return False
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    return 0 < size < _SAMPLE_MAX_BYTES


def implied_bitrate_kbps(size_bytes, duration_seconds) -> Optional[float]:
    """Average kbit/s a release carries, or None when it cannot be measured.

    This is an upper bound on the audio bitrate: the size includes everything
    shipped alongside the audio, so padding only ever inflates it.
    """
    try:
        size = float(size_bytes or 0)
        duration = float(duration_seconds or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0 or duration <= 0:
        return None
    return size * 8 / 1000 / duration


def size_contradicts_quality(quality, size_bytes, duration_seconds) -> Optional[str]:
    """Why the bytes disagree with the claimed quality, or None if they don't.

    Same mechanism as Lidarr's ``AcceptableSizeSpecification`` — expected
    kbit/s times known duration, compared against the release size — and the
    same fail-open contract: no size or no duration means no opinion, never a
    rejection. What it can prove is asymmetric, so the two directions are not
    the same rule (see the constants above).
    """
    implied = implied_bitrate_kbps(size_bytes, duration_seconds)
    if implied is None or quality is None:
        return None

    fmt = str(getattr(quality, 'format', '') or '').lower()
    if fmt in LOSSLESS_FORMATS:
        if implied < _LOSSLESS_MIN_IMPLIED_KBPS:
            return (
                f"claims lossless {fmt.upper()} but carries only "
                f"{implied:.0f} kbit/s"
            )
        return None

    claimed = getattr(quality, 'bitrate', None)
    if fmt in _LOSSY_FORMATS and claimed:
        ceiling = claimed * _LOSSY_CEILING_FACTOR + _LOSSY_CEILING_ALLOWANCE_KBPS
        if implied > ceiling:
            return (
                f"claims {claimed} kbit/s {fmt.upper()} but carries "
                f"{implied:.0f} kbit/s"
            )
    return None


def _khz_to_hz(value: str) -> int:
    return int(round(float(value) * 1000))


def formats_in_files(file_names: Iterable[str]) -> Set[str]:
    """Every audio format present in a release's FILE LIST.

    Evidence, not a hint. Non-audio entries (art, logs, cue sheets, .nfo) are
    ignored — a release is not lossy because it ships a JPEG.
    """
    formats: Set[str] = set()
    for name in file_names or ():
        ext = os.path.splitext(str(name or ''))[1].lower()
        fmt = format_from_extension(ext)
        if fmt != 'unknown':
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
    categories: Optional[Iterable[int]] = None,
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
        category_values = tuple(categories or ())
        title_formats = formats_in_title(title)
        category_ids = set()
        for raw in category_values:
            try:
                category_ids.add(int(raw))
            except (TypeError, ValueError):
                continue

        has_mp3_category = 3010 in category_ids
        has_lossless_category = 3040 in category_ids
        source = 'title/category' if category_ids else 'title'

        # Exact Audio/MP3 is structured codec evidence. Preserve it through
        # this strict decision boundary instead of recognizing it in search,
        # then throwing it away immediately before grab. A conflicting title
        # becomes a mixed/contradictory set and is therefore never silently
        # trusted by either a strict MP3 or strict lossless profile.
        if has_mp3_category and has_lossless_category:
            found = set()
        elif has_mp3_category:
            # 3010 is Audio/MP3, but plenty of indexers map their whole music
            # category to it, FLAC torrents included, so it is not proof of a
            # codec against a title that names one. The title describes THIS
            # release; the category describes the bucket the indexer put it in.
            # It fills a title that said nothing, and yields to one that did.
            found = title_formats or {'mp3'}
        elif has_lossless_category and title_formats & _LOSSY_FORMATS:
            # Audio/Lossless identifies a family rather than a codec, so it
            # cannot fill a bare title. It can still disprove a lossy title.
            found = set()
        else:
            quality = audio_quality_from_release(title, category_values)
            found = (
                {quality.format}
                if quality.format != 'unknown'
                else title_formats
            )

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
