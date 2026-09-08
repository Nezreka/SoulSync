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

Soulseek is the third source in the configured chain and is not wired in here
yet — ``search_releases`` is shaped to take more sources without changing its
callers, and the ranking below is source-agnostic.

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


def narrator_verdict(
    release_title: Optional[str],
    wanted_narrator: Optional[str],
) -> str:
    """"match", "mismatch" or "unknown" for a release against a wanted narrator.

    "unknown" is the common case and is NOT a failure — see narrator_in_release.
    """
    if not wanted_narrator:
        return "unknown"
    named = narrator_in_release(release_title)
    if not named:
        return "unknown"
    return "match" if same_person(named, wanted_narrator) else "mismatch"


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

    source: str                     # "prowlarr" today; "soulseek" later
    protocol: str                   # "torrent" | "usenet"
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
    relevance: float = 0.0
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)

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
            "relevance": self.relevance,
            "score": round(self.score, 2),
            "reasons": self.reasons,
        }


def score_release(
    release: AudiobookRelease,
    book: Dict[str, Any],
    wanted_narrator: Optional[str] = None,
) -> AudiobookRelease:
    """Rank one candidate, recording why.

    Relevance dominates by design. Every other signal is a tiebreak between
    releases that are already plausibly the right book — picking a beautifully
    seeded m4b of the wrong title is the only truly expensive mistake here.

    ``reasons`` is filled in as it goes so a user staring at an odd ordering can
    be shown the arithmetic instead of being asked to trust it.
    """
    reasons: List[str] = []

    relevance = title_relevance(
        release.title, book.get("title"), book.get("author_names"),
    )
    release.relevance = relevance
    score = relevance * 100.0
    reasons.append(f"relevance {relevance:.2f}")

    format_bonus = _FORMAT_SCORES.get(release.audio_format, 0.0)
    if format_bonus:
        score += format_bonus
        reasons.append(f"{release.audio_format} +{format_bonus:g}")

    # Abridged is a different, shorter recording. Wanted occasionally, almost
    # never what someone asked for by default.
    if release.abridged:
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
    wanted = ""
    narrators = book.get("narrator_names") or []
    if narrators:
        wanted = str(narrators[0])

    scored = [score_release(release, book, wanted) for release in releases]
    keep = [r for r in scored if r.relevance >= min_relevance]
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

    categories = _configured_categories()
    collected: List[AudiobookRelease] = []

    for query in queries:
        try:
            results = _run(client.search(query, categories=categories, limit=limit * 2))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Audiobook release search failed for %r: %s", query, exc)
            continue

        for result in results or []:
            release = release_from_prowlarr(result, book)
            if release is not None:
                collected.append(release)

        ranked = rank_releases(deduplicate(collected), book, min_relevance, narrator_mode)
        # Enough good candidates from a precise query: stop before spending
        # another search on every indexer.
        if len(ranked) >= 5:
            return ranked[:limit]

    return rank_releases(
        deduplicate(collected), book, min_relevance, narrator_mode,
    )[:limit]
