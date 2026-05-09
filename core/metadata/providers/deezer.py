"""Deezer metadata adapter."""

from __future__ import annotations

from typing import Any, Optional

from core.metadata.providers.base import BaseMetadataAdapter


class DeezerMetadataAdapter(BaseMetadataAdapter):
    provider_name = "deezer"
    min_api_interval = 1.0
    timeout = 15.0

    BASE_URL = "https://api.deezer.com"

    def __init__(self) -> None:
        super().__init__()
        self._access_token: Optional[str] = None

    def reload_config(self) -> None:
        self._access_token = None

    def _read_access_token(self) -> Optional[str]:
        try:
            from config.settings import config_manager

            token = config_manager.get("deezer.access_token", None)
        except Exception:
            token = None
        return (str(token).strip() or None) if token else None

    def _request_api(self, endpoint: str, params: Optional[dict[str, Any]] = None, timeout: int = 15) -> Optional[dict[str, Any]]:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        params = dict(params or {})
        token = self._read_access_token()
        if token and "access_token" not in params:
            params["access_token"] = token
        payload = self._request_json("GET", url, params=params, timeout=timeout)
        return payload if isinstance(payload, dict) else None

    def is_authenticated(self) -> bool:
        return True

    def search_tracks_raw(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = self._request_api("search/track", {"q": query, "limit": min(limit, 100)})
        return list(payload.get("data") or []) if payload else []

    def search_artists_raw(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = self._request_api("search/artist", {"q": query, "limit": min(limit, 100)})
        return list(payload.get("data") or []) if payload else []

    def search_albums_raw(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = self._request_api("search/album", {"q": query, "limit": min(limit, 100)})
        return list(payload.get("data") or []) if payload else []

    def get_track_raw(self, track_id: str) -> Optional[dict[str, Any]]:
        return self._request_api(f"track/{track_id}")

    def get_album_raw(self, album_id: str) -> Optional[dict[str, Any]]:
        return self._request_api(f"album/{album_id}")

    def get_artist_raw(self, artist_id: str) -> Optional[dict[str, Any]]:
        return self._request_api(f"artist/{artist_id}")

    def get_album_tracks_raw(self, album_id: str, limit: int = 500, max_pages: int = 0) -> Optional[dict[str, Any]]:
        data = self._request_api(f"album/{album_id}/tracks", {"limit": min(limit, 500)})
        if not data or not data.get("data"):
            album_data = self._request_api(f"album/{album_id}")
            if album_data and album_data.get("tracks") and album_data["tracks"].get("data"):
                data = album_data["tracks"]
            else:
                return None

        album_info = self._request_api(f"album/{album_id}") or {}
        items = list(data.get("data") or [])
        return {
            "items": items,
            "total": len(items),
            "limit": len(items),
            "next": None,
            "album": album_info,
        }

    def get_artist_albums_raw(self, artist_id: str, album_type: str = "album,single", limit: int = 200, max_pages: int = 0) -> list[dict[str, Any]]:
        albums: list[dict[str, Any]] = []
        offset = 0
        page_size = 100
        artist_data = self.get_artist_raw(artist_id) or {}
        artist_name = str(artist_data.get("name", "") or "").strip()
        artist_stub: dict[str, Any] = {"id": artist_data.get("id") or artist_id}
        if artist_name:
            artist_stub["name"] = artist_name
        while offset < limit:
            fetch_limit = min(page_size, limit - offset)
            data = self._request_api(f"artist/{artist_id}/albums", {"limit": fetch_limit, "index": offset})
            if not data or not data.get("data"):
                break
            for item in list(data.get("data") or []):
                if not isinstance(item, dict):
                    continue
                enriched = dict(item)
                album_artist = enriched.get("artist")
                if isinstance(album_artist, dict):
                    merged_artist = dict(artist_stub)
                    merged_artist.update(album_artist)
                    enriched["artist"] = merged_artist
                else:
                    enriched["artist"] = dict(artist_stub)
                if artist_name and not enriched.get("artist_name"):
                    enriched["artist_name"] = artist_name
                albums.append(enriched)
            if len(data.get("data") or []) < fetch_limit:
                break
            offset += len(data.get("data") or [])
            if max_pages and offset >= max_pages * page_size:
                break
        return albums[:limit]
