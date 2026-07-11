"""Typed parser and aggregation boundary for acquisition searches.

Search adapters return provider-specific payloads. Parsers turn those payloads
into this source-explicit shape before persistence or policy evaluation. Keeping
the boundary here prevents provider quirks and source inference from leaking into
the Decision Engine (audit section 9.4 and ADR-08).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple

from core.acquisition.candidates import (
    ReleaseCandidate,
    redact_sensitive_text,
    register_candidate,
)
from core.acquisition.capabilities import require_source_capabilities
from core.acquisition.decision_engine import CatalogContext
from core.acquisition.requests import AcquisitionRequest


class CandidateParseError(ValueError):
    """One provider result could not be normalized into a candidate."""


def safe_external_error(exc: Exception) -> str:
    return redact_sensitive_text(exc, max_length=500)


@dataclass(frozen=True)
class SearchCriteria:
    """Server-owned search intent passed to source adapters."""

    request_id: str
    profile_id: int
    request_scope: str
    entity_id: int
    content_scope: str
    artist: Optional[str] = None
    release_title: Optional[str] = None
    edition: Optional[str] = None
    track_count: Optional[int] = None
    identifiers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifiers", dict(self.identifiers))

    @property
    def text_query(self) -> str:
        parts = (self.artist, self.release_title, self.edition)
        return " ".join(str(part).strip() for part in parts if str(part or "").strip())

    def supports_source(self, source: str) -> bool:
        return require_source_capabilities(source).content_scope == self.content_scope


def _request_content_scope(request: AcquisitionRequest) -> str:
    explicit = str(request.search_options.get("content_scope") or "").strip().lower()
    if explicit in {"recording", "release_bundle"}:
        return explicit
    if request.scope == "recording":
        return "recording"
    if request.scope in {"release_group", "release_edition", "artist_missing"}:
        return "release_bundle"
    if request.scope == "upgrade":
        entity_type = str(
            request.search_options.get("entity_type") or "").strip().lower()
        if entity_type == "recording":
            return "recording"
        if entity_type in {"release_group", "release_edition"}:
            return "release_bundle"
    raise ValueError("acquisition request does not define a searchable content scope")


def _identifiers(request: AcquisitionRequest) -> Dict[str, str]:
    raw = request.search_options.get("identifiers") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("acquisition search identifiers must be an object")
    identifiers = {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value or "").strip()
    }
    for key in ("recording_id", "release_group_id", "release_edition_id"):
        value = request.search_options.get(key)
        if value not in (None, ""):
            identifiers.setdefault(key, str(value))
    return identifiers


def build_search_criteria(
    request: AcquisitionRequest, catalog: CatalogContext,
) -> SearchCriteria:
    """Build criteria only from the persisted request and current catalog."""
    return SearchCriteria(
        request_id=request.id,
        profile_id=request.profile_id,
        request_scope=request.scope,
        entity_id=request.entity_id,
        content_scope=_request_content_scope(request),
        artist=catalog.artist,
        release_title=catalog.release_title,
        edition=catalog.edition,
        track_count=catalog.track_count,
        identifiers=_identifiers(request),
    )


def _optional_nonnegative_int(value: Any, name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CandidateParseError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise CandidateParseError(f"{name} must be a non-negative integer")
    return parsed


@dataclass(frozen=True)
class ParsedCandidate:
    """Source-explicit candidate facts produced by one parser."""

    source: str
    protocol: str
    content_scope: str
    server_ref: str
    title: str
    indexer: Optional[str] = None
    guid: Optional[str] = None
    size_bytes: Optional[int] = None
    age_seconds: Optional[int] = None
    grabs: Optional[int] = None
    seeders: Optional[int] = None
    facts: Mapping[str, Any] = field(default_factory=dict)
    raw_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source = str(self.source or "").strip().lower()
        protocol = str(self.protocol or "").strip().lower()
        content_scope = str(self.content_scope or "").strip().lower()
        server_ref = str(self.server_ref or "").strip()
        title = str(self.title or "").strip()
        capabilities = require_source_capabilities(source)
        if content_scope != capabilities.content_scope:
            raise CandidateParseError(
                f"source {source} declares {capabilities.content_scope}, not {content_scope}")
        if not protocol or not server_ref or not title:
            raise CandidateParseError(
                "parsed candidate protocol, server_ref and title are required")
        if "://" in server_ref or server_ref.lower().startswith("magnet:"):
            raise CandidateParseError("parsed candidate server_ref must be opaque")
        if not isinstance(self.facts, Mapping) or not isinstance(self.raw_payload, Mapping):
            raise CandidateParseError("parsed candidate facts and raw_payload must be objects")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "content_scope", content_scope)
        object.__setattr__(self, "server_ref", server_ref)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "facts", dict(self.facts))
        object.__setattr__(self, "raw_payload", dict(self.raw_payload))
        for name in ("size_bytes", "age_seconds", "grabs", "seeders"):
            object.__setattr__(
                self, name, _optional_nonnegative_int(getattr(self, name), name))


class CandidateParser(Protocol):
    """Parser contract implemented per source/provider payload."""

    source: str

    def parse(
        self, payload: Any, *, criteria: SearchCriteria,
    ) -> Optional[ParsedCandidate]: ...


@dataclass(frozen=True)
class ParseFailure:
    source: str
    position: int
    error: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "position": self.position,
            "error": self.error,
        }


@dataclass(frozen=True)
class ParsedBatch:
    source: str
    candidates: Tuple[ParsedCandidate, ...]
    failures: Tuple[ParseFailure, ...]
    skipped: int = 0


def parse_candidate_batch(
    parser: CandidateParser,
    payloads: Iterable[Any],
    *,
    criteria: SearchCriteria,
) -> ParsedBatch:
    """Normalize one source batch while isolating malformed provider rows."""
    source = str(parser.source or "").strip().lower()
    if not criteria.supports_source(source):
        raise ValueError(
            f"source {source} cannot search {criteria.content_scope} requests")
    parsed = []
    failures = []
    skipped = 0
    for position, payload in enumerate(payloads):
        try:
            candidate = parser.parse(payload, criteria=criteria)
            if candidate is None:
                skipped += 1
                continue
            if candidate.source != source:
                raise CandidateParseError(
                    f"parser {source} returned candidate for {candidate.source}")
            if candidate.content_scope != criteria.content_scope:
                raise CandidateParseError(
                    "candidate content scope does not match acquisition request")
            parsed.append(candidate)
        except (CandidateParseError, KeyError, TypeError, ValueError) as exc:
            failures.append(ParseFailure(source, position, safe_external_error(exc)))
    return ParsedBatch(source, tuple(parsed), tuple(failures), skipped)


@dataclass(frozen=True)
class CandidateRegistration:
    candidate: ReleaseCandidate
    created: bool


@dataclass(frozen=True)
class AggregatedCandidates:
    registrations: Tuple[CandidateRegistration, ...]

    @property
    def created_count(self) -> int:
        return sum(item.created for item in self.registrations)

    @property
    def refreshed_count(self) -> int:
        return len(self.registrations) - self.created_count


def aggregate_candidates(
    conn: Any,
    *,
    criteria: SearchCriteria,
    batches: Sequence[ParsedBatch],
    ttl_seconds: int = 6 * 60 * 60,
    now: Optional[float] = None,
) -> AggregatedCandidates:
    """Persist parsed candidates under the server-owned request identity."""
    registrations = []
    for batch in batches:
        if not criteria.supports_source(batch.source):
            raise ValueError(
                f"source {batch.source} cannot search {criteria.content_scope} requests")
        for parsed in batch.candidates:
            if parsed.source != batch.source:
                raise ValueError("parsed candidate source does not match its batch")
            if parsed.content_scope != criteria.content_scope:
                raise ValueError(
                    "parsed candidate content scope does not match acquisition request")
            candidate, created = register_candidate(
                conn,
                request_id=criteria.request_id,
                source=parsed.source,
                protocol=parsed.protocol,
                content_scope=parsed.content_scope,
                server_ref=parsed.server_ref,
                title=parsed.title,
                indexer=parsed.indexer,
                guid=parsed.guid,
                size_bytes=parsed.size_bytes,
                age_seconds=parsed.age_seconds,
                grabs=parsed.grabs,
                seeders=parsed.seeders,
                facts=parsed.facts,
                raw_payload=parsed.raw_payload,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            registrations.append(CandidateRegistration(candidate, created))
    return AggregatedCandidates(tuple(registrations))


__all__ = [
    "AggregatedCandidates",
    "CandidateParseError",
    "CandidateParser",
    "CandidateRegistration",
    "ParseFailure",
    "ParsedBatch",
    "ParsedCandidate",
    "SearchCriteria",
    "aggregate_candidates",
    "build_search_criteria",
    "parse_candidate_batch",
    "safe_external_error",
]
