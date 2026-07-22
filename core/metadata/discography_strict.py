"""Three-state artist-discography orchestration.

A valid provider response with no releases is an empty catalogue and may advance
to the next configured source. A provider-access failure is a different state:
it stops fallback and is returned to the web layer as a visible error.

The strict path is intentionally limited to artist discography. Search, import
and enrichment keep their existing best-effort behaviour.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from core.metadata import registry as metadata_registry
from core.metadata.album_tracks import (
    _extract_lookup_value,
    _pick_best_artist_match,
)
from core.metadata.discography import (
    _build_artist_detail_release_card,
    _build_discography_release_dict,
    _dedup_variant_releases,
    _get_source_chain_for_lookup,
    _sort_discography_releases,
)
from core.metadata.lookup import MetadataLookupOptions
from core.metadata.provider_access import (
    ProviderAccessError,
    allow_provider_not_found,
    call_discography_provider,
)
from utils.logging_config import get_logger

logger = get_logger("metadata.discography.strict")


def _spotify_free_active(client: Any) -> bool:
    try:
        return bool(getattr(client, "_free_active", lambda: False)())
    except Exception:
        return False


def _search_spotify_free_artists(client: Any, artist_name: str, limit: int) -> List[Any]:
    """Search Spotify Free directly without enabling cross-provider fallback."""

    free_client = getattr(client, "_free_meta", None)
    if free_client is None or not hasattr(free_client, "search_artists"):
        return []
    return free_client.search_artists(artist_name, limit) or []


def _get_artist_albums_with_client(
    source: str,
    client: Any,
    artist_id: str,
    *,
    artist_name: str,
    limit: int,
    skip_cache: bool,
    max_pages: int,
) -> List[Any]:
    """Run the exact-ID/name-resolution flow on one isolated client.

    ``400``, ``404`` and ``410`` are treated as a valid empty result only while
    resolving an explicit artist ID. Search failures remain provider-access
    errors and stop the fallback chain.
    """

    if not client or not hasattr(client, "get_artist_albums"):
        return []

    spotify_free = source == "spotify" and _spotify_free_active(client)

    def fetch_for_artist(target_artist_id: str) -> List[Any]:
        kwargs: Dict[str, Any] = {
            "album_type": "album,single",
            "limit": limit,
        }
        if source in {"jiosaavn", "bandcamp"}:
            kwargs["artist_name"] = artist_name
        if source == "spotify":
            # Spotify Free is still the Spotify catalogue. Enabling fallback is
            # safe only for a real Spotify ID: the client's numeric-ID branch is
            # what delegates to iTunes/Deezer, so it remains disabled here.
            kwargs["allow_fallback"] = spotify_free and not target_artist_id.isdigit()
            kwargs["skip_cache"] = skip_cache
            kwargs["max_pages"] = max_pages

        with allow_provider_not_found(client):
            return client.get_artist_albums(target_artist_id, **kwargs) or []

    albums = fetch_for_artist(artist_id) if artist_id else []
    if albums or not artist_name:
        return albums

    if spotify_free:
        search_results = _search_spotify_free_artists(client, artist_name, 5)
    else:
        search_artists = getattr(client, "search_artists", None)
        if not callable(search_artists):
            return albums

        search_kwargs: Dict[str, Any] = {"limit": 5}
        if source == "spotify":
            search_kwargs["allow_fallback"] = False
        search_results = search_artists(artist_name, **search_kwargs) or []

    best = _pick_best_artist_match(search_results, artist_name)
    if not best:
        return albums

    found_artist_id = _extract_lookup_value(best, "id", "artist_id")
    if not found_artist_id:
        return albums

    resolved = fetch_for_artist(str(found_artist_id))
    if resolved:
        logger.debug(
            "Found %s artist '%s' (id=%s)",
            source,
            _extract_lookup_value(best, "name", "artist_name", "title"),
            found_artist_id,
        )
    return resolved


def get_artist_discography(
    artist_id: str,
    artist_name: str = "",
    options: Optional[MetadataLookupOptions] = None,
) -> Dict[str, Any]:
    """Return an artist discography with ``results`` / ``empty`` / ``error``.

    Fallback continues only after a provider completed successfully and returned
    no releases. Any outbound communication failure stops the chain immediately.
    """

    options = options or MetadataLookupOptions()
    source_priority = _get_source_chain_for_lookup(options)
    source_artist_ids = options.artist_source_ids or {}
    releases: List[Any] = []
    active_source: Optional[str] = None

    for source in source_priority:
        client = metadata_registry.get_client_for_source(source)
        if not client:
            continue

        source_artist_id = str(source_artist_ids.get(source) or "").strip()
        lookup_artist_id = (
            source_artist_id
            if source_artist_id
            else artist_id if not source_artist_ids else ""
        )

        try:
            load_discography = partial(
                _get_artist_albums_with_client,
                source,
                artist_id=lookup_artist_id,
                artist_name=artist_name,
                limit=options.limit,
                skip_cache=options.skip_cache,
                max_pages=options.max_pages,
            )
            releases = call_discography_provider(
                source,
                client,
                load_discography,
            ) or []
        except ProviderAccessError as exc:
            logger.warning(
                "Discography access failed for %s and fallback was stopped: %s",
                source,
                exc,
            )
            return {
                "success": False,
                "state": "error",
                "albums": [],
                "singles": [],
                "source": source,
                "source_priority": source_priority,
                "error": str(exc),
                "status_code": exc.status_code,
            }

        if releases:
            active_source = source
            break

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
