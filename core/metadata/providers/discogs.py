"""Discogs metadata adapter."""

from __future__ import annotations

import re
from typing import Any, Optional

from core.metadata.providers.base import BaseMetadataAdapter


class DiscogsMetadataAdapter(BaseMetadataAdapter):
    provider_name = "discogs"
    min_api_interval = 2.5
    timeout = 15.0

    BASE_URL = "https://api.discogs.com"

    def __init__(self, token: Optional[str] = None) -> None:
        super().__init__()
        self.token = token
        self.reload_config()

    def reload_config(self) -> None:
        if self.token:
            self.session.headers["Authorization"] = f"Discogs token={self.token}"
        else:
            self.session.headers.pop("Authorization", None)

    def _read_token(self) -> Optional[str]:
        if self.token is not None:
            return self.token or None
        try:
            from config.settings import config_manager

            token = config_manager.get("discogs.token", "")
        except Exception:
            token = ""
        return (str(token).strip() or None) if token else None

    def _sync_auth_state(self) -> None:
        token = self._read_token()
        self.token = token
        if token:
            self.session.headers["Authorization"] = f"Discogs token={token}"
            self.min_api_interval = 1.0
        else:
            self.session.headers.pop("Authorization", None)
            self.min_api_interval = 2.5

    def is_available(self) -> bool:
        return True

    def is_authenticated(self) -> bool:
        return bool(self._read_token())

    def _request_api(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        self._sync_auth_state()
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        payload = self._request_json("GET", url, params=params, timeout=self.timeout)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _normalize_name(name: str) -> str:
        name = (name or "").lower().strip()
        name = re.sub(r"\s*\(.*?\)\s*", " ", name)
        name = re.sub(r"[^\w\s]", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        return name

    def search_artists_raw(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self._request_api(
            "/database/search",
            {"q": query, "type": "artist", "per_page": min(limit, 50)},
        )
        if not data:
            return []
        return list(data.get("results") or [])[:limit]

    def search_albums_raw(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self._request_api(
            "/database/search",
            {"q": query, "type": "release", "per_page": min(limit, 50)},
        )
        if not data:
            return []
        return list(data.get("results") or [])[: limit * 2]

    def search_tracks_raw(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return []

    def get_artist_raw(self, artist_id: str) -> Optional[dict[str, Any]]:
        return self._request_api(f"/artists/{artist_id}")

    def get_album_raw(self, release_id: str) -> Optional[dict[str, Any]]:
        data = self._request_api(f"/masters/{release_id}")
        if not data or not data.get("title"):
            data = self._request_api(f"/releases/{release_id}")
        return data

    def get_artist_albums_raw(self, artist_id: str, album_type: str = "album,single", limit: int = 50, max_pages: int = 0) -> list[dict[str, Any]]:
        artist_data = self._request_api(f"/artists/{artist_id}")
        artist_name = str((artist_data or {}).get("name", "") or "").lower()
        data = self._request_api(
            f"/artists/{artist_id}/releases",
            {"sort": "year", "sort_order": "desc", "per_page": min(limit * 3, 200)},
        )
        if not data:
            return []

        masters: list[dict[str, Any]] = []
        releases_no_master: list[dict[str, Any]] = []
        master_titles = set()
        for item in data.get("releases") or []:
            role = str(item.get("role", "Main") or "Main").lower()
            if role not in ("main", ""):
                continue
            release_artist = str(item.get("artist", "") or "")
            if artist_name and release_artist:
                primary = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+", release_artist, flags=re.IGNORECASE)[0]
                primary = re.split(r"\s*[&,]\s*", primary)[0].strip()
                if self._normalize_name(primary) != self._normalize_name(artist_name):
                    continue
            if item.get("type") == "master":
                masters.append(item)
                master_titles.add(str(item.get("title", "")).lower())
            else:
                releases_no_master.append(item)

        ordered = masters + [r for r in releases_no_master if str(r.get("title", "")).lower() not in master_titles]
        seen_titles = set()
        allowed_types = {part.strip() for part in (album_type or "album,single").split(",") if part.strip()}
        albums: list[dict[str, Any]] = []
        for item in ordered:
            title = str(item.get("title", "") or "").lower().strip()
            if title in seen_titles:
                continue
            seen_titles.add(title)
            album_type_value = "album"
            formats = item.get("formats", []) or []
            if formats:
                fmt = formats[0]
                descriptions = [str(desc).lower() for desc in (fmt.get("descriptions", []) or [])]
                format_name = str(fmt.get("name", "") or "").lower()
                raw_format = item.get("format") or ""
                if isinstance(raw_format, list):
                    format_str = ", ".join(raw_format).lower()
                else:
                    format_str = str(raw_format).lower()
                if "single" in descriptions or "single" in format_name or "single" in format_str:
                    album_type_value = "single"
                elif "ep" in descriptions or ", ep" in format_str or format_str.endswith("ep"):
                    album_type_value = "ep"
                elif "compilation" in descriptions or "compilation" in format_str:
                    album_type_value = "compilation"
            if album_type_value in allowed_types or (album_type_value == "ep" and "single" in allowed_types):
                albums.append(item)
            if len(albums) >= limit:
                break
        return albums

    def get_album_tracks_raw(self, release_id: str, limit: int = 50, max_pages: int = 0) -> Optional[dict[str, Any]]:
        data = self._request_api(f"/masters/{release_id}")
        if not data or not data.get("tracklist"):
            data = self._request_api(f"/releases/{release_id}")
        if not data or not data.get("tracklist"):
            return None
        track_items = [t for t in data.get("tracklist") or [] if t.get("type_", "") in ("track", "") or not t.get("type_")]
        return {
            "items": track_items,
            "total": len(track_items),
            "limit": len(track_items),
            "next": None,
            "album": data,
        }
