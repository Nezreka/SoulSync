"""Strict three-state artist-discography orchestration.

The orchestrator is provider-agnostic. Provider adapters own their operation and
return RESULTS, EMPTY or ACCESS_ERROR. Search, import and enrichment keep their
existing best-effort behaviour.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.metadata import registry as metadata_registry
from core.metadata.discography import (
    _build_artist_detail_release_card,
    _build_discography_release_dict,
    _dedup_variant_releases,
    _sort_discography_releases,
)
from core.metadata.discography_providers import get_discography_provider_adapter
from core.metadata.discography_result import (
    DiscographyRequest,
    DiscographyStatus,
)
from core.metadata.lookup import MetadataLookupOptions
from utils.logging_config import get_logger

logger = get_logger("metadata.discography.strict")


def _get_strict_source_chain(options: MetadataLookupOptions) -> List[str]:
    """Return the provider chain for the user-facing strict discography path.

    An explicit source is authoritative and therefore exclusive. Automatic mode
    preserves the configured priority and may continue after confirmed EMPTY.
    """

    override = (options.source_override or "").strip().lower()
    if override:
        return [override]

    primary_source = metadata_registry.get_primary_source()
    source_chain = list(metadata_registry.get_source_priority(primary_source))
    if not options.allow_fallback:
        return source_chain[:1]
    return source_chain


def _lookup_artist_id(
    source: str,
    artist_id: str,
    source_artist_ids: Dict[str, str],
) -> str:
    source_artist_id = str(source_artist_ids.get(source) or "").strip()
    if source_artist_id:
        return source_artist_id
    if source_artist_ids:
        return ""
    return str(artist_id or "").strip()


def _error_response(
    *,
    source: str,
    source_priority: List[str],
    message: str,
    status_code: int,
) -> Dict[str, Any]:
    return {
        "success": False,
        "state": "error",
        "albums": [],
        "singles": [],
        "source": source,
        "source_priority": source_priority,
        "error": message,
        "status_code": status_code,
    }


def get_artist_discography(
    artist_id: str,
    artist_name: str = "",
    options: Optional[MetadataLookupOptions] = None,
) -> Dict[str, Any]:
    """Return an artist discography with results / empty / error.

    Automatic mode continues only after a confirmed EMPTY. An explicit source
    never crosses into another provider, regardless of ``allow_fallback``.
    """

    options = options or MetadataLookupOptions()
    source_priority = _get_strict_source_chain(options)
    source_artist_ids = options.artist_source_ids or {}
    releases: List[Any] = []
    active_source: Optional[str] = None

    for source in source_priority:
        client = metadata_registry.get_client_for_source(source)
        if not client:
            if options.source_override:
                return _error_response(
                    source=source,
                    source_priority=source_priority,
                    message=(
                        f"Could not access {source} while loading the artist "
                        "discography: provider is unavailable"
                    ),
                    status_code=503,
                )
            continue

        request = DiscographyRequest(
            artist_id=_lookup_artist_id(
                source,
                artist_id,
                source_artist_ids,
            ),
            artist_name=artist_name,
            limit=options.limit,
            skip_cache=options.skip_cache,
            max_pages=options.max_pages,
        )
        outcome = get_discography_provider_adapter(source, client).load(request)

        if outcome.status is DiscographyStatus.ACCESS_ERROR:
            logger.warning(
                "Discography access failed for %s and fallback was stopped: %s",
                source,
                outcome.message,
            )
            return _error_response(
                source=source,
                source_priority=source_priority,
                message=str(outcome.message),
                status_code=outcome.status_code,
            )

        if outcome.status is DiscographyStatus.RESULTS:
            releases = list(outcome.releases)
            active_source = source
            break

        # EMPTY is the only state that may advance in automatic mode. For an
        # explicit source the chain contains exactly one provider, so it ends.

    albums: List[Dict[str, Any]] = []
    singles: List[Dict[str, Any]] = []
    seen_ids = set()

    for release in releases:
        normalized = _build_discography_release_dict(
            release,
            artist_id,
            source=active_source,
        )
        if not normalized or normalized["id"] in seen_ids:
            continue
        seen_ids.add(normalized["id"])
        if (normalized.get("album_type") or "album").lower() in {"single", "ep"}:
            singles.append(normalized)
        else:
            albums.append(normalized)

    albums = _sort_discography_releases(albums)
    singles = _sort_discography_releases(singles)
    has_releases = bool(albums or singles)

    return {
        "success": has_releases,
        "state": "results" if has_releases else "empty",
        "albums": albums,
        "singles": singles,
        "source": active_source or (source_priority[0] if source_priority else "unknown"),
        "source_priority": source_priority,
        "error": (
            None
            if has_releases
            else f'No releases found for artist "{artist_name or artist_id}"'
        ),
        "status_code": 200 if has_releases else 404,
    }


def get_artist_detail_discography(
    artist_id: str,
    artist_name: str = "",
    options: Optional[MetadataLookupOptions] = None,
) -> Dict[str, Any]:
    """Build artist-detail cards while preserving the three-state contract."""

    source_discography = get_artist_discography(
        artist_id,
        artist_name=artist_name,
        options=options,
    )
    if source_discography.get("state") == "error":
        return {
            "success": False,
            "state": "error",
            "albums": [],
            "eps": [],
            "singles": [],
            "source": source_discography.get("source", "unknown"),
            "source_priority": source_discography.get("source_priority", []),
            "error": source_discography.get("error"),
            "status_code": source_discography.get("status_code", 502),
        }

    albums: List[Dict[str, Any]] = []
    eps: List[Dict[str, Any]] = []
    singles: List[Dict[str, Any]] = []
    seen_ids = set()

    for release in list(source_discography.get("albums", [])) + list(
        source_discography.get("singles", [])
    ):
        card = _build_artist_detail_release_card(release)
        if not card or card["id"] in seen_ids:
            continue
        seen_ids.add(card["id"])
        album_type = (card.get("album_type") or "album").lower()
        if album_type == "ep":
            eps.append(card)
        elif album_type == "single":
            singles.append(card)
        else:
            albums.append(card)

    if options is None or options.dedup_variants:
        albums = _dedup_variant_releases(albums)
        eps = _dedup_variant_releases(eps)
        singles = _dedup_variant_releases(singles)

    albums = _sort_discography_releases(albums)
    eps = _sort_discography_releases(eps)
    singles = _sort_discography_releases(singles)
    has_releases = bool(albums or eps or singles)

    return {
        "success": has_releases,
        "state": "results" if has_releases else "empty",
        "albums": albums,
        "eps": eps,
        "singles": singles,
        "source": source_discography.get("source", "unknown"),
        "source_priority": source_discography.get("source_priority", []),
        "error": None if has_releases else source_discography.get("error"),
        "status_code": 200 if has_releases else 404,
    }
