"""Prowlarr search adapter for release-bundle acquisition candidates.

Prowlarr reports releases, never individual recordings. This adapter therefore
declares Usenet/Torrent bundle scope explicitly and never creates the legacy
pseudo-track projection prohibited by ADR-08.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

from config.settings import config_manager
from core.acquisition.search_contract import (
    CandidateParseError,
    ParsedCandidate,
    SearchCriteria,
)
from core.download_plugins.candidate_store import (
    CandidateStore,
    candidate_binding,
    get_candidate_store,
)
from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrSearchResult,
)


PROWLARR_SOURCES = frozenset({"usenet", "torrent"})

_FORMAT_PATTERNS = (
    ("flac", re.compile(r"\b(?:flac|lossless)\b", re.IGNORECASE)),
    ("alac", re.compile(r"\balac\b", re.IGNORECASE)),
    ("aac", re.compile(r"\b(?:aac|m4a)\b", re.IGNORECASE)),
    ("ogg", re.compile(r"\b(?:ogg|vorbis)\b", re.IGNORECASE)),
    ("opus", re.compile(r"\bopus\b", re.IGNORECASE)),
    ("mp3", re.compile(r"\bmp3\b", re.IGNORECASE)),
)
_EDITION_TERMS = (
    "deluxe edition",
    "expanded edition",
    "anniversary edition",
    "special edition",
    "limited edition",
    "collector edition",
    "remastered",
    "remaster",
    "reissue",
    "vinyl",
)
_TRAILING_METADATA = re.compile(
    r"\s*[\[(](?:19|20)\d{2}|\s*[\[(][^\])]*(?:flac|lossless|alac|aac|m4a|"
    r"ogg|opus|mp3|web|cd|vinyl|24\s*bit|16\s*bit|\d{3}\s*kbps|"
    r"remaster(?:ed)?|deluxe|expanded|anniversary|edition)[^\])]*[\])]\s*$",
    re.IGNORECASE,
)


def _normalized_words(value: Any) -> Tuple[str, ...]:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    return tuple(re.findall(r"[a-z0-9]+", folded.casefold()))


def parse_release_title(value: str) -> Tuple[str, str]:
    """Return an indexer release's explicit artist/title components."""
    title = str(value or "").strip()
    if not title:
        return "", ""
    parts = re.split(r"\s+-\s+", title, maxsplit=1)
    if len(parts) != 2:
        return "", title
    artist, release = (part.strip() for part in parts)
    if not artist or re.match(r"^(?:https?|magnet):", artist, re.IGNORECASE):
        return "", title
    return artist, release or title


def _strip_trailing_metadata(value: str) -> str:
    current = value.strip()
    while True:
        stripped = _TRAILING_METADATA.sub("", current).strip()
        if stripped == current:
            return current
        current = stripped


def _mapped_fact(parsed: str, expected: Optional[str]) -> str:
    """Map only exact normalized words; never turn a fuzzy title into identity."""
    cleaned = _strip_trailing_metadata(parsed)
    if expected and _normalized_words(cleaned) == _normalized_words(expected):
        return str(expected).strip()
    return cleaned


def _quality_facts(title: str) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for quality_format, pattern in _FORMAT_PATTERNS:
        if pattern.search(title):
            facts["format"] = quality_format
            break
    bit_depth = re.search(r"\b(16|24|32)\s*(?:bit|bits)\b", title, re.IGNORECASE)
    if bit_depth:
        facts["bit_depth"] = int(bit_depth.group(1))
    sample_rate = re.search(
        r"\b(44(?:\.1)?|48|88(?:\.2)?|96|176(?:\.4)?|192)\s*k(?:hz)?\b",
        title,
        re.IGNORECASE,
    )
    if sample_rate:
        facts["sample_rate"] = int(float(sample_rate.group(1)) * 1000)
    bitrate = re.search(r"\b(128|192|256|320)\s*k(?:bps)?\b", title, re.IGNORECASE)
    if bitrate:
        facts["bitrate"] = int(bitrate.group(1))
    return facts


def _year(title: str) -> Optional[int]:
    match = re.search(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)", title)
    return int(match.group(1)) if match else None


def _edition(title: str, expected: Optional[str]) -> Optional[str]:
    title_words = set(_normalized_words(title))
    if expected:
        expected_words = set(_normalized_words(expected))
        if expected_words and expected_words <= title_words:
            return expected
    normalized = " ".join(_normalized_words(title))
    return next((term for term in _EDITION_TERMS if term in normalized), None)


def _age_seconds(value: Optional[str], now: float) -> Optional[int]:
    if not value:
        return None
    try:
        published = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max(0, int(now - published.timestamp()))


def parse_indexer_ids(value: Any) -> Tuple[int, ...]:
    """Normalize a comma-separated Prowlarr indexer allowlist."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        chunks: Iterable[Any] = value
    else:
        chunks = str(value or "").split(",")
    parsed = []
    for chunk in chunks:
        try:
            indexer_id = int(str(chunk).strip())
        except (TypeError, ValueError):
            continue
        if indexer_id > 0 and indexer_id not in parsed:
            parsed.append(indexer_id)
    return tuple(parsed)


@dataclass
class ProwlarrCandidateParser:
    source: str
    candidate_store: Optional[CandidateStore] = None
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self.source = str(self.source or "").strip().lower()
        if self.source not in PROWLARR_SOURCES:
            raise ValueError("Prowlarr acquisition source must be usenet or torrent")

    def parse(
        self, payload: Any, *, criteria: SearchCriteria,
    ) -> Optional[ParsedCandidate]:
        if not isinstance(payload, ProwlarrSearchResult):
            raise CandidateParseError("Prowlarr returned an unexpected result type")
        protocol = str(payload.protocol or "").strip().lower()
        if protocol != self.source:
            return None
        title = str(payload.title or "").strip()
        if not title:
            raise CandidateParseError("Prowlarr release title is missing")
        download_url = (
            payload.magnet_uri or payload.download_url
            if self.source == "torrent" else payload.download_url
        )
        if not download_url:
            raise CandidateParseError("Prowlarr release has no download reference")
        download_url = str(download_url).strip()
        allowed_reference = (
            re.match(r"^https?://", download_url, re.IGNORECASE)
            or (
                self.source == "torrent"
                and download_url.lower().startswith("magnet:")
            )
        )
        if not allowed_reference:
            raise CandidateParseError(
                "Prowlarr release uses an unsupported download-reference scheme")
        store = self.candidate_store or get_candidate_store()
        with candidate_binding(criteria.profile_id):
            server_ref = store.put(download_url)
        parsed_artist, parsed_release = parse_release_title(title)
        artist = _mapped_fact(parsed_artist, criteria.artist) if parsed_artist else None
        release_title = _mapped_fact(parsed_release, criteria.release_title)
        facts = {
            "artist": artist,
            "release_title": release_title,
            "edition": _edition(title, criteria.edition),
            "year": _year(title),
            "track_count": None,
            **_quality_facts(title),
        }
        raw = {
            "indexer_id": payload.indexer_id,
            "publish_date": payload.publish_date,
            "categories": list(payload.categories),
            "info_url": payload.info_url,
            "download_url": payload.download_url,
            "magnet_uri": payload.magnet_uri,
            "provider": dict(payload.raw),
        }
        return ParsedCandidate(
            source=self.source,
            protocol=protocol,
            content_scope="release_bundle",
            server_ref=server_ref,
            title=title,
            indexer=payload.indexer_name or None,
            guid=payload.guid or None,
            size_bytes=payload.size if payload.size and payload.size > 0 else None,
            age_seconds=_age_seconds(payload.publish_date, self.clock()),
            grabs=payload.grabs,
            seeders=payload.seeders,
            facts=facts,
            raw_payload=raw,
        )


class ProwlarrAcquisitionAdapter:
    """Search one explicit Prowlarr protocol as one acquisition source."""

    def __init__(
        self,
        source: str,
        *,
        client: Optional[ProwlarrClient] = None,
        parser: Optional[ProwlarrCandidateParser] = None,
        download_client_configured: Optional[Callable[[], bool]] = None,
        indexer_ids_getter: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.source = str(source or "").strip().lower()
        if self.source not in PROWLARR_SOURCES:
            raise ValueError("Prowlarr acquisition source must be usenet or torrent")
        self.client = client or ProwlarrClient()
        self.parser = parser or ProwlarrCandidateParser(self.source)
        if self.parser.source != self.source:
            raise ValueError("Prowlarr adapter/parser source mismatch")
        self._download_client_configured = download_client_configured or (lambda: True)
        self._indexer_ids_getter = indexer_ids_getter or (
            lambda: config_manager.get("prowlarr.indexer_ids", ""))

    def is_configured(self) -> bool:
        return bool(
            self.client.is_configured() and self._download_client_configured())

    async def search(self, criteria: SearchCriteria) -> Iterable[ProwlarrSearchResult]:
        query = criteria.text_query.strip()
        if not query:
            raise ValueError("acquisition request has no searchable catalog terms")
        return await self.client.search(
            query,
            categories=DEFAULT_MUSIC_CATEGORIES,
            indexer_ids=parse_indexer_ids(self._indexer_ids_getter()),
        )


def default_usenet_search_adapter() -> ProwlarrAcquisitionAdapter:
    """Phase-5 default; Torrent remains behind its later source cutover."""
    def client_configured() -> bool:
        from core.usenet_clients import get_active_adapter

        adapter = get_active_adapter()
        return bool(adapter and adapter.is_configured())

    return ProwlarrAcquisitionAdapter(
        "usenet", download_client_configured=client_configured)


__all__ = [
    "PROWLARR_SOURCES",
    "ProwlarrAcquisitionAdapter",
    "ProwlarrCandidateParser",
    "default_usenet_search_adapter",
    "parse_indexer_ids",
    "parse_release_title",
]
