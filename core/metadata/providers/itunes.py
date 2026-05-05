"""iTunes metadata adapter."""

from __future__ import annotations

import re
from typing import Any, Optional

from core.metadata.providers.base import BaseMetadataAdapter


class ITunesMetadataAdapter(BaseMetadataAdapter):
    provider_name = "itunes"
    min_api_interval = 3.0
    timeout = 30.0

    SEARCH_URL = "https://itunes.apple.com/search"
    LOOKUP_URL = "https://itunes.apple.com/lookup"
    FALLBACK_COUNTRIES = ["US", "GB", "FR", "DE", "JP", "AU", "CA", "BR", "KR", "SE"]

    def __init__(self) -> None:
        super().__init__()
        self._fixed_country: Optional[str] = None
        self.reload_config()

    def reload_config(self) -> None:
        try:
            from config.settings import config_manager

            country = config_manager.get("itunes.country", "US")
        except Exception:
            country = "US"
        if self._fixed_country:
            country = self._fixed_country
        self._country = (country or "US").upper()

    @property
    def country(self) -> str:
        try:
            from config.settings import config_manager

            if self._fixed_country:
                return self._fixed_country
            country = config_manager.get("itunes.country", self._country or "US") or "US"
            return str(country).upper()
        except Exception:
            return self._country or "US"

    def is_authenticated(self) -> bool:
        return True

    def _search_raw(self, term: str, entity: str, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._request_json(
            "GET",
            self.SEARCH_URL,
            params={
                "term": term,
                "country": self.country,
                "media": "music",
                "entity": entity,
                "limit": min(limit, 200),
                "explicit": "Yes",
            },
            timeout=self.timeout,
        )
        if not payload:
            return []
        results = list(payload.get("results") or [])
        if entity == "song":
            return [item for item in results if item.get("wrapperType") == "track" and item.get("kind") == "song"]
        if entity == "album":
            return [item for item in results if item.get("wrapperType") == "collection"]
        if entity == "musicArtist":
            return [item for item in results if item.get("wrapperType") == "artist"]
        return results

    def _lookup_raw(self, **params) -> list[dict[str, Any]]:
        params = dict(params or {})
        params["country"] = self.country
        payload = self._request_json("GET", self.LOOKUP_URL, params=params, timeout=self.timeout)
        if not payload:
            return []
        results = list(payload.get("results") or [])
        if results:
            return results

        if "id" in params:
            for fallback in self.FALLBACK_COUNTRIES:
                if fallback == self.country:
                    continue
                fallback_params = dict(params)
                fallback_params["country"] = fallback
                try:
                    payload = self._request_json("GET", self.LOOKUP_URL, params=fallback_params, timeout=15)
                except Exception:
                    continue
                if payload and payload.get("results"):
                    return list(payload.get("results") or [])
        return []

    def search_tracks_raw(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._search_raw(query, "song", limit)

    def search_artists_raw(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._search_raw(query, "musicArtist", limit)

    def search_albums_raw(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._search_raw(query, "album", limit * 2)

    def get_track_raw(self, track_id: str) -> Optional[dict[str, Any]]:
        results = self._lookup_raw(id=track_id)
        for item in results:
            if item.get("wrapperType") == "track":
                return item
        return None

    def get_album_raw(self, album_id: str) -> Optional[dict[str, Any]]:
        results = self._lookup_raw(id=album_id)
        for item in results:
            if item.get("wrapperType") == "collection":
                return item
        return None

    def get_artist_raw(self, artist_id: str) -> Optional[dict[str, Any]]:
        results = self._lookup_raw(id=artist_id)
        for item in results:
            if item.get("wrapperType") == "artist":
                return item
        return None

    def get_album_tracks_raw(self, album_id: str, limit: int = 50, max_pages: int = 0) -> Optional[dict[str, Any]]:
        results = self._lookup_raw(id=album_id, entity="song", limit=min(limit, 200))
        if not results:
            return None

        album_raw = None
        tracks: list[dict[str, Any]] = []
        for item in results:
            if item.get("wrapperType") == "collection" and album_raw is None:
                album_raw = item
            elif item.get("wrapperType") == "track" and item.get("kind") == "song":
                tracks.append(item)

        if not tracks:
            return None

        return {
            "items": tracks,
            "total": len(tracks),
            "limit": len(tracks),
            "next": None,
            "album": album_raw,
        }

    def get_artist_albums_raw(self, artist_id: str, album_type: str = "album,single", limit: int = 200, max_pages: int = 0) -> list[dict[str, Any]]:
        results = self._lookup_raw(id=artist_id, entity="album", limit=min(limit, 200))
        if not results:
            return []

        seen: dict[str, dict[str, Any]] = {}
        track_count_cache: dict[str, int] = {}

        def _normalize_album_name(name: str) -> str:
            normalized = (name or "").lower().strip()
            normalized = re.sub(
                r"\s*[\(\[]\s*(deluxe|explicit|clean|remaster|expanded|anniversary|edition|version|bonus|special|standard).*?[\)\]]",
                "",
                normalized,
                flags=re.IGNORECASE,
            )
            normalized = re.sub(r"\s*[-–—]\s*(deluxe|explicit|clean|remaster|expanded|anniversary|edition|version).*?$", "", normalized, flags=re.IGNORECASE)
            normalized = re.sub(r"\s+", " ", normalized).strip()
            return normalized

        def _album_has_tracks(collection_id: str) -> bool:
            if not collection_id:
                return False
            if collection_id not in track_count_cache:
                items = self._lookup_raw(id=collection_id, entity="song", limit=200)
                track_count_cache[collection_id] = sum(
                    1
                    for item in items
                    if item.get("wrapperType") == "track" and item.get("kind") == "song"
                )
            return track_count_cache[collection_id] > 0

        for album_data in results:
            if album_data.get("wrapperType") != "collection":
                continue
            normalized_name = _normalize_album_name(str(album_data.get("collectionName", "") or ""))
            collection_id = str(album_data.get("collectionId", "") or "")
            current = seen.get(normalized_name)
            is_explicit = album_data.get("collectionExplicitness") == "explicit"
            has_tracks = not is_explicit or _album_has_tracks(collection_id)
            if not has_tracks:
                continue
            if current is None:
                seen[normalized_name] = {"data": album_data, "is_explicit": is_explicit}
                continue
            if is_explicit and not current["is_explicit"]:
                seen[normalized_name] = {"data": album_data, "is_explicit": True}

        albums = [item["data"] for item in seen.values()]
        return albums[:limit]

    def get_artist_image_from_albums_raw(self, artist_id: str) -> Optional[str]:
        results = self._lookup_raw(id=artist_id, entity="album", limit=1)
        for item in results:
            if item.get("wrapperType") == "collection" and item.get("artworkUrl100"):
                return str(item["artworkUrl100"]).replace("100x100bb", "600x600bb")
        return None
