"""Typed metadata engine and compatibility facades."""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, Sequence

from core.metadata.cache import get_metadata_cache
from core.metadata.constants import METADATA_PROVIDER_SOURCES, METADATA_SOURCE_PRIORITY
from core.metadata.contracts import (
    MetadataEntityKind,
    MetadataLookupOutcome,
    MetadataLookupRequest,
    MetadataProviderStatus,
    MetadataSearchOutcome,
    MetadataSearchRequest,
)
from core.metadata.exceptions import MetadataProviderError, MetadataRateLimited
from core.metadata.models import MetadataRecord, as_metadata_record
from core.metadata.normalize import (
    normalize_deezer_album,
    normalize_deezer_artist,
    normalize_deezer_track,
    normalize_discogs_album,
    normalize_discogs_artist,
    normalize_discogs_track,
    normalize_itunes_album,
    normalize_itunes_artist,
    normalize_itunes_track,
    normalize_spotify_album,
    normalize_spotify_artist,
    normalize_spotify_track,
)
from core.metadata.providers import (
    DeezerMetadataAdapter,
    DiscogsMetadataAdapter,
    ITunesMetadataAdapter,
    SpotifyMetadataAdapter,
)
from utils.logging_config import get_logger

logger = get_logger("metadata.engine")


def _normalize_source(source: Optional[str]) -> str:
    return (source or "").strip().lower()


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen = set()
    ordered: list[str] = []
    for value in values:
        normalized = _normalize_source(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def _normalize_enabled_sources(value: Any) -> tuple[str, ...]:
    if value is None:
        return METADATA_PROVIDER_SOURCES
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return _dedupe(parts)
    try:
        return _dedupe(list(value))
    except TypeError:
        return METADATA_PROVIDER_SOURCES


def _read_global_enabled_sources() -> tuple[str, ...]:
    try:
        from config.settings import config_manager

        configured = config_manager.get("metadata.enabled_sources", None)
    except Exception:
        configured = None
    enabled = _normalize_enabled_sources(configured)
    return enabled or METADATA_PROVIDER_SOURCES


def _provider_source_chain(preferred_source: Optional[str]) -> tuple[str, ...]:
    primary = _normalize_source(preferred_source)
    ordered = []
    if primary in METADATA_SOURCE_PRIORITY:
        ordered.append(primary)
    for source in METADATA_SOURCE_PRIORITY:
        if source not in ordered:
            ordered.append(source)
    return tuple(source for source in ordered if source in METADATA_PROVIDER_SOURCES)


def _record_from_mapping(raw: Any, **extra: Any) -> MetadataRecord:
    if isinstance(raw, MetadataRecord):
        payload = raw.copy()
    elif isinstance(raw, dict):
        payload = MetadataRecord(raw)
    else:
        payload = MetadataRecord({})
    payload.update(extra)
    return payload


def _record_identity(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        source_id = value.get("source_id") or value.get("id") or value.get("artist_id") or value.get("album_id")
        return str(source_id or "")
    source_id = getattr(value, "source_id", None) or getattr(value, "id", None)
    return str(source_id or "")


def _extract_image_url(raw: Any) -> Optional[str]:
    if not isinstance(raw, dict):
        return None
    images = raw.get("images") or []
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("uri")
    return (
        raw.get("image_url")
        or raw.get("thumb_url")
        or raw.get("cover_image")
        or raw.get("picture_xl")
        or raw.get("picture_big")
        or raw.get("picture_medium")
        or raw.get("artworkUrl600")
        or raw.get("artworkUrl100")
    )


def _normalize_artist_artists(raw: Any) -> list[dict[str, Any]]:
    artists: list[dict[str, Any]] = []
    if not raw:
        return artists

    if isinstance(raw, dict):
        raw = [raw]
    elif isinstance(raw, (str, bytes)):
        raw = [raw]

    try:
        iterable = list(raw)
    except TypeError:
        iterable = [raw]

    for item in iterable:
        if isinstance(item, dict):
            entry: dict[str, Any] = {}
            name = item.get("name") or item.get("artist_name") or item.get("title")
            if name:
                entry["name"] = str(name)
            item_id = item.get("source_id") or item.get("id") or item.get("artist_id")
            if item_id:
                entry["id"] = str(item_id)
            if item.get("genres"):
                entry["genres"] = item.get("genres")
            if entry:
                artists.append(entry)
            continue

        name = str(item).strip()
        if name:
            artists.append({"name": name})
    return artists


def _artist_payload(source: str, raw: Any) -> MetadataRecord:
    model = {
        "spotify": normalize_spotify_artist,
        "deezer": normalize_deezer_artist,
        "itunes": normalize_itunes_artist,
        "discogs": normalize_discogs_artist,
    }[source](raw or {})

    payload = _record_from_mapping(raw or {})
    payload.update(model.to_dict())
    payload["source_id"] = _record_identity(model)
    payload["id"] = payload["source_id"]
    payload["name"] = payload.get("name") or model.name
    payload["image_url"] = payload.get("image_url") or model.image_url
    payload["images"] = payload.get("images") or ([{"url": model.image_url}] if model.image_url else [])
    payload["genres"] = payload.get("genres") or list(model.genres)
    payload["popularity"] = payload.get("popularity", model.popularity)
    payload["followers"] = model.followers
    payload["external_urls"] = payload.get("external_urls") or dict(model.external_urls or {})
    payload["source"] = source
    payload["raw_data"] = raw
    return payload


def _album_payload(source: str, raw: Any) -> MetadataRecord:
    model = {
        "spotify": normalize_spotify_album,
        "deezer": normalize_deezer_album,
        "itunes": normalize_itunes_album,
        "discogs": normalize_discogs_album,
    }[source](raw or {})

    payload = _record_from_mapping(raw or {})
    payload.update(model.to_dict())
    payload["source_id"] = _record_identity(model)
    payload["id"] = payload["source_id"]
    payload["name"] = payload.get("name") or model.name
    payload["image_url"] = payload.get("image_url") or model.image_url
    payload["images"] = payload.get("images") or ([{"url": model.image_url}] if model.image_url else [])

    raw_artists = payload.get("artists")
    if not raw_artists:
        payload["artists"] = _normalize_artist_artists(raw.get("artists") if isinstance(raw, dict) else None)
    elif isinstance(raw_artists, list) and raw_artists and isinstance(raw_artists[0], str):
        payload["artists"] = _normalize_artist_artists(raw_artists)

    if not payload.get("artists") and model.artists:
        payload["artists"] = _normalize_artist_artists(model.artists)

    payload["artist_name"] = payload.get("artist_name") or (
        payload["artists"][0]["name"] if payload.get("artists") else None
    )
    payload["release_date"] = payload.get("release_date") or model.release_date
    payload["total_tracks"] = payload.get("total_tracks") or model.total_tracks
    payload["album_type"] = payload.get("album_type") or model.album_type
    payload["external_urls"] = payload.get("external_urls") or dict(model.external_urls or {})
    payload["source"] = source
    payload["raw_data"] = raw
    return payload


def _track_payload(source: str, raw: Any) -> MetadataRecord:
    raw_dict = raw if isinstance(raw, dict) else {}
    model = {
        "spotify": normalize_spotify_track,
        "deezer": normalize_deezer_track,
        "itunes": normalize_itunes_track,
        "discogs": normalize_discogs_track,
    }[source](raw_dict or {})

    payload = _record_from_mapping(raw_dict or {})
    payload.update(model.to_dict())
    payload["source_id"] = _record_identity(model)
    payload["id"] = payload["source_id"]
    payload["name"] = payload.get("name") or model.name
    payload["artists"] = payload.get("artists") or _normalize_artist_artists(raw_dict.get("artists"))
    if not payload["artists"] and model.artists:
        payload["artists"] = _normalize_artist_artists(model.artists)
    payload["album_name"] = payload.get("album_name") or model.album

    album_raw = raw_dict.get("album")
    if isinstance(album_raw, dict):
        album_payload = _album_payload(source, album_raw)
        payload["album"] = album_payload
    else:
        payload["album"] = _record_from_mapping(
            {
                "id": raw_dict.get("album_id") or raw_dict.get("collectionId") or raw_dict.get("release_id") or "",
                "name": model.album,
                "artists": payload["artists"],
                "image_url": model.image_url,
                "release_date": model.release_date,
                "album_type": model.album_type,
                "total_tracks": model.total_tracks,
            }
        )
    payload["duration_ms"] = payload.get("duration_ms") or model.duration_ms
    payload["popularity"] = payload.get("popularity") or model.popularity
    payload["preview_url"] = payload.get("preview_url") or model.preview_url
    payload["external_urls"] = payload.get("external_urls") or dict(model.external_urls or {})
    payload["image_url"] = payload.get("image_url") or model.image_url
    payload["release_date"] = payload.get("release_date") or model.release_date
    payload["track_number"] = payload.get("track_number") or model.track_number
    payload["disc_number"] = payload.get("disc_number") or model.disc_number
    payload["album_type"] = payload.get("album_type") or model.album_type
    payload["total_tracks"] = payload.get("total_tracks") or model.total_tracks
    payload["source"] = source
    if raw_dict.get("external_ids"):
        payload["external_ids"] = raw_dict.get("external_ids")
    if raw_dict.get("uri"):
        payload["uri"] = raw_dict.get("uri")
    payload["raw_data"] = raw
    return payload


class MetadataSourceFacade:
    """Compatibility facade for a single provider source."""

    def __init__(self, engine: "MetadataEngine", source: str) -> None:
        self._engine = engine
        self.source = _normalize_source(source)
        self.provider_name = self.source

    def reload_config(self) -> None:
        self._engine.reload_config()

    def is_authenticated(self) -> bool:
        status = self._engine.get_provider_status(self.source)
        return bool(status.authenticated)

    def is_spotify_authenticated(self) -> bool:
        return self.is_authenticated()

    def is_connected(self) -> bool:
        return self.is_authenticated()

    def get_status(self) -> MetadataProviderStatus:
        return self._engine.get_provider_status(self.source)

    def search_tracks(self, query: str, limit: int = 20, **kwargs) -> list[MetadataRecord]:
        return self._engine.search_tracks(
            query,
            limit=limit,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
            max_pages=int(kwargs.get("max_pages", 0) or 0),
        ).items

    def search_artists(self, query: str, limit: int = 20, **kwargs) -> list[MetadataRecord]:
        return self._engine.search_artists(
            query,
            limit=limit,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
            max_pages=int(kwargs.get("max_pages", 0) or 0),
        ).items

    def search_albums(self, query: str, limit: int = 20, **kwargs) -> list[MetadataRecord]:
        return self._engine.search_albums(
            query,
            limit=limit,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
            max_pages=int(kwargs.get("max_pages", 0) or 0),
        ).items

    def get_track_details(self, track_id: str, **kwargs) -> Optional[MetadataRecord]:
        return self._engine.get_track_details(
            track_id,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
        ).value

    def get_track_features(self, track_id: str, **kwargs) -> Optional[dict[str, Any]]:
        return self._engine.get_track_features(
            track_id,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
        ).value

    def get_album(self, album_id: str, **kwargs) -> Optional[MetadataRecord]:
        include_tracks = kwargs.get("include_tracks", True)
        return self._engine.get_album(
            album_id,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
            include_tracks=bool(include_tracks),
            max_pages=int(kwargs.get("max_pages", 0) or 0),
        ).value

    def get_album_tracks(self, album_id: str, **kwargs) -> Optional[dict[str, Any]]:
        return self._engine.get_album_tracks(
            album_id,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
            max_pages=int(kwargs.get("max_pages", 0) or 0),
            limit=int(kwargs.get("limit", 50) or 50),
        ).value

    def get_album_tracks_dict(self, album_id: str, **kwargs) -> Optional[dict[str, Any]]:
        return self.get_album_tracks(album_id, **kwargs)

    def get_artist(self, artist_id: str, **kwargs) -> Optional[MetadataRecord]:
        return self._engine.get_artist(
            artist_id,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
        ).value

    def get_artist_albums(
        self,
        artist_id: str,
        album_type: str = "album,single",
        limit: int = 50,
        **kwargs,
    ) -> list[MetadataRecord]:
        return self._engine.get_artist_albums(
            artist_id,
            album_type=album_type,
            limit=limit,
            source_override=self.source,
            allow_fallback=bool(kwargs.get("allow_fallback", False)),
            skip_cache=bool(kwargs.get("skip_cache", False)),
            max_pages=int(kwargs.get("max_pages", 0) or 0),
        ).items

    def _get_artist_image_from_albums(self, artist_id: str) -> Optional[str]:
        outcome = self._engine.get_artist_albums(
            artist_id,
            limit=5,
            source_override=self.source,
            allow_fallback=False,
        )
        for album in outcome.items:
            image_url = album.get("image_url")
            if image_url:
                return image_url
        return None


class MetadataEngine:
    """Typed metadata engine for provider orchestration and normalization."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, Any] = {}
        self._facades: dict[str, MetadataSourceFacade] = {}

    def reload_config(self) -> None:
        with self._lock:
            for adapter in self._adapters.values():
                try:
                    adapter.reload_config()
                except Exception as exc:
                    logger.debug("Could not reload metadata adapter config: %s", exc)

    def _get_adapter(self, source: str):
        source = _normalize_source(source)
        if source not in METADATA_PROVIDER_SOURCES:
            return None

        with self._lock:
            adapter = self._adapters.get(source)
            if adapter is not None:
                return adapter

            if source == "spotify":
                adapter = SpotifyMetadataAdapter()
            elif source == "deezer":
                adapter = DeezerMetadataAdapter()
            elif source == "itunes":
                adapter = ITunesMetadataAdapter()
            elif source == "discogs":
                adapter = DiscogsMetadataAdapter()
            else:  # pragma: no cover - defensive fallback
                return None

            self._adapters[source] = adapter
            return adapter

    def get_provider_status(self, source: str) -> MetadataProviderStatus:
        adapter = self._get_adapter(source)
        if adapter is None:
            return MetadataProviderStatus(
                provider=_normalize_source(source),
                configured=False,
                available=False,
            )

        try:
            return adapter.get_status()
        except Exception as exc:
            logger.debug("Could not query status for %s: %s", source, exc)
            return MetadataProviderStatus(
                provider=_normalize_source(source),
                configured=False,
                available=False,
                last_error=str(exc),
            )

    def get_source_facade(self, source: str) -> Optional[MetadataSourceFacade]:
        source = _normalize_source(source)
        if source not in METADATA_PROVIDER_SOURCES and source != "hydrabase":
            return None

        with self._lock:
            facade = self._facades.get(source)
            if facade is not None:
                return facade
            if source not in METADATA_PROVIDER_SOURCES:
                return None
            facade = MetadataSourceFacade(self, source)
            self._facades[source] = facade
            return facade

    def _resolve_source_chain(
        self,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
    ) -> tuple[str, ...]:
        requested = _normalize_source(source_override)
        global_enabled = _read_global_enabled_sources()
        request_enabled = _normalize_enabled_sources(enabled_sources) if enabled_sources is not None else None
        if request_enabled is not None and not request_enabled:
            return ()

        chain = list(_provider_source_chain(requested or None))
        if requested and requested not in chain:
            chain.insert(0, requested)

        if request_enabled is not None:
            allowed = set(request_enabled)
            chain = [source for source in chain if source in allowed]

        allowed_global = set(global_enabled)
        chain = [source for source in chain if source in allowed_global]

        if requested and requested in METADATA_PROVIDER_SOURCES and requested not in chain:
            if requested in allowed_global and (request_enabled is None or requested in set(request_enabled)):
                chain.insert(0, requested)

        if not allow_fallback:
            chain = chain[:1]

        return _dedupe(chain)

    def _entity_kind_to_methods(
        self,
        source: str,
        entity_kind: MetadataEntityKind,
    ) -> tuple[Callable[..., Any], Callable[[Any], MetadataRecord]]:
        adapter = self._get_adapter(source)
        if adapter is None:
            raise MetadataProviderError(source, entity_kind, f"Unknown provider: {source}")

        if entity_kind == "artist":
            return adapter.get_artist_raw, lambda raw: _artist_payload(source, raw)
        if entity_kind == "album":
            return adapter.get_album_raw, lambda raw: _album_payload(source, raw)
        if entity_kind == "track":
            return adapter.get_track_raw, lambda raw: _track_payload(source, raw)
        raise MetadataProviderError(source, entity_kind, f"Unsupported entity kind: {entity_kind}")

    def _search_source(
        self,
        source: str,
        entity_kind: MetadataEntityKind,
        query: str,
        limit: int,
    ) -> list[MetadataRecord]:
        adapter = self._get_adapter(source)
        if adapter is None:
            return []

        if entity_kind == "artist":
            raw_results = adapter.search_artists_raw(query, limit=limit)
            normalizer = lambda raw: _artist_payload(source, raw)
        elif entity_kind == "album":
            raw_results = adapter.search_albums_raw(query, limit=limit)
            normalizer = lambda raw: _album_payload(source, raw)
        elif entity_kind == "track":
            raw_results = adapter.search_tracks_raw(query, limit=limit)
            normalizer = lambda raw: _track_payload(source, raw)
        else:
            return []

        if not raw_results:
            return []

        normalized = [normalizer(raw) for raw in raw_results if raw]
        if normalized:
            try:
                entity_rows = [
                    (source_id, item.get("raw_data") or item)
                    for item in normalized
                    if (source_id := _record_identity(item))
                ]
                get_metadata_cache().store_entities_bulk(
                    source,
                    entity_kind,
                    entity_rows,
                    skip_if_exists=True,
                )
            except Exception as exc:
                logger.debug("Could not cache %s search results from %s: %s", entity_kind, source, exc)
        return normalized

    def search_artists(
        self,
        query: str,
        *,
        limit: int = 20,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
        max_pages: int = 0,
    ) -> MetadataSearchOutcome[MetadataRecord]:
        request = MetadataSearchRequest(
            entity_kind="artist",
            query=query,
            limit=limit,
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
            skip_cache=skip_cache,
            max_pages=max_pages,
        )
        return self._search(request)

    def search_albums(
        self,
        query: str,
        *,
        limit: int = 20,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
        max_pages: int = 0,
    ) -> MetadataSearchOutcome[MetadataRecord]:
        request = MetadataSearchRequest(
            entity_kind="album",
            query=query,
            limit=limit,
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
            skip_cache=skip_cache,
            max_pages=max_pages,
        )
        return self._search(request)

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 20,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
        max_pages: int = 0,
    ) -> MetadataSearchOutcome[MetadataRecord]:
        request = MetadataSearchRequest(
            entity_kind="track",
            query=query,
            limit=limit,
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
            skip_cache=skip_cache,
            max_pages=max_pages,
        )
        return self._search(request)

    def _search(self, request: MetadataSearchRequest) -> MetadataSearchOutcome[MetadataRecord]:
        chain = self._resolve_source_chain(
            source_override=request.source_override,
            enabled_sources=request.enabled_sources,
            allow_fallback=request.allow_fallback,
        )
        errors: list[str] = []
        attempted: list[str] = []
        for source in chain:
            attempted.append(source)
            try:
                results = self._search_source(source, request.entity_kind, request.query, request.limit)
            except MetadataRateLimited as exc:
                errors.append(f"{source}: rate limited")
                logger.debug("Search rate limited on %s for %s: %s", source, request.query, exc)
                continue
            except MetadataProviderError as exc:
                errors.append(f"{source}: {exc}")
                logger.debug("Search failed on %s for %s: %s", source, request.query, exc)
                continue
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{source}: {exc}")
                logger.debug("Search failed on %s for %s: %s", source, request.query, exc)
                continue

            if results:
                return MetadataSearchOutcome(
                    items=results[: request.limit],
                    source=source,
                    attempted_sources=tuple(attempted),
                    cache_hit=False,
                    status="ok",
                    errors=tuple(errors),
                    raw_payload=results,
                )

        return MetadataSearchOutcome(
            items=[],
            source=None,
            attempted_sources=tuple(attempted),
            cache_hit=False,
            status="miss" if not errors else "error",
            errors=tuple(errors),
            raw_payload=None,
        )

    def get_artist(
        self,
        artist_id: str,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
    ) -> MetadataLookupOutcome[MetadataRecord]:
        return self._lookup("artist", artist_id, source_override=source_override, enabled_sources=enabled_sources, allow_fallback=allow_fallback, skip_cache=skip_cache)

    def get_album(
        self,
        album_id: str,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
        include_tracks: bool = True,
        max_pages: int = 0,
    ) -> MetadataLookupOutcome[MetadataRecord]:
        outcome = self._lookup(
            "album",
            album_id,
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
            skip_cache=skip_cache,
        )
        if outcome.value is None:
            return outcome
        if not include_tracks:
            return outcome

        album = _record_from_mapping(outcome.value)
        tracks = None
        try:
            tracks = self.get_album_tracks(
                album_id,
                source_override=outcome.source or source_override,
                enabled_sources=enabled_sources,
                allow_fallback=False,
                skip_cache=skip_cache,
                max_pages=max_pages,
            ).value
        except Exception as exc:
            logger.debug("Could not fetch embedded tracks for album %s: %s", album_id, exc)
        if tracks and isinstance(tracks, dict):
            album["tracks"] = tracks
        return MetadataLookupOutcome(
            value=album,
            source=outcome.source,
            attempted_sources=outcome.attempted_sources,
            cache_hit=outcome.cache_hit,
            status=outcome.status,
            errors=outcome.errors,
            raw_payload=outcome.raw_payload,
        )

    def get_track_details(
        self,
        track_id: str,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
    ) -> MetadataLookupOutcome[MetadataRecord]:
        return self._lookup("track", track_id, source_override=source_override, enabled_sources=enabled_sources, allow_fallback=allow_fallback, skip_cache=skip_cache)

    def get_track_features(
        self,
        track_id: str,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
    ) -> MetadataLookupOutcome[dict[str, Any]]:
        chain = self._resolve_source_chain(
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
        )
        attempted: list[str] = []
        errors: list[str] = []
        for source in chain:
            if source != "spotify":
                continue
            attempted.append(source)
            adapter = self._get_adapter(source)
            if adapter is None or not hasattr(adapter, "get_track_features_raw"):
                continue
            try:
                raw = adapter.get_track_features_raw(track_id)
                if raw:
                    return MetadataLookupOutcome(
                        value=raw,
                        source=source,
                        attempted_sources=tuple(attempted),
                        cache_hit=False,
                        status="ok",
                        errors=tuple(errors),
                        raw_payload=raw,
                    )
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                logger.debug("Track features lookup failed on %s for %s: %s", source, track_id, exc)
        return MetadataLookupOutcome(
            value=None,
            source=None,
            attempted_sources=tuple(attempted),
            cache_hit=False,
            status="miss" if not errors else "error",
            errors=tuple(errors),
            raw_payload=None,
        )

    def get_album_tracks(
        self,
        album_id: str,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
        max_pages: int = 0,
        limit: int = 50,
    ) -> MetadataLookupOutcome[dict[str, Any]]:
        chain = self._resolve_source_chain(
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
        )
        attempted: list[str] = []
        errors: list[str] = []
        cache = get_metadata_cache()

        for source in chain:
            attempted.append(source)
            adapter = self._get_adapter(source)
            if adapter is None:
                continue

            album_raw = None
            tracks_raw = None
            cache_hit = False

            if not skip_cache:
                try:
                    album_raw = cache.get_entity(source, "album", album_id)
                    cache_hit = album_raw is not None
                except Exception as exc:
                    logger.debug("Album cache lookup failed for %s/%s: %s", source, album_id, exc)

            if album_raw is None:
                try:
                    album_raw = adapter.get_album_raw(album_id)
                except MetadataRateLimited as exc:
                    errors.append(f"{source}: rate limited")
                    logger.debug("Album lookup rate limited on %s for %s: %s", source, album_id, exc)
                    continue
                except MetadataProviderError as exc:
                    errors.append(f"{source}: {exc}")
                    logger.debug("Album lookup failed on %s for %s: %s", source, album_id, exc)
                    continue
                except Exception as exc:
                    errors.append(f"{source}: {exc}")
                    logger.debug("Album lookup failed on %s for %s: %s", source, album_id, exc)
                    continue

                if album_raw:
                    try:
                        cache.store_entity(source, "album", album_id, album_raw)
                    except Exception as exc:
                        logger.debug("Could not cache album %s from %s: %s", album_id, source, exc)

            if album_raw is None:
                continue

            album_payload = _album_payload(source, album_raw)
            embedded_tracks = album_raw.get("tracks") if isinstance(album_raw, dict) else None
            if isinstance(embedded_tracks, dict):
                items = embedded_tracks.get("items") or embedded_tracks.get("data") or []
            elif isinstance(embedded_tracks, list):
                items = embedded_tracks
            else:
                items = []

            if not items:
                try:
                    tracks_raw = adapter.get_album_tracks_raw(album_id, limit=limit, max_pages=max_pages)
                except MetadataRateLimited as exc:
                    errors.append(f"{source}: rate limited")
                    logger.debug("Tracklist lookup rate limited on %s for %s: %s", source, album_id, exc)
                    continue
                except MetadataProviderError as exc:
                    errors.append(f"{source}: {exc}")
                    logger.debug("Tracklist lookup failed on %s for %s: %s", source, album_id, exc)
                    continue
                except Exception as exc:
                    errors.append(f"{source}: {exc}")
                    logger.debug("Tracklist lookup failed on %s for %s: %s", source, album_id, exc)
                    continue
                if tracks_raw:
                    items = tracks_raw.get("items") or tracks_raw.get("data") or []
                    album_from_tracks = tracks_raw.get("album") if isinstance(tracks_raw, dict) else None
                    if album_from_tracks and not album_payload.get("image_url"):
                        album_payload = _album_payload(source, album_from_tracks)

            normalized_tracks = [_track_payload(source, item) for item in items if item]
            if not normalized_tracks:
                continue

            tracks_payload = {
                "items": normalized_tracks,
                "tracks": normalized_tracks,
                "album": album_payload,
                "total": len(normalized_tracks),
                "limit": len(normalized_tracks),
                "next": None,
                "source": source,
                "success": True,
            }
            album_payload["tracks"] = tracks_payload
            return MetadataLookupOutcome(
                value=tracks_payload,
                source=source,
                attempted_sources=tuple(attempted),
                cache_hit=cache_hit,
                status="ok",
                errors=tuple(errors),
                raw_payload=tracks_raw or album_raw,
            )

        return MetadataLookupOutcome(
            value=None,
            source=None,
            attempted_sources=tuple(attempted),
            cache_hit=False,
            status="miss" if not errors else "error",
            errors=tuple(errors),
            raw_payload=None,
        )

    def get_artist_albums(
        self,
        artist_id: str,
        *,
        album_type: str = "album,single",
        limit: int = 50,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
        max_pages: int = 0,
    ) -> MetadataSearchOutcome[MetadataRecord]:
        chain = self._resolve_source_chain(
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
        )
        attempted: list[str] = []
        errors: list[str] = []
        for source in chain:
            attempted.append(source)
            adapter = self._get_adapter(source)
            if adapter is None or not hasattr(adapter, "get_artist_albums_raw"):
                continue
            try:
                raw_albums = adapter.get_artist_albums_raw(
                    artist_id,
                    album_type=album_type,
                    limit=limit,
                    max_pages=max_pages,
                )
            except MetadataRateLimited as exc:
                errors.append(f"{source}: rate limited")
                logger.debug("Artist albums lookup rate limited on %s for %s: %s", source, artist_id, exc)
                continue
            except MetadataProviderError as exc:
                errors.append(f"{source}: {exc}")
                logger.debug("Artist albums lookup failed on %s for %s: %s", source, artist_id, exc)
                continue
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                logger.debug("Artist albums lookup failed on %s for %s: %s", source, artist_id, exc)
                continue

            if not raw_albums:
                continue

            normalized = [_album_payload(source, raw) for raw in raw_albums if raw]
            if normalized:
                try:
                    entity_rows = [
                        (source_id, item.get("raw_data") or item)
                        for item in normalized
                        if (source_id := _record_identity(item))
                    ]
                    get_metadata_cache().store_entities_bulk(
                        source,
                        "album",
                        entity_rows,
                        skip_if_exists=True,
                    )
                except Exception as exc:
                    logger.debug("Could not cache artist albums from %s: %s", source, exc)
                return MetadataSearchOutcome(
                    items=normalized[:limit],
                    source=source,
                    attempted_sources=tuple(attempted),
                    cache_hit=False,
                    status="ok",
                    errors=tuple(errors),
                    raw_payload=raw_albums,
                )

        return MetadataSearchOutcome(
            items=[],
            source=None,
            attempted_sources=tuple(attempted),
            cache_hit=False,
            status="miss" if not errors else "error",
            errors=tuple(errors),
            raw_payload=None,
        )

    def _lookup(
        self,
        entity_kind: MetadataEntityKind,
        entity_id: str,
        *,
        source_override: Optional[str] = None,
        enabled_sources: Optional[Sequence[str]] = None,
        allow_fallback: bool = True,
        skip_cache: bool = False,
    ) -> MetadataLookupOutcome[MetadataRecord]:
        chain = self._resolve_source_chain(
            source_override=source_override,
            enabled_sources=enabled_sources,
            allow_fallback=allow_fallback,
        )
        attempted: list[str] = []
        errors: list[str] = []
        cache = get_metadata_cache()

        for source in chain:
            attempted.append(source)
            try:
                raw_cache = None
                cache_hit = False
                if not skip_cache and entity_kind in {"artist", "album", "track"}:
                    try:
                        raw_cache = cache.get_entity(source, entity_kind, entity_id)
                        cache_hit = raw_cache is not None
                    except Exception as exc:
                        logger.debug("Cache lookup failed for %s/%s/%s: %s", source, entity_kind, entity_id, exc)

                if raw_cache is not None:
                    payload = self._lookup_payload_for_source(source, entity_kind, raw_cache)
                    return MetadataLookupOutcome(
                        value=payload,
                        source=source,
                        attempted_sources=tuple(attempted),
                        cache_hit=cache_hit,
                        status="ok",
                        errors=tuple(errors),
                        raw_payload=raw_cache,
                    )

                get_raw, payload_builder = self._entity_kind_to_methods(source, entity_kind)
                raw = get_raw(entity_id)
                if not raw:
                    continue

                payload = payload_builder(raw)
                try:
                    cache.store_entity(source, entity_kind, _record_identity(payload) or entity_id, raw)
                except Exception as exc:
                    logger.debug("Could not cache %s/%s/%s: %s", source, entity_kind, entity_id, exc)

                return MetadataLookupOutcome(
                    value=payload,
                    source=source,
                    attempted_sources=tuple(attempted),
                    cache_hit=False,
                    status="ok",
                    errors=tuple(errors),
                    raw_payload=raw,
                )
            except MetadataRateLimited as exc:
                errors.append(f"{source}: rate limited")
                logger.debug("%s lookup rate limited on %s for %s: %s", entity_kind, source, entity_id, exc)
                continue
            except MetadataProviderError as exc:
                errors.append(f"{source}: {exc}")
                logger.debug("%s lookup failed on %s for %s: %s", entity_kind, source, entity_id, exc)
                continue
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                logger.debug("%s lookup failed on %s for %s: %s", entity_kind, source, entity_id, exc)
                continue

        return MetadataLookupOutcome(
            value=None,
            source=None,
            attempted_sources=tuple(attempted),
            cache_hit=False,
            status="miss" if not errors else "error",
            errors=tuple(errors),
            raw_payload=None,
        )

    def _lookup_payload_for_source(self, source: str, entity_kind: MetadataEntityKind, raw: Any) -> MetadataRecord:
        if entity_kind == "artist":
            return _artist_payload(source, raw)
        if entity_kind == "album":
            return _album_payload(source, raw)
        if entity_kind == "track":
            return _track_payload(source, raw)
        raise MetadataProviderError(source, entity_kind, f"Unsupported entity kind: {entity_kind}")


_ENGINE_INSTANCE: Optional[MetadataEngine] = None
_ENGINE_LOCK = threading.RLock()


def get_metadata_engine() -> MetadataEngine:
    global _ENGINE_INSTANCE
    with _ENGINE_LOCK:
        if _ENGINE_INSTANCE is None:
            _ENGINE_INSTANCE = MetadataEngine()
        return _ENGINE_INSTANCE


def clear_metadata_engine_cache() -> None:
    global _ENGINE_INSTANCE
    with _ENGINE_LOCK:
        _ENGINE_INSTANCE = None
