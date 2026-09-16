"""Find downloadable releases for a known audiobook.

The catalog client answers "what is this book"; this answers "where can I get
it". Those are separate concerns on purpose — the catalog is a metadata service
and must never be paced against indexers, while everything here IS an indexer
call and rides the shared budget.

Sources
-------
Prowlarr, category 3030. ``core/prowlarr_client.py`` already defines that as
MUSIC_CATEGORY_AUDIOBOOK and deliberately keeps it OUT of music searches, so
asking for it here costs the music side nothing and reuses the same client, the
same indexer settings and the same process-wide throttle. Torrent and usenet
both come back from one search; the protocol is on each result.

Soulseek is the third source and lives in ``core/audiobook_soulseek.py``,
because a peer's shared FOLDER is a different shape from a torrent and
pretending otherwise in here would have made both harder to read.
``search_all_sources`` below is what callers actually ask: it runs whichever
sources the chain names, then ranks everything together on one scale so a peer
with the right narrator can beat a torrent with the wrong one.

Rate limiting
-------------
Every search here goes through ``core.prowlarr_throttle`` via the shared
ProwlarrClient, which is the SAME budget the music and video sides spend. That
is deliberate: it is one Prowlarr in front of one set of indexers, and an
indexer cannot tell which half of the app asked. Audiobook searches must not be
able to out-shout a music wishlist drain.

Ranking
-------
An audiobook release is judged mostly on whether it is the right book at all.
Bitrate barely matters for speech — 64kbps mono is a normal, good audiobook —
so format and completeness carry far more weight than they would for music, and
the heavy lifting is done by relevance and a size sanity check.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("audiobook_release_search")

# Newznab audiobook category, mirrored from core.prowlarr_client so this module
# reads standalone. Asserted equal to the client's constant in the tests.
AUDIOBOOK_CATEGORY = 3030

# Format preference. m4b is the audiobook-native container: one file, real
# chapter marks, resume position. mp3 folders work everywhere but carry chapters
# only as filenames.
_FORMAT_SCORES: Dict[str, float] = {
    "m4b": 24.0,
    "m4a": 14.0,
    "mp3": 10.0,
    "flac": 4.0,     # real, but enormous for speech and rarely worth it
    "opus": 6.0,
    "ogg": 5.0,
}

def _quality_format_scores() -> Dict[str, float]:
    """Format scores from the quality profile, falling back to the defaults.

    Wrapped so a profile that cannot be read never stops a search: taste is
    not allowed to break ranking.
    """
    try:
        from core.audiobook_quality import format_scores
        return format_scores()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the quality profile, using defaults: %s", exc)
        return dict(_FORMAT_SCORES)


_FORMAT_PATTERNS = (
    ("m4b", re.compile(r"(?i)\bm4b\b")),
    ("m4a", re.compile(r"(?i)\bm4a\b")),
    ("flac", re.compile(r"(?i)\bflac\b")),
    ("opus", re.compile(r"(?i)\bopus\b")),
    ("ogg", re.compile(r"(?i)\bogg\b|\bvorbis\b")),
    ("mp3", re.compile(r"(?i)\bmp3\b|\bcbr\b|\bvbr\b")),
)

_BITRATE_RE = re.compile(r"(?i)\b(\d{2,3})\s?k(?:bps|b)?\b")

# "Narrated by Michael Kramer", "Read by Stephen Fry", "Narrator: Ray Porter".
# Deliberately narrow: it only fires when the release SAYS whose reading it is,
# because that is the only time we can be sure it is someone else's.
_NARRATOR_RE = re.compile(
    r"(?i)\b(?:narrated\s+by|read\s+by|narrator)\s*[:\-]?\s*"
    r"([A-Za-z][\w.'\-]*(?:\s+[A-Za-z][\w.'\-]*){0,3})"
)
_ABRIDGED_RE = re.compile(r"(?i)\babridged\b")
_UNABRIDGED_RE = re.compile(r"(?i)\bunabridged\b")

# Speech encodes small. A 64kbps mp3 runs about 28MB an hour, an m4b a little
# more, and a generous FLAC rip several times that. These bounds are wide enough
# to admit anything legitimate and narrow enough to throw out a 3MB "sample" or
# a 40GB bundle that happens to mention the title.
_MIN_BYTES_PER_MINUTE = 60 * 1024          # ~3.5 MB/hour, below any real encode
_MAX_BYTES_PER_MINUTE = 40 * 1024 * 1024   # ~2.4 GB/hour, above any sane one
_ABSOLUTE_MIN_BYTES = 2 * 1024 * 1024      # 2MB — nothing real is smaller

_NOISE_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for",
    "unabridged", "abridged", "audiobook", "audio", "book", "novel",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_text(text: Optional[str]) -> str:
    """Lowercase, strip punctuation and collapse whitespace.

    Release titles arrive as ``Project.Hail.Mary.2021.Andy.Weir.M4B-GRP``; the
    catalogue calls it ``Project Hail Mary``. Both reduce to the same words.
    """
    if not text:
        return ""
    lowered = str(text).lower()
    lowered = re.sub(r"[._\-\[\]\(\)\{\}/\\|:;,'\"!?*+]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def significant_tokens(text: Optional[str]) -> List[str]:
    """Content words, in order, with the filler dropped.

    "The" and "audiobook" appear in almost every release title, so counting them
    as matches makes an unrelated release look like a hit.
    """
    return [t for t in _TOKEN_RE.findall(normalize_text(text)) if t not in _NOISE_WORDS]


def detect_format(release_title: Optional[str]) -> str:
    """Audio format named in a release title, or "" when it does not say.

    Checked most-specific first: a title reading "M4B (from MP3 source)" is an
    m4b, and the mp3 pattern would otherwise win on a plain substring search.
    """
    text = str(release_title or "")
    for name, pattern in _FORMAT_PATTERNS:
        if pattern.search(text):
            return name
    return ""


def detect_bitrate(release_title: Optional[str]) -> Optional[int]:
    """Bitrate named in a release title, in kbps, or None.

    Ignores anything outside 16-512: a "1080k" in a filename is not a bitrate,
    and neither is a year.
    """
    match = _BITRATE_RE.search(str(release_title or ""))
    if not match:
        return None
    try:
        value = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return value if 16 <= value <= 512 else None


def is_abridged(release_title: Optional[str]) -> bool:
    """True when the release says abridged and does not say unabridged.

    Order matters: "Unabridged" contains "abridged", so a naive substring check
    marks every full recording as cut down.
    """
    text = str(release_title or "")
    if _UNABRIDGED_RE.search(text):
        return False
    return bool(_ABRIDGED_RE.search(text))


def narrator_in_release(release_title: Optional[str]) -> str:
    """The narrator a release NAMES, or "" when it does not say.

    Most audiobook releases never name the narrator, and "" means exactly that —
    unknown, not absent. That distinction is the whole design: a release that
    says nothing is allowed through, and only one that names somebody else is
    ever rejected.
    """
    match = _NARRATOR_RE.search(str(release_title or ""))
    if not match:
        return ""
    # Trim trailing format/scene words the pattern can swallow after a name.
    name = re.sub(r"(?i)\s+(m4b|mp3|m4a|flac|unabridged|abridged|audiobook)\b.*$",
                  "", match.group(1)).strip()
    return name.strip(" -_.")


def same_person(left: Optional[str], right: Optional[str]) -> bool:
    """Whether two credit strings name the same person.

    Token containment rather than equality: releases abbreviate ("M. Kramer"),
    reorder, and add middle names, and treating those as different people would
    reject the very release the listener asked for.
    """
    left_tokens = significant_tokens(left)
    right_tokens = significant_tokens(right)
    if not left_tokens or not right_tokens:
        return False

    smaller, larger = sorted((left_tokens, right_tokens), key=len)
    pool = set(larger)
    for token in smaller:
        if token in pool:
            continue
        # A single letter is an initial, not a name: "M. Kramer" is the same
        # person as "Michael Kramer", and rejecting it would throw away the very
        # release the listener asked for.
        if len(token) == 1 and any(other.startswith(token) for other in larger):
            continue
        return False
    return True


# Credit strings that name a production rather than a person. Matching on them
# is meaningless, and treating one as "a different narrator" would reject every
# release of a full-cast recording.
_CAST_SENTINELS = frozenset({"full cast", "a full cast", "cast", "various", "multiple"})


def narrator_verdict(
    release_title: Optional[str],
    wanted_narrators: Any,
) -> str:
    """"match", "mismatch" or "unknown" for a release against a book's narrators.

    Takes ALL the book's narrators, not just the first. A full-cast recording
    credits a dozen people, and comparing only against the first meant a release
    naming any other cast member read as a different narrator and was dropped —
    rejecting exactly the release the listener wanted.

    "unknown" is the common case and is NOT a failure: most releases never name
    a narrator, and only one that names somebody demonstrably else is rejected.
    """
    if isinstance(wanted_narrators, str):
        wanted = [wanted_narrators]
    else:
        wanted = [str(n) for n in (wanted_narrators or [])]
    wanted = [n for n in wanted if n.strip()
              and n.strip().casefold() not in _CAST_SENTINELS]
    if not wanted:
        return "unknown"

    named = narrator_in_release(release_title)
    if not named:
        return "unknown"
    if named.strip().casefold() in _CAST_SENTINELS:
        # The release says "full cast" — that agrees with a cast recording and
        # tells us nothing about a single-narrator one.
        return "unknown"
    return "match" if any(same_person(named, one) for one in wanted) else "mismatch"


# Language names as they appear in release titles, mapped to the value the
# catalogue reports. Only the ones that actually turn up; an unlisted language
# simply reads as "unknown" and is allowed through.
_LANGUAGE_HINTS = {
    "spanish": ("spanish", "espanol", "español", "castellano"),
    "german": ("german", "deutsch"),
    "french": ("french", "francais", "français"),
    "italian": ("italian", "italiano"),
    "portuguese": ("portuguese", "portugues", "português"),
    "dutch": ("dutch", "nederlands"),
    "japanese": ("japanese",),
    "russian": ("russian",),
    "polish": ("polish", "polski"),
}


def language_verdict(release_title: Optional[str], wanted_language: Optional[str]) -> str:
    """"mismatch" when a release announces a language the book is not in.

    Audible carries the same title in many languages, and a title search drags
    the translations in alongside the original — "Proyecto Hail Mary" scores
    well against "Project Hail Mary" because the author and half the words
    match. A release that names its language is the one honest signal available.

    Silence is "unknown" and always allowed: the overwhelming majority of
    releases never state a language, and requiring one would find nothing.
    """
    wanted = str(wanted_language or "").strip().lower()
    if not wanted:
        return "unknown"

    text = normalize_text(release_title)
    for language, hints in _LANGUAGE_HINTS.items():
        if any(f" {hint} " in f" {text} " for hint in hints):
            return "match" if language == wanted else "mismatch"
    return "unknown"


def abridgement_verdict(release_title: Optional[str], wanted_format: Optional[str]) -> str:
    """"match", "mismatch" or "unknown" for a release's abridgement.

    Audible sells abridged and unabridged as separate ASINs with different
    runtimes, so wanting a book already means wanting one of them. Penalising
    "abridged" unconditionally punished the very release someone asked for when
    the abridged edition was the one they picked.

    A release that says nothing is unknown — most do not say — and unabridged is
    assumed only when the catalogue told us the edition is unabridged.
    """
    wanted = str(wanted_format or "").strip().lower()
    if wanted not in ("abridged", "unabridged"):
        return "unknown"

    text = str(release_title or "")
    if _UNABRIDGED_RE.search(text):
        found = "unabridged"
    elif _ABRIDGED_RE.search(text):
        found = "abridged"
    else:
        return "unknown"
    return "match" if found == wanted else "mismatch"


def title_relevance(release_title: Optional[str], book_title: Optional[str],
                    authors: Optional[Sequence[str]] = None) -> float:
    """How much of the book's identity appears in a release title, 0.0-1.0.

    Scored on the BOOK's words being present in the release, not the other way
    round: a release title carries scene tags, group names, years and format
    markers that the book title will never contain, and penalising those would
    rank a clean upload below a bare one.

    The author counts for a quarter of the score. It is what separates the real
    book from a same-named one, but plenty of legitimate uploads omit it.
    """
    wanted = significant_tokens(book_title)
    if not wanted:
        return 0.0
    haystack = set(significant_tokens(release_title))
    if not haystack:
        return 0.0

    hits = sum(1 for token in wanted if token in haystack)
    score = hits / len(wanted)

    author_tokens = [t for name in (authors or []) for t in significant_tokens(name)]
    if author_tokens:
        author_hits = sum(1 for token in author_tokens if token in haystack)
        author_score = author_hits / len(author_tokens)
        score = (score * 0.75) + (author_score * 0.25)
    return round(min(1.0, score), 4)


# What an audiobook's size actually MEANS, given its runtime. Audible's own
# files are the reference point: their standard format is 32 kbps mono and
# their enhanced one 64 kbps stereo, both AAC. Speech carries almost no high
# frequency content and no stereo image worth preserving, so anything far above
# that is not buying audible quality — it is a lossless rip, several formats in
# one pack, or something that is not only this book.
_QUALITY_BANDS = (
    (24, "thin", "Below what Audible ships. Likely to sound muffled."),
    (48, "standard", "About what Audible's own standard files use."),
    (96, "good", "Better than Audible ships. Comfortably transparent for speech."),
    (192, "generous", "More than speech needs, but nothing wrong with it."),
    (10 ** 9, "oversized",
     "Far more than speech needs — usually lossless, several formats in one "
     "pack, or extras that are not the book."),
)


def implied_bitrate_kbps(
    size_bytes: Optional[int], runtime_minutes: Optional[int],
) -> Optional[int]:
    """Average kbps this release must be, from its size and the book's runtime.

    This is the one number that explains why the same book turns up at 100MB
    and at 2GB, and it needs nothing an indexer has to volunteer: the size is
    in every search result and the runtime comes from the catalogue.

    None when the runtime is unknown, because the arithmetic has no meaning
    then. Also unreliable for an ABRIDGED release, whose real runtime is
    shorter than the catalogue's — the caller has that flag and should say so.
    """
    try:
        size = int(size_bytes or 0)
        minutes = int(runtime_minutes or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0 or minutes <= 0:
        return None
    return int(round((size * 8) / (minutes * 60) / 1000))


# "(1 of 5)", "[1/5]", "Part 2 of 6" — an uploader splitting a long book across
# several postings. Deliberately NOT matching "CD1" or "Disc 2", which are
# normal internal structure inside a COMPLETE release.
_PART_RE = re.compile(
    r"(?i)[\(\[]?\b(?:part|pt\.?)?\s*(\d{1,2})\s*(?:of|/)\s*(\d{1,2})\b[\)\]]?"
)


# A dramatised adaptation is not the audiobook. GraphicAudio and friends
# re-record the text with a full cast, music and sound effects, sell it in
# separately-purchased parts, and run to a completely different length — so it
# can never satisfy a runtime check against the Audible edition, and somebody
# expecting a narrator reading the book gets a radio play.
#
# Plenty of people want exactly that, so this warns rather than rejects.
_DRAMATIZED_RE = re.compile(
    r"(?i)\b(graphic\s*audio|a\s+movie\s+in\s+your\s+mind|dramati[sz]ed|"
    r"dramati[sz]ation|full[\s-]cast\s+dramati[sz])"
)


def is_dramatized(release_title: Optional[str]) -> bool:
    """True when a release says it is a dramatised adaptation."""
    return bool(_DRAMATIZED_RE.search(str(release_title or "")))


def part_marker(release_title: Optional[str]) -> Optional[tuple]:
    """``(part, total)`` when a title says it is one piece of a split posting.

    A 45-hour book posted as five 9-hour chunks is the failure that costs a
    whole download and is invisible until the files are decoded: every chunk
    plays perfectly and is simply not the book.

    Ambiguous on purpose-built sets — "The Stormlight Archive 1 - Way of Kings
    (1 of 5)" could be book one of five rather than part one of five — so the
    caller warns rather than drops.
    """
    match = _PART_RE.search(str(release_title or ""))
    if not match:
        return None
    try:
        part, total = int(match.group(1)), int(match.group(2))
    except (TypeError, ValueError):
        return None
    # 1 of 1 is a whole book saying so. Anything above 20 is not a part count.
    if total <= 1 or total > 20 or part < 1 or part > total:
        return None
    return part, total


# The floor below which a release cannot be a COMPLETE copy of the book.
#
# Audible's own files are 32 kbps mono (standard) and 64 kbps stereo
# (enhanced), and no real rip of a whole book lands under about 24. So when
# size-over-runtime implies less than this, the release is not a thinner encode
# of the book — it is a piece of it, a different shorter edition, or the wrong
# title. The old bounds allowed anything from 8 kbps up, which let a release
# holding a sixth of a 45-hour book rank first.
#
# Deliberately below Audible's own 32 so a legitimately frugal mono rip still
# passes; this is a floor for "impossible", not for "good".
DEFAULT_MIN_COMPLETE_KBPS = 24


def min_complete_kbps() -> float:
    """The configured floor, in kbps."""
    try:
        from core.settings import config_manager
        value = float(config_manager.get(
            "audiobooks.min_complete_kbps", DEFAULT_MIN_COMPLETE_KBPS))
        return value if value > 0 else DEFAULT_MIN_COMPLETE_KBPS
    except Exception:                                       # noqa: BLE001
        return DEFAULT_MIN_COMPLETE_KBPS


def too_small_to_be_complete(
    size_bytes: Optional[int],
    runtime_minutes: Optional[int],
    floor_kbps: Optional[float] = None,
) -> bool:
    """True when this size cannot hold this runtime at any real bitrate.

    Works in both directions without extra rules: the runtime always belongs to
    the edition being looked at, so an abridged entry is measured against the
    abridged runtime and an unabridged one against the unabridged runtime.
    Picking the wrong edition therefore fails this check by itself.
    """
    implied = implied_bitrate_kbps(size_bytes, runtime_minutes)
    if implied is None:
        return False
    floor = min_complete_kbps() if floor_kbps is None else float(floor_kbps)
    return implied < floor


def implied_runtime_minutes(
    size_bytes: Optional[int], kbps: Optional[int],
) -> Optional[float]:
    """How long this release can possibly play, at its STATED bitrate.

    The only pre-download way to catch a release that is missing half the book.
    Playing time can otherwise be measured only after downloading, by decoding
    the files, so a partial release costs a whole download to discover.

    Needs a bitrate the release actually names — inferring one from the size
    would be circular, since the size is what we are trying to explain.
    """
    try:
        size = int(size_bytes or 0)
        rate = int(kbps or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0 or rate <= 0:
        return None
    return (size * 8) / (rate * 1000) / 60.0


def runtime_coverage(
    size_bytes: Optional[int], kbps: Optional[int], runtime_minutes: Optional[int],
) -> Optional[float]:
    """Fraction of the book a release could hold, or None when unknowable.

    1.0 means it can hold the whole thing. 0.25 means that at the bitrate it
    claims, there is only a quarter of the book in there — a sample, a single
    part of a multi-part posting, or a bad rip.
    """
    implied = implied_runtime_minutes(size_bytes, kbps)
    try:
        expected = int(runtime_minutes or 0)
    except (TypeError, ValueError):
        return None
    if implied is None or expected <= 0:
        return None
    return implied / expected


# Below this, a release that names its own bitrate cannot be the whole book.
# Deliberately generous: a title saying "64kbps" over a VBR encode is common
# and reads low, so only a dramatic shortfall is called out.
_SHORT_COVERAGE = 0.7


def quality_band(kbps: Optional[int]) -> tuple:
    """``(band, explanation)`` for an implied bitrate, or ("", "") if unknown."""
    if not kbps or kbps <= 0:
        return "", ""
    for ceiling, band, text in _QUALITY_BANDS:
        if kbps < ceiling:
            return band, text
    return "", ""


def plausible_size(size_bytes: Optional[int], runtime_minutes: Optional[int]) -> bool:
    """Could a file this size be this book?

    Catches the two failure modes that waste a whole download slot: a few-MB
    "release" that is really a sample or a link file, and a huge bundle that
    merely mentions the title. With no runtime known only the absolute floor is
    applied, because guessing a ceiling from nothing would reject box sets.
    """
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    if size < _ABSOLUTE_MIN_BYTES:
        return False

    try:
        minutes = int(runtime_minutes or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return True
    return _MIN_BYTES_PER_MINUTE * minutes <= size <= _MAX_BYTES_PER_MINUTE * minutes


def build_queries(book: Dict[str, Any]) -> List[str]:
    """Search strings to try for a book, most specific first.

    Author plus title is the precise one. Title alone is the fallback, because
    uploaders frequently leave the author out of the release name entirely, and
    the relevance scoring re-checks the author afterwards anyway.

    Series entries get one extra query — a lot of releases are named
    "Series 03 - Title" and never mention the book title on its own.
    """
    title = str(book.get("title") or "").strip()
    if not title:
        return []

    authors = book.get("author_names") or []
    author = str(authors[0]).strip() if authors else ""
    series = (book.get("series") or [{}])
    series_entry = series[0] if series else {}
    series_title = str(series_entry.get("title") or "").strip()
    sequence = str(series_entry.get("sequence") or "").strip()

    queries: List[str] = []
    if author:
        queries.append(f"{author} {title}")
    queries.append(title)
    if series_title and sequence:
        queries.append(f"{series_title} {sequence}")

    seen = set()
    unique = []
    for query in queries:
        key = normalize_text(query)
        if key and key not in seen:
            seen.add(key)
            unique.append(query)
    return unique


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

@dataclass
class AudiobookRelease:
    """One downloadable candidate for a book."""

    source: str                     # "prowlarr" | "soulseek"
    protocol: str                   # "torrent" | "usenet" | "soulseek"
    title: str
    indexer: str
    size_bytes: int
    guid: str = ""
    download_url: Optional[str] = None
    magnet_uri: Optional[str] = None
    seeders: Optional[int] = None
    publish_date: Optional[str] = None
    audio_format: str = ""
    bitrate_kbps: Optional[int] = None
    abridged: bool = False
    # "match" | "mismatch" | "unknown" against the edition that was wanted.
    narrator_verdict: str = "unknown"
    abridgement_verdict: str = "unknown"
    language_verdict: str = "unknown"
    relevance: float = 0.0
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    # Only a Soulseek release carries this: the peer, the folder, and every
    # file in it. Torrents and NZBs need nothing beyond their URL, so it stays
    # None for them and the grab side branches on protocol, not on this.
    soulseek: Optional[Dict[str, Any]] = None
    # Filled in during ranking, where the book's runtime is in hand.
    implied_kbps: Optional[int] = None
    quality_band: str = ""
    quality_note: str = ""
    # Set only when the release NAMES its bitrate and the maths says it cannot
    # hold the whole book. The one partial-release check that works before
    # spending a download.
    short_warning: str = ""
    # A full-cast dramatisation rather than a reading. Its runtime bears no
    # relation to the book's, so the completeness gate must not measure it.
    dramatized: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "protocol": self.protocol,
            "title": self.title,
            "indexer": self.indexer,
            "size_bytes": self.size_bytes,
            "guid": self.guid,
            "download_url": self.download_url,
            "magnet_uri": self.magnet_uri,
            "seeders": self.seeders,
            "publish_date": self.publish_date,
            "audio_format": self.audio_format,
            "bitrate_kbps": self.bitrate_kbps,
            "abridged": self.abridged,
            "narrator_verdict": self.narrator_verdict,
            "abridgement_verdict": self.abridgement_verdict,
            "language_verdict": self.language_verdict,
            "relevance": self.relevance,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "soulseek": self.soulseek,
            "implied_kbps": self.implied_kbps,
            "quality_band": self.quality_band,
            "quality_note": self.quality_note,
            "short_warning": self.short_warning,
            "dramatized": self.dramatized,
        }


def score_release(
    release: AudiobookRelease,
    book: Dict[str, Any],
    wanted_narrator: Any = None,
) -> AudiobookRelease:
    """Rank one candidate, recording why.

    Relevance dominates by design. Every other signal is a tiebreak between
    releases that are already plausibly the right book — picking a beautifully
    seeded m4b of the wrong title is the only truly expensive mistake here.

    ``reasons`` is filled in as it goes so a user staring at an odd ordering can
    be shown the arithmetic instead of being asked to trust it.
    """
    reasons: List[str] = []

    # Why one release is 100MB and another 2GB, from data every result already
    # carries. Recorded even when it does not move the score, because the
    # person choosing needs it more than the ranker does.
    release.implied_kbps = implied_bitrate_kbps(
        release.size_bytes, book.get("runtime_minutes"),
    )
    release.quality_band, release.quality_note = quality_band(release.implied_kbps)
    if release.implied_kbps:
        reasons.append(f"~{release.implied_kbps} kbps for this runtime")

    relevance = title_relevance(
        release.title, book.get("title"), book.get("author_names"),
    )
    release.relevance = relevance
    score = relevance * 100.0
    reasons.append(f"relevance {relevance:.2f}")

    # A release that names its bitrate can be checked for length BEFORE it is
    # downloaded. Without this, a release holding a quarter of the book looks
    # identical to a well-compressed complete one until the files are decoded.
    # A dramatisation is a different product, not a different encode of the
    # same one. Flagged rather than dropped because plenty of people want it.
    warnings: List[str] = []
    if is_dramatized(release.title):
        release.dramatized = True
        warnings.append(
            "This is a dramatised adaptation (full cast, music, sound effects), not "
            "the audiobook. Different cast and a different running time."
        )
        reasons.append("dramatised adaptation, not the audiobook")
        score -= 50.0

    # A title that says it is one piece of a set. This is the cheapest catch
    # there is and needs no bitrate: the uploader already told us.
    part = part_marker(release.title)
    if part:
        warnings.append(
            f"Names itself part {part[0]} of {part[1]}. If that means the audio was "
            f"split across {part[1]} postings, this is roughly "
            f"{100 // part[1]}% of the book and will not import on its own."
        )
        reasons.append(f"says part {part[0]} of {part[1]}")
        score -= 60.0

    coverage = runtime_coverage(
        release.size_bytes, release.bitrate_kbps, book.get("runtime_minutes"),
    )
    if coverage is not None and coverage < _SHORT_COVERAGE and not release.abridged:
        hours = implied_runtime_minutes(release.size_bytes, release.bitrate_kbps) or 0
        warnings.append(
            f"At the {release.bitrate_kbps} kbps it claims, this is about "
            f"{hours / 60:.1f} hours — roughly {coverage * 100:.0f}% of the book."
        )
        reasons.append(f"only ~{coverage * 100:.0f}% of the runtime at its stated bitrate")
        score -= 40.0

    # Every problem found, not just the last one. A GraphicAudio release split
    # into five parts is two separate things wrong with it and the reader needs
    # both — assigning to short_warning in turn hid whichever came first.
    release.short_warning = " ".join(warnings)

    # The user's order when they have set one; the table above is the default
    # it is built from.
    format_bonus = _quality_format_scores().get(release.audio_format, 0.0)
    if format_bonus:
        score += format_bonus
        reasons.append(f"{release.audio_format} +{format_bonus:g}")

    # Judged against the edition that was actually wanted rather than assumed:
    # abridged is a different, shorter recording, but it is a legitimate thing
    # to have chosen.
    abridgement = abridgement_verdict(release.title, book.get("format_type"))
    release.abridgement_verdict = abridgement
    if abridgement == "mismatch":
        score -= 45.0
        reasons.append(
            "abridged, wanted unabridged -45" if release.abridged
            else "unabridged, wanted abridged -45"
        )
    elif abridgement == "match":
        score += 10.0
        reasons.append("edition matches +10")
    elif release.abridged and not str(book.get("format_type") or "").strip():
        # Nothing known about the wanted edition, so fall back to the old
        # assumption: unabridged is what people usually mean.
        score -= 30.0
        reasons.append("abridged -30")

    verdict = narrator_verdict(release.title, wanted_narrator)
    release.narrator_verdict = verdict
    if verdict == "match":
        # The listener asked for this reading specifically.
        score += 35.0
        reasons.append("narrator matches +35")
    elif verdict == "mismatch":
        score -= 60.0
        reasons.append("different narrator -60")

    language = language_verdict(release.title, book.get("language"))
    release.language_verdict = language
    if language == "mismatch":
        # A translation is a different recording in a language the listener did
        # not ask for, not a worse copy of this one.
        score -= 70.0
        reasons.append("wrong language -70")

    seeders = release.seeders
    if seeders is not None:
        if seeders <= 0:
            score -= 25.0
            reasons.append("no seeders -25")
        else:
            # Diminishing: 1 seeder to 10 is a real difference, 200 to 400 is not.
            bonus = min(12.0, 4.0 * math.log10(seeders + 1) * 2)
            score += bonus
            reasons.append(f"{seeders} seeders +{bonus:.1f}")
    elif release.protocol == "usenet":
        # Usenet has no seeders and retention is the real question; neutral
        # rather than penalised, or every usenet release loses to every torrent.
        reasons.append("usenet, no seeder signal")

    release.score = score
    release.reasons = reasons
    return release


def rank_releases(
    releases: Sequence[AudiobookRelease],
    book: Dict[str, Any],
    min_relevance: float = 0.5,
    narrator_mode: str = "exact",
) -> List[AudiobookRelease]:
    """Score, filter and order candidates, best first.

    Anything under ``min_relevance`` is dropped rather than ranked low: a
    half-matching release is not a worse version of the book, it is a different
    book, and leaving it in the list invites someone to grab it.

    ``narrator_mode`` is the listener's answer to "must this be the narrator I
    picked?". On Audible the narrator is baked into the ASIN — Jim Dale and
    Stephen Fry are different catalogue entries — so wanting a book already
    means wanting a reading. In "exact" a release that names a DIFFERENT
    narrator is dropped; in "any" it is merely outranked. A release that names
    no narrator is always allowed, because most of them do not.
    """
    # All of them: a full-cast recording credits a dozen people and any of them
    # naming the release is confirmation, not contradiction.
    wanted = book.get("narrator_names") or []

    scored = [score_release(release, book, wanted) for release in releases]
    keep = [r for r in scored if r.relevance >= min_relevance]
    # A release in the wrong language is never what was asked for, whatever the
    # narrator setting says.
    keep = [r for r in keep if r.language_verdict != "mismatch"]
    # Nor is the wrong EDITION. Audible sells abridged and unabridged as
    # separate ASINs with different runtimes, so the book being looked at is
    # already one or the other — an abridgement is a different product, not a
    # worse copy, exactly like the wrong language. Someone who wants the
    # abridgement opens its own catalogue entry, and this flips to match.
    #
    # Only a CONFIDENT mismatch is dropped: the verdict is "unknown" unless the
    # catalogue names the edition AND the release names its own, and most
    # releases name nothing.
    keep = [r for r in keep if r.abridgement_verdict != "mismatch"]
    # Releases the user has blocked, or that failed badly enough to be blocked
    # automatically. Without this the wishlist re-grabs the same broken posting
    # forever: it fails, returns to the wishlist, and is found again next pass.
    try:
        from core.audiobook_database import get_audiobook_db
        blocked = get_audiobook_db().blocked_keys()
        if blocked:
            from core.audiobook_database import AudiobookDatabase
            keep = [r for r in keep
                    if AudiobookDatabase.release_key(r.to_dict()) not in blocked]
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the blocklist: %s", exc)

    # What the user's quality profile refuses. Kept apart from the checks
    # above: those are facts about a release, this is taste, and a taste
    # setting must never be the reason a search looks broken — a profile that
    # cannot be read rejects nothing.
    try:
        from core.audiobook_quality import profile as quality_profile, rejection
        prof = quality_profile()
        keep = [r for r in keep if not rejection(r, prof)]
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not apply the quality profile: %s", exc)

    # Too small to be the whole book, at any bitrate a real audiobook uses.
    # Dropped rather than ranked low for the same reason as the wrong edition:
    # it is not a worse copy of what was asked for, it is not the thing.
    floor = min_complete_kbps()
    keep = [
        r for r in keep
        if not too_small_to_be_complete(r.size_bytes, book.get("runtime_minutes"), floor)
    ]
    if str(narrator_mode).lower() != "any":
        keep = [r for r in keep if r.narrator_verdict != "mismatch"]
    keep.sort(key=lambda r: (r.score, r.size_bytes), reverse=True)
    return keep


def deduplicate(releases: Sequence[AudiobookRelease]) -> List[AudiobookRelease]:
    """Collapse the same release arriving from several queries.

    Keyed on guid where the indexer gives one, and on indexer plus normalized
    title otherwise — running three query variants against one indexer returns
    the same upload three times.
    """
    seen = set()
    unique: List[AudiobookRelease] = []
    for release in releases:
        key = release.guid or f"{release.indexer}:{normalize_text(release.title)}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(release)
    return unique


# ---------------------------------------------------------------------------
# Prowlarr
# ---------------------------------------------------------------------------

def _configured_categories() -> List[int]:
    """Indexer categories to search, from settings.

    Falls back to the audiobook category rather than to Prowlarr's music
    default: searching the music tree for a book returns albums, and searching
    everything returns the whole internet.
    """
    try:
        from core.settings import config_manager
        configured = config_manager.get("audiobooks.prowlarr_categories", None)
    except Exception:                                       # noqa: BLE001
        configured = None
    if isinstance(configured, (list, tuple)) and configured:
        try:
            return [int(c) for c in configured]
        except (TypeError, ValueError):
            pass
    return [AUDIOBOOK_CATEGORY]


def release_from_prowlarr(result: Any, book: Dict[str, Any]) -> Optional[AudiobookRelease]:
    """Convert one ProwlarrSearchResult into a candidate.

    Returns None for anything with no title or no way to fetch it — a release
    with neither a download URL nor a magnet cannot be grabbed, so ranking it
    would only produce a button that fails.
    """
    title = str(getattr(result, "title", "") or "").strip()
    if not title:
        return None
    download_url = getattr(result, "download_url", None)
    magnet = getattr(result, "magnet_uri", None)
    if not download_url and not magnet:
        return None

    size = getattr(result, "size", 0) or 0
    if not plausible_size(size, book.get("runtime_minutes")):
        return None

    return AudiobookRelease(
        source="prowlarr",
        protocol=str(getattr(result, "protocol", "") or "torrent").lower(),
        title=title,
        indexer=str(getattr(result, "indexer_name", "") or ""),
        size_bytes=int(size),
        guid=str(getattr(result, "guid", "") or ""),
        download_url=download_url,
        magnet_uri=magnet,
        seeders=getattr(result, "seeders", None),
        publish_date=getattr(result, "publish_date", None),
        audio_format=detect_format(title),
        bitrate_kbps=detect_bitrate(title),
        abridged=is_abridged(title),
    )


def _run(coro):
    """Run one async call from sync code.

    Flask's handlers are synchronous and there is no loop running under them,
    so a throwaway loop is correct here. Same approach as
    core/video/client_grab.py, for the same reason.
    """
    return asyncio.run(coro)


def search_releases(
    book: Dict[str, Any],
    limit: int = 25,
    min_relevance: float = 0.5,
    prowlarr_client: Any = None,
    narrator_mode: str = "exact",
) -> List[AudiobookRelease]:
    """Every plausible release for a book, best first.

    Runs each query variant in turn and stops as soon as one produces enough
    ranked candidates. That matters more than it looks: every query is a real
    search landing on every configured indexer, and the precise
    "author + title" query answers most of the time, so firing all three
    variants unconditionally would triple the indexer load for nothing.

    Fails open — an unreachable or unconfigured Prowlarr returns [], and the
    caller reports "no releases found" rather than a 500.
    """
    queries = build_queries(book)
    if not queries:
        return []

    client = prowlarr_client
    if client is None:
        try:
            from core.prowlarr_client import ProwlarrClient
            client = ProwlarrClient()
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Could not build a Prowlarr client: %s", exc)
            return []

    if hasattr(client, "is_configured") and not client.is_configured():
        logger.debug("Prowlarr is not configured; no audiobook release search")
        return []

    last: List[AudiobookRelease] = []
    for step in iter_prowlarr_releases(
        book, limit=limit, min_relevance=min_relevance,
        prowlarr_client=client, narrator_mode=narrator_mode,
    ):
        last = step["releases"]
    return last


def iter_prowlarr_releases(
    book: Dict[str, Any],
    limit: int = 25,
    min_relevance: float = 0.5,
    prowlarr_client: Any = None,
    narrator_mode: str = "exact",
):
    """The same search, yielding after every query variant.

    Each yield is ``{"query", "releases", "complete"}`` where releases is the
    WHOLE ranked pool so far, not the new arrivals. Callers replace rather than
    append, because ranking is global: a later query can turn up a release that
    belongs above everything an earlier one found, and appending would pin it
    to the bottom forever.

    Exists so a UI can show results as they land instead of holding a modal
    blank through three sequential fan-outs to every indexer.
    """
    queries = build_queries(book)
    if not queries:
        return

    client = prowlarr_client
    if client is None:
        try:
            from core.prowlarr_client import ProwlarrClient
            client = ProwlarrClient()
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Could not build a Prowlarr client: %s", exc)
            return

    if hasattr(client, "is_configured") and not client.is_configured():
        logger.debug("Prowlarr is not configured; no audiobook release search")
        return

    categories = _configured_categories()
    collected: List[AudiobookRelease] = []

    for index, query in enumerate(queries):
        try:
            results = _run(client.search(query, categories=categories, limit=limit * 2))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Audiobook release search failed for %r: %s", query, exc)
            continue

        for result in results or []:
            release = release_from_prowlarr(result, book)
            if release is not None:
                collected.append(release)

        ranked = rank_releases(
            deduplicate(collected), book, min_relevance, narrator_mode,
        )[:limit]

        # Enough good candidates from a precise query: stop before spending
        # another search on every indexer. Unchanged from the blocking version
        # — indexer manners are the point, not an optimisation.
        enough = len(ranked) >= 5
        yield {
            "query": query,
            "releases": ranked,
            "complete": enough or index == len(queries) - 1,
        }
        if enough:
            return


def configured_chain() -> List[str]:
    """Which sources to ask, in order, from the AUDIOBOOK settings.

    Never music's. A user who runs Soulseek for music and torrents for books
    gets exactly that, and one side's chain can be changed without touching
    the other.
    """
    default = ["torrent", "usenet", "soulseek"]
    try:
        from core.settings import config_manager
        mode = str(config_manager.get("audiobooks.download_source.mode", "hybrid")
                   or "hybrid").strip().lower()
        if mode in ("torrent", "usenet", "soulseek"):
            return [mode]
        order = config_manager.get("audiobooks.download_source.hybrid_order", default)
        chosen = [str(item).strip().lower() for item in (order or []) if str(item).strip()]
        return chosen or default
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the audiobook source chain: %s", exc)
        return default


def search_all_sources(
    book: Dict[str, Any],
    limit: int = 25,
    min_relevance: float = 0.5,
    prowlarr_client: Any = None,
    soulseek_client: Any = None,
    narrator_mode: str = "exact",
) -> List[AudiobookRelease]:
    """Every candidate for a book across every configured source, best first.

    The chain decides who gets ASKED, not who wins. Once the answers are in
    they are ranked together on the same scale, because a peer with the right
    narrator beats a torrent with the wrong one no matter which came first.

    Both halves fail open independently: an unreachable Prowlarr still leaves
    Soulseek results, and the other way round.
    """
    chain = configured_chain()
    collected: List[AudiobookRelease] = []
    failures: List[Exception] = []

    # Prowlarr answers for both torrent and usenet, so one search covers both
    # rather than the same query going out twice.
    if prowlarr_client is not None or {"torrent", "usenet"} & set(chain):
        try:
            collected.extend(search_releases(
                book, limit=limit, min_relevance=min_relevance,
                prowlarr_client=prowlarr_client, narrator_mode=narrator_mode,
            ))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Prowlarr audiobook search failed: %s", exc)
            failures.append(exc)

    want_soulseek = soulseek_client is not None
    if not want_soulseek and "soulseek" in chain:
        try:
            from core.audiobook_soulseek import is_available
            want_soulseek = is_available()
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not check whether Soulseek is available: %s", exc)

    if want_soulseek:
        try:
            from core.audiobook_soulseek import search as search_soulseek
            collected.extend(search_soulseek(
                book, limit=limit, min_relevance=min_relevance,
                client=soulseek_client, narrator_mode=narrator_mode,
            ))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Soulseek audiobook search failed: %s", exc)
            failures.append(exc)

    # A source that ERRORED did not answer "no". If nothing was found and
    # something broke, say so: an empty shelf would tell the user their book
    # does not exist and send a wishlist row into a backoff it did not earn.
    #
    # Deliberately not "every source failed". Requiring that made the verdict
    # depend on whether slskd happened to be configured — Prowlarr down plus a
    # configured-but-empty Soulseek reported "no releases found" and hid the
    # outage, while the same outage on an install without slskd reported the
    # error. Same failure, two answers.
    #
    # Results still win: anything actually found means no error, however many
    # other sources broke getting there.
    if failures and not collected:
        raise failures[0]

    # Re-ranked as one pool. Both halves arrive already ranked, but each was
    # ranked only against its own kind.
    return rank_releases(
        deduplicate(collected), book, min_relevance, narrator_mode,
    )[:limit]
