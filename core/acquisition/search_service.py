"""Network/DB split for server-owned acquisition searches.

Source calls run concurrently without holding a SQLite transaction. Their typed
results can then be persisted in one short transaction and evaluated through the
shared Decision Engine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Protocol, Sequence, Tuple

from core.acquisition.search_contract import (
    AggregatedCandidates,
    CandidateParser,
    ParsedBatch,
    SearchCriteria,
    aggregate_candidates,
    parse_candidate_batch,
    safe_external_error,
)


SOURCE_STATUSES = frozenset({"searched", "unsupported", "unconfigured", "failed"})


class AcquisitionSearchAdapter(Protocol):
    """One explicitly named source and its provider-payload parser."""

    source: str
    parser: CandidateParser

    def is_configured(self) -> bool: ...

    async def search(self, criteria: SearchCriteria) -> Iterable[Any]: ...


@dataclass(frozen=True)
class SourceSearchOutcome:
    source: str
    status: str
    batch: Optional[ParsedBatch] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in SOURCE_STATUSES:
            raise ValueError(f"invalid source search status: {self.status}")

    @property
    def candidate_count(self) -> int:
        return len(self.batch.candidates) if self.batch else 0

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "candidate_count": self.candidate_count,
            "skipped_count": self.batch.skipped if self.batch else 0,
            "parse_failures": (
                [failure.to_dict() for failure in self.batch.failures]
                if self.batch else []
            ),
            "error": self.error,
        }


@dataclass(frozen=True)
class SearchCollection:
    criteria: SearchCriteria
    outcomes: Tuple[SourceSearchOutcome, ...]

    @property
    def batches(self) -> Tuple[ParsedBatch, ...]:
        return tuple(
            outcome.batch
            for outcome in self.outcomes
            if outcome.status == "searched" and outcome.batch is not None
        )

    @property
    def candidate_count(self) -> int:
        return sum(outcome.candidate_count for outcome in self.outcomes)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "sources": [outcome.to_public_dict() for outcome in self.outcomes],
        }


def _adapter_source(adapter: AcquisitionSearchAdapter) -> str:
    source = str(adapter.source or "").strip().lower()
    if not source:
        raise ValueError("acquisition search adapter source is required")
    parser_source = str(adapter.parser.source or "").strip().lower()
    if parser_source != source:
        raise ValueError(
            f"search adapter {source} uses parser for {parser_source or '(empty)'}")
    return source


async def _collect_one(
    criteria: SearchCriteria,
    adapter: AcquisitionSearchAdapter,
    *,
    timeout_seconds: float,
) -> SourceSearchOutcome:
    source = _adapter_source(adapter)
    if not criteria.supports_source(source):
        return SourceSearchOutcome(source, "unsupported")
    try:
        if not adapter.is_configured():
            return SourceSearchOutcome(source, "unconfigured")
        payloads = await asyncio.wait_for(
            adapter.search(criteria), timeout=timeout_seconds)
        batch = parse_candidate_batch(
            adapter.parser, payloads, criteria=criteria)
        return SourceSearchOutcome(source, "searched", batch=batch)
    except asyncio.TimeoutError:
        return SourceSearchOutcome(
            source, "failed", error="Source search timed out")
    except Exception as exc:  # noqa: BLE001 - source boundary isolates providers
        return SourceSearchOutcome(
            source, "failed", error=safe_external_error(exc))


async def collect_search_results(
    criteria: SearchCriteria,
    adapters: Sequence[AcquisitionSearchAdapter],
    *,
    timeout_seconds: float = 30.0,
) -> SearchCollection:
    """Search every configured compatible source concurrently."""
    timeout_seconds = float(timeout_seconds)
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise ValueError("search timeout must be between 0 and 300 seconds")
    normalized = []
    seen = set()
    for adapter in adapters:
        source = _adapter_source(adapter)
        if source in seen:
            raise ValueError(f"duplicate acquisition search adapter: {source}")
        seen.add(source)
        normalized.append(adapter)
    if not normalized:
        return SearchCollection(criteria, tuple())
    outcomes = await asyncio.gather(*(
        _collect_one(criteria, adapter, timeout_seconds=timeout_seconds)
        for adapter in normalized
    ))
    return SearchCollection(criteria, tuple(outcomes))


def persist_search_results(
    conn: Any,
    collection: SearchCollection,
    *,
    ttl_seconds: int = 6 * 60 * 60,
    now: Optional[float] = None,
) -> AggregatedCandidates:
    """Persist a completed network collection in the caller's transaction."""
    return aggregate_candidates(
        conn,
        criteria=collection.criteria,
        batches=collection.batches,
        ttl_seconds=ttl_seconds,
        now=now,
    )


__all__ = [
    "AcquisitionSearchAdapter",
    "SOURCE_STATUSES",
    "SearchCollection",
    "SourceSearchOutcome",
    "collect_search_results",
    "persist_search_results",
]
