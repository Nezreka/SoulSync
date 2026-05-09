"""Spotify metadata adapter."""

from __future__ import annotations

import base64
import time
from typing import Any, Optional

from core.metadata.exceptions import MetadataProviderError, MetadataRateLimited
from core.metadata.providers.base import BaseMetadataAdapter


class SpotifyMetadataAdapter(BaseMetadataAdapter):
    provider_name = "spotify"
    min_api_interval = 0.35
    timeout = 15.0

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    BASE_URL = "https://api.spotify.com/v1"

    def __init__(self) -> None:
        super().__init__()
        self._credential_fingerprint: Optional[tuple[str, str]] = None
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    def _read_credentials(self) -> tuple[Optional[str], Optional[str]]:
        try:
            from config.settings import config_manager

            config = config_manager.get("spotify", {}) or {}
        except Exception:
            config = {}
        client_id = (config.get("client_id") or "").strip() or None
        client_secret = (config.get("client_secret") or "").strip() or None
        return client_id, client_secret

    def is_available(self) -> bool:
        client_id, client_secret = self._read_credentials()
        return bool(client_id and client_secret)

    def is_authenticated(self) -> bool:
        return self.is_available()

    def reload_config(self) -> None:
        self._credential_fingerprint = None
        self._access_token = None
        self._token_expires_at = 0.0

    def _get_access_token(self) -> Optional[str]:
        client_id, client_secret = self._read_credentials()
        if not client_id or not client_secret:
            return None

        fingerprint = (client_id, client_secret)
        if fingerprint != self._credential_fingerprint:
            self._credential_fingerprint = fingerprint
            self._access_token = None
            self._token_expires_at = 0.0

        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = self.session.post(
            self.TOKEN_URL,
            headers=headers,
            data={"grant_type": "client_credentials"},
            timeout=self.timeout,
        )
        if response.status_code == 429:
            retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
            raise MetadataRateLimited(
                self.provider_name,
                "token",
                "Spotify token endpoint rate limited",
                status_code=429,
                retry_after=retry_after,
                payload=response.text,
            )
        if response.status_code >= 400:
            raise MetadataProviderError(
                self.provider_name,
                "token",
                f"Spotify token request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                payload=response.text,
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise MetadataProviderError(
                self.provider_name,
                "token",
                "Spotify token response did not include an access token",
                status_code=response.status_code,
                payload=payload,
            )

        expires_in = int(payload.get("expires_in", 3600) or 3600)
        self._access_token = str(token)
        self._token_expires_at = now + max(60, expires_in - 60)
        return self._access_token

    def _auth_headers(self) -> dict[str, str]:
        token = self._get_access_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _request_api(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{self.BASE_URL}/{path_or_url.lstrip('/')}"
        return self._request_json(method, url, params=params, headers=self._auth_headers())

    def search_tracks_raw(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        payload = self._request_api(
            "GET",
            "search",
            params={"q": query, "type": "track", "limit": min(limit, 50)},
        )
        if not payload:
            return []
        return list((payload.get("tracks") or {}).get("items") or [])

    def search_artists_raw(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        search_query = f"artist:{query}" if len((query or "").strip()) <= 4 else query
        payload = self._request_api(
            "GET",
            "search",
            params={"q": search_query, "type": "artist", "limit": min(limit, 50)},
        )
        if not payload:
            return []
        return list((payload.get("artists") or {}).get("items") or [])

    def search_albums_raw(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        payload = self._request_api(
            "GET",
            "search",
            params={"q": query, "type": "album", "limit": min(limit, 50)},
        )
        if not payload:
            return []
        return list((payload.get("albums") or {}).get("items") or [])

    def get_track_raw(self, track_id: str) -> Optional[dict[str, Any]]:
        return self._request_api("GET", f"tracks/{track_id}")

    def get_album_raw(self, album_id: str) -> Optional[dict[str, Any]]:
        return self._request_api("GET", f"albums/{album_id}")

    def get_artist_raw(self, artist_id: str) -> Optional[dict[str, Any]]:
        return self._request_api("GET", f"artists/{artist_id}")

    def get_track_features_raw(self, track_id: str) -> Optional[dict[str, Any]]:
        return self._request_api("GET", f"audio-features/{track_id}")

    def get_album_tracks_raw(self, album_id: str, limit: int = 50, max_pages: int = 0) -> Optional[dict[str, Any]]:
        first_page = self._request_api("GET", f"albums/{album_id}/tracks", params={"limit": min(limit, 50)})
        if not first_page or not first_page.get("items"):
            return None

        all_tracks = list(first_page.get("items") or [])
        page_count = 1
        next_page = first_page
        while next_page.get("next") and (max_pages <= 0 or page_count < max_pages):
            next_page = self._request_api("GET", next_page["next"])
            page_count += 1
            if next_page and next_page.get("items"):
                all_tracks.extend(next_page.get("items") or [])

        result = dict(first_page)
        result["items"] = all_tracks
        result["next"] = None
        result["limit"] = len(all_tracks)
        return result

    def get_artist_albums_raw(
        self,
        artist_id: str,
        album_type: str = "album,single",
        limit: int = 10,
        max_pages: int = 0,
    ) -> list[dict[str, Any]]:
        results = self._request_api(
            "GET",
            f"artists/{artist_id}/albums",
            params={"include_groups": album_type, "limit": min(limit, 50)},
        )
        if not results:
            return []

        albums = list(results.get("items") or [])
        page_count = 1
        next_page = results
        while next_page.get("next") and (max_pages <= 0 or page_count < max_pages):
            next_page = self._request_api("GET", next_page["next"])
            page_count += 1
            if next_page and next_page.get("items"):
                albums.extend(next_page.get("items") or [])
        return albums
