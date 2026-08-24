"""EXT.to-backed video torrent search via FlareSolverr.

EXT.to is not a Prowlarr/Newznab indexer, so Basic Search talks to it directly.
The site hides magnet links behind JavaScript: detail pages expose
``.download-btn-magnet[data-id]`` plus ``window.pageToken`` and ``window.csrfToken``;
main.min.js POSTs those to ``/ajax/getTorrentMagnet.php`` with a SHA-256 HMAC.

This module keeps the dependency explicit: no FlareSolverr URL, no search. Hits are
projected into the same raw shape as Prowlarr/slskd so api.video.downloads can run the
shared parse/evaluate/rank path and the grab flow can hand magnets to the torrent client.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from utils.logging_config import get_logger

logger = get_logger("video.extto_search")

DEFAULT_FLARESOLVERR_URL = "http://localhost:8191"
BASES = ("https://ext.to", "https://search.extto.com")
SEARCH_PATHS = (
    "/browse/?q={q}&with_adult=1",
    "/browse/?order=desc&q={q}&sort=age&user_sort=1",
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SoulSync-ExtTo/0.1"
)
DETAIL_PATH_RE = re.compile(r"^/[a-z0-9][a-z0-9-]+-\d+/?$", re.I)
MAGNET_RE = re.compile(r"magnet:\?xt=urn:[^\s\"'<>]+", re.I)
INFO_HASH_RE = re.compile(r"\b([a-f0-9]{40})\b", re.I)
PAGE_TOKEN_RE = re.compile(r"window\.pageToken\s*=\s*['\"]([^'\"]+)", re.I)
CSRF_TOKEN_RE = re.compile(r"window\.csrfToken\s*=\s*['\"]([^'\"]+)", re.I)


@dataclass
class ExtToHit:
    title: str
    url: str
    size_text: str | None = None
    seeders: int | None = None
    leechers: int | None = None
    magnet_id: str | None = None
    magnet: str | None = None


class ExtToBlocked(RuntimeError):
    pass


class FlareSolverrClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = timeout
        self.session_id = "soulsync-extto"

    def close(self) -> None:
        try:
            requests.post(self.base_url + "/v1", json={"cmd": "sessions.destroy", "session": self.session_id}, timeout=10)
        except requests.RequestException:
            pass

    def request(self, method: str, url: str, data: dict[str, str] | None = None) -> tuple[int, str, str]:
        payload: dict[str, Any] = {
            "cmd": "request.post" if method.upper() == "POST" else "request.get",
            "url": url,
            "session": self.session_id,
            "maxTimeout": self.timeout * 1000,
        }
        if method.upper() == "POST":
            payload["postData"] = urlencode(data or {})
        r = requests.post(self.base_url + "/v1", json=payload, timeout=self.timeout + 10)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "ok":
            raise RuntimeError(body.get("message") or "FlareSolverr request failed")
        sol = body.get("solution") or {}
        return int(sol.get("status") or 0), str(sol.get("response") or ""), str(sol.get("url") or url)


def flaresolverr_url() -> str:
    from core.settings import config_manager

    return str(config_manager.get("flaresolverr.url", DEFAULT_FLARESOLVERR_URL) or "").rstrip("/")


def is_configured() -> bool:
    return bool(flaresolverr_url())


def _cloudflare(status: int, html: str) -> bool:
    sample = html[:8000].lower()
    return status in (403, 429, 503) and (
        "just a moment" in sample or "challenge-platform" in sample or "cloudflare" in sample
    )


def _fetch(client: FlareSolverrClient, url: str, method: str = "GET", data: dict[str, str] | None = None) -> str:
    status, html, final = client.request(method, url, data=data)
    if _cloudflare(status, html):
        raise ExtToBlocked("Cloudflare challenge at %s (%s)" % (final, status))
    if status >= 400:
        raise requests.HTTPError("EXT.to returned HTTP %s for %s" % (status, final))
    return html


def _candidate_urls(query: str, bases: Iterable[str] = BASES) -> list[str]:
    q = quote_plus(str(query or "").strip())
    return [base.rstrip("/") + path.format(q=q) for base in bases for path in SEARCH_PATHS]


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d[\d,]*", value)
    return int(m.group(0).replace(",", "")) if m else None


def _size_bytes(text: str | None) -> int:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb)\b", text or "", re.I)
    if not m:
        return 0
    scale = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}[m.group(2).lower()]
    return int(float(m.group(1)) * scale)


def parse_results(html: str, base_url: str, limit: int = 20) -> list[ExtToHit]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[ExtToHit] = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        path = urlparse(href).path if urlparse(href).scheme else href.split("?", 1)[0]
        if not DETAIL_PATH_RE.match(path):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = _clean(a.get_text(" ")) or path.strip("/").rsplit("-", 1)[0].replace("-", " ").title()
        row_text = _clean(a.find_parent().get_text(" ") if a.find_parent() else "")
        lower = row_text.lower()
        seeds = re.search(r"(?:seeders?|seeds?)\D+(\d[\d,]*)", lower)
        peers = re.search(r"(?:leechers?|peers?)\D+(\d[\d,]*)", lower)
        size = re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)\b", row_text, re.I)
        out.append(ExtToHit(title=title, url=url, size_text=size.group(0) if size else None,
                            seeders=_int(seeds.group(1) if seeds else None),
                            leechers=_int(peers.group(1) if peers else None)))
        seen.add(url)
        if len(out) >= limit:
            break
    return out


def extract_magnet_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    ids: list[str] = []
    for node in soup.select(".download-btn-magnet[data-id],.detail-magnet-link[data-id],[title*='magnet' i][data-id]"):
        value = str(node.get("data-id") or "").strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def extract_page_tokens(html: str) -> tuple[str | None, str | None]:
    page = PAGE_TOKEN_RE.search(html)
    csrf = CSRF_TOKEN_RE.search(html)
    return (page.group(1) if page else None, csrf.group(1) if csrf else None)


def compute_hmac(torrent_id: str, timestamp: int, page_token: str) -> str:
    return hashlib.sha256(("%d|%d|%s" % (int(torrent_id), int(timestamp), page_token)).encode("utf-8")).hexdigest()


def _direct_magnet(html: str) -> str | None:
    m = MAGNET_RE.search(html)
    return unescape(m.group(0)) if m else None


def _magnet_from_payload(payload: Any) -> str | None:
    if isinstance(payload, str):
        return _direct_magnet(payload)
    if isinstance(payload, dict):
        for key in ("url", "magnet", "data", "link", "href"):
            value = payload.get(key)
            if isinstance(value, str) and value.lower().startswith("magnet:"):
                return unescape(value)
        value = payload.get("hash")
        if isinstance(value, str) and INFO_HASH_RE.fullmatch(value.strip()):
            return "magnet:?xt=urn:btih:" + value.strip().lower()
        for value in payload.values():
            found = _magnet_from_payload(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _magnet_from_payload(value)
            if found:
                return found
    return None


def fetch_magnet(client: FlareSolverrClient, detail_url: str, html: str, torrent_id: str) -> str | None:
    direct = _direct_magnet(html)
    if direct:
        return direct
    page_token, csrf_token = extract_page_tokens(html)
    if not page_token or not csrf_token:
        return None
    ts = int(time.time())
    body = {
        "torrent_id": str(int(torrent_id)),
        "download_type": "magnet",
        "timestamp": str(ts),
        "hmac": compute_hmac(torrent_id, ts, page_token),
        "sessid": csrf_token,
    }
    parsed = urlparse(detail_url)
    endpoint = "%s://%s/ajax/getTorrentMagnet.php" % (parsed.scheme, parsed.netloc)
    resp = _fetch(client, endpoint, "POST", body)
    try:
        payload: Any = json.loads(resp)
    except ValueError:
        payload = resp
    return _magnet_from_payload(payload)


def _project(hit: ExtToHit) -> dict:
    magnet = hit.magnet or ""
    guid = None
    m = re.search(r"btih:([a-z0-9]+)", magnet, re.I)
    if m:
        guid = "extto:" + m.group(1).lower()
    return {
        "title": hit.title,
        "size_bytes": _size_bytes(hit.size_text),
        "seeders": hit.seeders,
        "peers": hit.leechers,
        "username": "EXT.to",
        "filename": hit.title,
        "availability": hit.seeders if hit.seeders is not None else 0,
        "files": [],
        "file_count": 0,
        "folder_size_bytes": _size_bytes(hit.size_text),
        "download_url": magnet,
        "magnet_uri": magnet,
        "protocol": "torrent",
        "indexer_id": "extto",
        "guid": guid or ("extto:" + str(hit.magnet_id or hit.url)),
    }


def extto_search(query: Any, *, limit: int = 20, timeout: int = 30,
                 flaresolverr: Optional[str] = None) -> dict:
    q = str(query or "").strip()
    url = str(flaresolverr if flaresolverr is not None else flaresolverr_url()).rstrip("/")
    if not url:
        return {"configured": False, "hits": []}
    if not q:
        return {"configured": True, "hits": []}
    client = FlareSolverrClient(url, timeout=timeout)
    try:
        search_html, search_url = None, None
        errors = []
        for candidate in _candidate_urls(q):
            try:
                search_html = _fetch(client, candidate)
                search_url = candidate
                break
            except Exception as exc:  # noqa: BLE001 - try the mirror/alternate sort before surfacing
                errors.append(str(exc))
        if search_html is None or search_url is None:
            return {"configured": True, "error": errors[-1] if errors else "EXT.to search failed", "hits": []}
        hits = parse_results(search_html, search_url, limit=limit)
        out: list[dict] = []
        for hit in hits:
            try:
                detail_html = _fetch(client, hit.url)
                ids = extract_magnet_ids(detail_html)
                hit.magnet_id = ids[0] if ids else None
                if hit.magnet_id:
                    hit.magnet = fetch_magnet(client, hit.url, detail_html, hit.magnet_id)
                else:
                    hit.magnet = _direct_magnet(detail_html)
                if hit.magnet:
                    out.append(_project(hit))
            except Exception as exc:  # noqa: BLE001
                logger.debug("EXT.to detail probe failed for %s: %s", hit.url, exc)
        return {"configured": True, "live": True, "hits": out}
    finally:
        client.close()


__all__ = [
    "ExtToHit", "FlareSolverrClient", "compute_hmac", "extract_magnet_ids",
    "extract_page_tokens", "extto_search", "fetch_magnet", "parse_results",
]