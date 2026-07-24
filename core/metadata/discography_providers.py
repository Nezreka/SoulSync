"""Provider adapters for strict artist-discography lookups.

Every adapter exposes the same provider-independent request/result contract. The
legacy metadata methods remain untouched for background and best-effort flows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from core.metadata.album_tracks import _extract_lookup_value, _pick_best_artist_match
from core.metadata.discography_result import DiscographyOutcome, DiscographyRequest
from core.metadata.provider_access import (
    ProviderAccessError,
    allow_provider_not_found,
    call_discography_provider,
)
from utils.logging_config import get_logger

logger = get_logger("metadata.discography.providers")


class DiscographyProviderAdapter(ABC):
    """Single responsibility: execute one provider's discography operation."""

    def __init__(self, source: str, client: Any):
        self.source = (source or "unknown").strip().lower() or "unknown"
        self.client = client

    @abstractmethod
    def load(self, request: DiscographyRequest) -> DiscographyOutcome:
        """Return RESULTS, EMPTY or ACCESS_ERROR for this provider only."""

    def _execute(self, callback) -> DiscographyOutcome:
        try:
            releases = call_discography_provider(
                self.source,
                self.client,
                callback,
            ) or []
        except ProviderAccessError as exc:
            return DiscographyOutcome.access_error(
                self.source,
                str(exc),
                operation=exc.operation,
                status_code=exc.status_code,
            )

        if releases:
            return DiscographyOutcome.results(self.source, releases)
        return DiscographyOutcome.empty(self.source)


class StandardDiscographyProviderAdapter(DiscographyProviderAdapter):
    """Adapter for providers exposing get_artist_albums/search_artists."""

    def _spotify_free_active(self, client: Any) -> bool:
        if self.source != "spotify":
            return False
        try:
            return bool(getattr(client, "_free_active", lambda: False)())
        except Exception:
            return False

    def _search_spotify_free_artists(
        self,
        client: Any,
        artist_name: str,
        limit: int,
    ) -> List[Any]:
        free_client = getattr(client, "_free_meta", None)
        if free_client is None or not hasattr(free_client, "search_artists"):
            return []
        return free_client.search_artists(artist_name, limit) or []

    def _load_with_client(
        self,
        client: Any,
        request: DiscographyRequest,
    ) -> List[Any]:
        if not hasattr(client, "get_artist_albums"):
            return []

        spotify_free = self._spotify_free_active(client)

        def fetch_for_artist(target_artist_id: str) -> List[Any]:
            kwargs: Dict[str, Any] = {
                "album_type": "album,single",
                "limit": request.limit,
            }
            if self.source in {"jiosaavn", "bandcamp"}:
                kwargs["artist_name"] = request.artist_name
            if self.source == "spotify":
                # Spotify Free is still Spotify. Cross-provider fallbacks inside
                # SpotifyClient remain disabled for the strict provider path.
                kwargs["allow_fallback"] = spotify_free and not target_artist_id.isdigit()
                kwargs["skip_cache"] = request.skip_cache
                kwargs["max_pages"] = request.max_pages

            with allow_provider_not_found(client):
                return client.get_artist_albums(target_artist_id, **kwargs) or []

        releases = fetch_for_artist(request.artist_id) if request.artist_id else []
        if releases or not request.artist_name:
            return releases

        if spotify_free:
            search_results = self._search_spotify_free_artists(
                client,
                request.artist_name,
                5,
            )
        else:
            search_artists = getattr(client, "search_artists", None)
            if not callable(search_artists):
                return releases

            search_kwargs: Dict[str, Any] = {"limit": 5}
            if self.source == "spotify":
                search_kwargs["allow_fallback"] = False
            search_results = search_artists(
                request.artist_name,
                **search_kwargs,
            ) or []

        best = _pick_best_artist_match(search_results, request.artist_name)
        if not best:
            return releases

        found_artist_id = _extract_lookup_value(best, "id", "artist_id")
        if not found_artist_id:
            return releases

        resolved = fetch_for_artist(str(found_artist_id))
        if resolved:
            logger.debug(
                "Found %s artist '%s' (id=%s)",
                self.source,
                _extract_lookup_value(best, "name", "artist_name", "title"),
                found_artist_id,
            )
        return resolved

    def load(self, request: DiscographyRequest) -> DiscographyOutcome:
        return self._execute(
            lambda isolated: self._load_with_client(isolated, request)
        )


class MusicBrainzDiscographyProviderAdapter(DiscographyProviderAdapter):
    """MusicBrainz implementation of the same discography contract."""

    def _load_with_client(
        self,
        client: Any,
        request: DiscographyRequest,
    ) -> List[Any]:
        # MusicBrainzSearchClient currently exposes its artist-discography flow
        # through search_albums. The strict adapter owns only the operation
        # mapping; the orchestrator remains provider-agnostic.
        if request.artist_name and hasattr(client, "search_albums"):
            return client.search_albums(
                request.artist_name,
                limit=request.limit,
            ) or []

        if request.artist_id:
            nested = getattr(client, "_client", None)
            browse = getattr(nested, "browse_artist_release_groups", None)
            convert = getattr(client, "_release_group_to_album", None)
            if callable(browse) and callable(convert):
                release_groups = browse(
                    request.artist_id,
                    release_types=["album", "ep", "single", "other"],
                    limit=request.limit,
                ) or []
                return [
                    convert(group, request.artist_name)
                    for group in release_groups
                    if group
                ]

        return []

    def load(self, request: DiscographyRequest) -> DiscographyOutcome:
        return self._execute(
            lambda isolated: self._load_with_client(isolated, request)
        )


_ADAPTERS = {
    "musicbrainz": MusicBrainzDiscographyProviderAdapter,
}


def get_discography_provider_adapter(
    source: str,
    client: Any,
) -> DiscographyProviderAdapter:
    """Return the adapter implementing the common contract for ``source``."""

    adapter_type = _ADAPTERS.get(
        (source or "").strip().lower(),
        StandardDiscographyProviderAdapter,
    )
    return adapter_type(source, client)


__all__ = [
    "DiscographyProviderAdapter",
    "MusicBrainzDiscographyProviderAdapter",
    "StandardDiscographyProviderAdapter",
    "get_discography_provider_adapter",
]
