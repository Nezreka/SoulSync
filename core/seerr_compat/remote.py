"""talks to a real seerr (jellyseerr / overseerr) server (#2324).

with a seerr url and api key in settings, soulsync reads the full request
behind a seerr webhook (who asked, which seasons, 4k or not) and tells seerr
what came of it: approved or declined, and available or partly available once
the files land. paths are seerr's own api (seerr-api.yml):

    GET  /api/v1/request/{requestId}
    POST /api/v1/request/{requestId}/approve | decline
    POST /api/v1/media/{mediaId}/available | partial   body {"is4k": bool}
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("seerr_compat.remote")

TIMEOUT_S = 10


class SeerrRemoteError(Exception):
    pass


def base_url(url: str) -> str:
    """the server's root: no trailing slash, no /api/v1 a person pasted in."""
    u = str(url or "").strip().rstrip("/")
    for tail in ("/api/v1", "/api"):
        if u.lower().endswith(tail):
            u = u[: -len(tail)].rstrip("/")
    return u


class SeerrRemote:
    def __init__(self, url: str, api_key: str, http: Optional[Callable[..., Any]] = None):
        self.base = base_url(url)
        self._key = str(api_key or "").strip()
        self._http = http

    @property
    def configured(self) -> bool:
        return bool(self.base and self._key)

    def _call(self, method: str, path: str, body: Optional[dict] = None):
        if not self.configured:
            raise SeerrRemoteError("no seerr url or api key")
        http = self._http
        if http is None:
            import requests
            http = requests.request
        try:
            r = http(method, self.base + path, headers={"X-Api-Key": self._key,
                                                        "Accept": "application/json"},
                     json=body, timeout=TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - every network failure reads the same
            raise SeerrRemoteError("could not reach seerr: %s" % e) from e
        code = int(getattr(r, "status_code", 0) or 0)
        if code < 200 or code >= 300:
            raise SeerrRemoteError("seerr answered HTTP %s for %s %s" % (code, method, path))
        try:
            return r.json()
        except Exception:  # noqa: BLE001 - an empty answer is fine for a status push
            return {}

    def get_request(self, request_id: int) -> dict:
        out = self._call("GET", "/api/v1/request/%d" % int(request_id))
        if not isinstance(out, dict):
            raise SeerrRemoteError("seerr sent no request")
        return out

    def decide(self, request_id: int, approve: bool) -> dict:
        return self._call("POST", "/api/v1/request/%d/%s" % (int(request_id),
                                                              "approve" if approve else "decline"))

    def media_status(self, media_id: int, status: str, is4k: bool = False) -> dict:
        if status not in ("available", "partial"):
            raise ValueError(status)
        return self._call("POST", "/api/v1/media/%d/%s" % (int(media_id), status),
                          {"is4k": bool(is4k)})

    def test(self) -> str:
        """a line for the settings Test button. /status says the version, a
        request list proves the key works."""
        about = self._call("GET", "/api/v1/status")
        self._call("GET", "/api/v1/request?take=1")
        version = (about or {}).get("version") if isinstance(about, dict) else None
        return "Connected to Seerr" + (" v%s" % version if version else "")
