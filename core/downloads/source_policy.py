"""Shared source-selection policy for every download entry point.

The legacy orchestrator and Library-v2 acquisition must interpret source mode,
hybrid order and quality-profile search mode identically.  This module is the
pure contract; callers keep ownership of network I/O and candidate parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple


SEARCH_MODE_PRIORITY = "priority"
SEARCH_MODE_BEST_QUALITY = "best_quality"
SEARCH_MODES = frozenset({SEARCH_MODE_PRIORITY, SEARCH_MODE_BEST_QUALITY})

# Streaming plugins (and Torrent/Usenet) stamp their canonical source name as
# the download's "username"; Soulseek uses the real peer name instead, so
# anything not in this set is a Soulseek peer. Single source of truth for
# every consumer that needs to bucket a username by source (retry budgeting,
# engine-status fallback, candidate-source inference) — they used to each
# hardcode their own copy, which silently drifted apart whenever a new source
# was added to one copy and not the others.
STREAMING_SOURCE_NAMES = frozenset((
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr', 'soundcloud',
    'amazon', 'torrent', 'usenet',
))

# The subset of STREAMING_SOURCE_NAMES that are release-grabbing sources
# (whole-release download engines), not per-track streaming rippers.
RELEASE_SOURCE_NAMES = frozenset(('torrent', 'usenet'))


def canonical_source_name(value: Any) -> Optional[str]:
    """Normalize config aliases without initializing the plugin registry."""
    name = str(value or "").strip().lower()
    if not name:
        return None
    return {"deezer_dl": "deezer"}.get(name, name)


@dataclass(frozen=True)
class SourcePolicy:
    mode: str
    search_mode: str
    source_chain: Tuple[str, ...]
    rank_candidates_by_quality: bool = False

    @property
    def search_all_sources(self) -> bool:
        return (
            self.mode == "hybrid"
            and self.search_mode == SEARCH_MODE_BEST_QUALITY
        )

    @property
    def quality_first(self) -> bool:
        return (
            self.search_mode == SEARCH_MODE_BEST_QUALITY
            or self.rank_candidates_by_quality
        )

    @property
    def source_priorities(self) -> Mapping[str, int]:
        return {source: index for index, source in enumerate(self.source_chain)}

    def permits(self, source: str) -> bool:
        return canonical_source_name(source) in self.source_chain


def resolve_source_policy(
    *,
    mode: Any,
    hybrid_order: Sequence[Any] = (),
    hybrid_primary: Any = "soulseek",
    hybrid_secondary: Any = "youtube",
    search_mode: Any = SEARCH_MODE_PRIORITY,
    rank_candidates_by_quality: Any = False,
    normalize: Optional[Callable[[Any], Optional[str]]] = None,
    available_sources: Optional[Iterable[str]] = None,
) -> SourcePolicy:
    """Resolve one deterministic source chain from existing settings."""
    normalizer = normalize or canonical_source_name
    resolved_mode = str(mode or "soulseek").strip().lower()
    resolved_search_mode = str(search_mode or SEARCH_MODE_PRIORITY).strip().lower()
    if resolved_search_mode not in SEARCH_MODES:
        resolved_search_mode = SEARCH_MODE_PRIORITY

    available_order = tuple(
        source
        for item in (available_sources or ())
        if (source := canonical_source_name(item))
    )
    available = set(available_order) if available_sources is not None else None

    def accepted(value: Any) -> Optional[str]:
        source = normalizer(value)
        source = canonical_source_name(source)
        if not source:
            return None
        if available is not None and source not in available:
            return None
        return source

    if resolved_mode != "hybrid":
        source = accepted(resolved_mode)
        fallback = accepted("soulseek") or (available_order[0] if available_order else None)
        chain = (source or fallback,) if source or fallback else ()
    else:
        raw_chain = list(hybrid_order or ())
        if not raw_chain:
            raw_chain = [hybrid_primary, hybrid_secondary]
        ordered = []
        seen = set()
        for raw in raw_chain:
            source = accepted(raw)
            if source is None:
                # Compatibility with the legacy primary/secondary resolver:
                # obsolete or unavailable configured slots fell back to
                # Soulseek instead of silently shortening the chain.
                source = accepted("soulseek")
            if source and source not in seen:
                ordered.append(source)
                seen.add(source)
        if not ordered:
            fallback = accepted("soulseek") or (
                available_order[0] if available_order else None
            )
            if fallback:
                ordered.append(fallback)
        chain = tuple(ordered)

    return SourcePolicy(
        mode=resolved_mode,
        search_mode=resolved_search_mode,
        source_chain=chain,
        rank_candidates_by_quality=bool(rank_candidates_by_quality),
    )


def source_policy_from_settings(
    config_get: Callable[[str, Any], Any],
    *,
    profile: Optional[Mapping[str, Any]] = None,
    normalize: Optional[Callable[[Any], Optional[str]]] = None,
    available_sources: Optional[Iterable[str]] = None,
) -> SourcePolicy:
    profile = dict(profile or {})
    return resolve_source_policy(
        mode=config_get("download_source.mode", "soulseek"),
        hybrid_order=config_get("download_source.hybrid_order", []) or [],
        hybrid_primary=config_get("download_source.hybrid_primary", "soulseek"),
        hybrid_secondary=config_get("download_source.hybrid_secondary", "youtube"),
        search_mode=profile.get("search_mode", SEARCH_MODE_PRIORITY),
        rank_candidates_by_quality=profile.get("rank_candidates_by_quality", False),
        normalize=normalize,
        available_sources=available_sources,
    )


__all__ = [
    "SEARCH_MODE_BEST_QUALITY",
    "SEARCH_MODE_PRIORITY",
    "SEARCH_MODES",
    "STREAMING_SOURCE_NAMES",
    "RELEASE_SOURCE_NAMES",
    "SourcePolicy",
    "canonical_source_name",
    "resolve_source_policy",
    "source_policy_from_settings",
]
