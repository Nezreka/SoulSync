"""Shared FlareSolverr client.

FlareSolverr is the headless-browser proxy that solves Cloudflare challenges.
Two scrapers lean on it - EXT.to (video torrent search) and the Beatport
scraper (music discovery) - and both speak the same /v1 wire protocol, so the
client lives here once. Each caller keeps its OWN browser session id so one
site's poisoned session never costs the other a re-solve.
"""

from __future__ import annotations

from typing import Any

import requests

from utils.logging_config import get_logger

logger = get_logger("flaresolverr")

DEFAULT_FLARESOLVERR_URL = "http://localhost:8191"


class FlareSolverrClient:
    def __init__(self, base_url: str, timeout: int = 30, session_id: str = "soulsync"):
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = timeout
        self.session_id = session_id
        # The clearance FlareSolverr solved, kept so a DIRECT fetch (image bytes,
        # which FlareSolverr cannot stream) can reuse it. See extto_search.clearance().
        self.last_cookies: dict[str, str] = {}
        self.last_user_agent: str = ""

    def close(self) -> None:
        try:
            requests.post(self.base_url + "/v1", json={"cmd": "sessions.destroy", "session": self.session_id}, timeout=10)
        except requests.RequestException:
            logger.debug("FlareSolverr session destroy failed", exc_info=True)

    def request(self, method: str, url: str, data: dict[str, str] | None = None) -> tuple[int, str, str]:
        from urllib.parse import urlencode

        payload: dict[str, Any] = {
            "cmd": "request.post" if method.upper() == "POST" else "request.get",
            "url": url,
            "session": self.session_id,
            "maxTimeout": self.timeout * 1000,
        }
        if method.upper() == "POST":
            payload["postData"] = urlencode(data or {})
        r = requests.post(self.base_url + "/v1", json=payload, timeout=self.timeout + 10)
        # FlareSolverr answers a failed solve with HTTP 500 AND a JSON body naming the
        # cause ("Error solving the challenge. Timeout after 20.0 seconds."). This used
        # to raise_for_status() first, which threw that body away and surfaced
        # "500 Server Error: Internal Server Error for url: http://localhost:8191/v1" —
        # a message that names the proxy instead of the problem and reads like
        # FlareSolverr is broken when it is really just out of time.
        try:
            body = r.json()
        except ValueError:
            body = {}
        if body.get("status") != "ok":
            raise RuntimeError(body.get("message")
                               or "FlareSolverr returned HTTP %s" % r.status_code)
        sol = body.get("solution") or {}
        try:
            self.last_cookies = {str(c.get("name")): str(c.get("value"))
                                 for c in (sol.get("cookies") or []) if c.get("name")}
            self.last_user_agent = str(sol.get("userAgent") or "")
        except Exception:   # noqa: BLE001 - clearance is a bonus, never the request
            logger.debug("FlareSolverr clearance capture failed", exc_info=True)
        return int(sol.get("status") or 0), str(sol.get("response") or ""), str(sol.get("url") or url)


def flaresolverr_url() -> str:
    from core.settings import config_manager

    return str(config_manager.get("flaresolverr.url", DEFAULT_FLARESOLVERR_URL) or "").rstrip("/")
