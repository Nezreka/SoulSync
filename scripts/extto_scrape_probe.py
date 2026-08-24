"""Probe EXT.to search scraping and magnet extraction.

This is intentionally a standalone test script. It does not integrate with
SoulSync yet; it answers one question: can we fetch EXT.to HTML, discover result
pages for a query, and extract magnet links from those detail pages?

EXT.to currently appears to reveal magnets through JavaScript. Detail pages can
contain a button like ``.download-btn-magnet[data-id]`` whose click issues an XHR
containing ``getTorrentMagnet``. This probe parses that id and tries likely JSON
endpoints, while still supporting direct magnet links and visible info hashes.

Usage:
    python scripts/extto_scrape_probe.py interstellar
    python scripts/extto_scrape_probe.py "interstellar 2014" --show-magnets
    python scripts/extto_scrape_probe.py --detail-url https://ext.to/example-123/
    python scripts/extto_scrape_probe.py interstellar --flaresolverr-url http://localhost:8191
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any, Iterable
from urllib.parse import quote_plus, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:  # optional in this repo; kept as a best-effort probe only
    import cloudscraper  # type: ignore
except Exception:  # pragma: no cover - depends on local environment
    cloudscraper = None


DEFAULT_BASES = ("https://ext.to", "https://search.extto.com")
SEARCH_PATHS = (
    "/browse/?q={q}&with_adult=1",
    "/browse/?order=desc&q={q}&sort=age&user_sort=1",
    "/search/?q={q}",
    "/search/{slug}/",
    "/?q={q}",
)
MAGNET_ENDPOINTS = (
    ("POST", "/ajax/getTorrentMagnet.php", None),
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SoulSync-ExtTo-Probe/0.3"
)
MAGNET_RE = re.compile(r"magnet:\?xt=urn:[^\s\"'<>]+", re.I)
DETAIL_PATH_RE = re.compile(r"^/[a-z0-9][a-z0-9-]+-\d+/?$", re.I)
INFO_HASH_RE = re.compile(r"\b([a-f0-9]{40})\b", re.I)
PAGE_TOKEN_RE = re.compile(r"window\.pageToken\s*=\s*['\"]([^'\"]+)", re.I)
CSRF_TOKEN_RE = re.compile(r"window\.csrfToken\s*=\s*['\"]([^'\"]+)", re.I)


@dataclass
class SearchHit:
    title: str
    url: str
    seeders: int | None = None
    leechers: int | None = None
    size: str | None = None
    magnet_id: str | None = None
    magnet: str | None = None


class ExtToBlocked(RuntimeError):
    pass


class FlareSolverrClient:
    """Tiny client for FlareSolverr's POST /v1 command API."""

    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id = "soulsync-extto-probe"

    def close(self) -> None:
        try:
            requests.post(
                self.base_url + "/v1",
                json={"cmd": "sessions.destroy", "session": self.session_id},
                timeout=min(self.timeout, 10),
            )
        except requests.RequestException:
            pass

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        payload: dict[str, Any] = {
            "cmd": "request.post" if method.upper() == "POST" else "request.get",
            "url": url,
            "session": self.session_id,
            "maxTimeout": self.timeout * 1000,
        }
        if headers:
            payload["headers"] = headers
        if method.upper() == "POST":
            payload["postData"] = urlencode(data or {})
        r = requests.post(self.base_url + "/v1", json=payload, timeout=self.timeout + 10)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "ok":
            raise RuntimeError(body.get("message") or "FlareSolverr request failed")
        solution = body.get("solution") or {}
        return int(solution.get("status") or 0), str(solution.get("response") or ""), str(solution.get("url") or url)


def is_cloudflare_challenge(status: int, html: str) -> bool:
    sample = html[:8000].lower()
    return status in (403, 429, 503) and (
        "just a moment" in sample
        or "cf-browser-verification" in sample
        or "challenge-platform" in sample
        or "cloudflare" in sample
    )


def session(use_cloudscraper: bool) -> requests.Session:
    if use_cloudscraper and cloudscraper is not None:
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    else:
        s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    return s


def fetch_html(
    s: requests.Session,
    url: str,
    timeout: int,
    solver: FlareSolverrClient | None = None,
) -> str:
    if solver:
        status, text, final_url = solver.request("GET", url)
        if is_cloudflare_challenge(status, text):
            raise ExtToBlocked(f"Cloudflare challenge at {final_url} ({status})")
        if status >= 400:
            raise requests.HTTPError(f"FlareSolverr returned HTTP {status} for {final_url}")
        return text
    r = s.get(url, timeout=timeout, allow_redirects=True)
    text = r.text or ""
    if is_cloudflare_challenge(r.status_code, text):
        raise ExtToBlocked(f"Cloudflare challenge at {r.url} ({r.status_code})")
    r.raise_for_status()
    return text


def candidate_search_urls(query: str, bases: Iterable[str]) -> list[str]:
    slug = quote_plus(query).replace("+", "-")
    q = quote_plus(query)
    out: list[str] = []
    for base in bases:
        for path in SEARCH_PATHS:
            out.append(base.rstrip("/") + path.format(q=q, slug=slug))
    return out


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else None


def parse_result_links(html: str, base_url: str, limit: int) -> list[SearchHit]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    hits: list[SearchHit] = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        parsed = urlparse(href)
        path = parsed.path if parsed.scheme else href.split("?", 1)[0]
        if not DETAIL_PATH_RE.match(path):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = clean_text(a.get_text(" "))
        if not title or len(title) < 3:
            title = path.strip("/").rsplit("-", 1)[0].replace("-", " ").title()
        row_text = clean_text(a.find_parent().get_text(" ") if a.find_parent() else "")
        lower = row_text.lower()
        seeds_match = re.search(r"(?:seeders?|seeds?)\D+(\d[\d,]*)", lower)
        leech_match = re.search(r"(?:leechers?|peers?)\D+(\d[\d,]*)", lower)
        size_match = re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)\b", row_text, re.I)
        hits.append(
            SearchHit(
                title=title,
                url=url,
                seeders=parse_int(seeds_match.group(1) if seeds_match else None),
                leechers=parse_int(leech_match.group(1) if leech_match else None),
                size=size_match.group(0) if size_match else None,
            )
        )
        seen.add(url)
        if len(hits) >= limit:
            break
    return hits


def extract_direct_magnet(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select('a[href^="magnet:"]'):
        href = a.get("href")
        if href:
            return unescape(href)
    for node in soup.select("[data-clipboard-text], [data-magnet]"):
        value = node.get("data-clipboard-text") or node.get("data-magnet")
        if value and value.lower().startswith("magnet:"):
            return unescape(value)
    m = MAGNET_RE.search(html)
    return unescape(m.group(0)) if m else None


def extract_info_hash(html: str) -> str | None:
    text = BeautifulSoup(html, "lxml").get_text(" ")
    m = INFO_HASH_RE.search(text) or INFO_HASH_RE.search(html)
    return m.group(1).lower() if m else None


def extract_magnet_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    ids: list[str] = []
    selectors = (
        ".download-btn-magnet[data-id]",
        ".detail-magnet-link[data-id]",
        "[title*='magnet' i][data-id]",
        "[data-id][onclick*='Magnet' i]",
        "[data-id][onclick*='magnet' i]",
    )
    for node in soup.select(",".join(selectors)):
        value = str(node.get("data-id") or "").strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def magnet_from_payload(payload: Any) -> str | None:
    if isinstance(payload, str):
        return extract_direct_magnet(payload)
    if isinstance(payload, dict):
        for key in ("url", "magnet", "data", "link", "href"):
            value = payload.get(key)
            if isinstance(value, str) and value.lower().startswith("magnet:"):
                return unescape(value)
        value = payload.get("hash")
        if isinstance(value, str) and INFO_HASH_RE.fullmatch(value.strip()):
            return "magnet:?xt=urn:btih:" + value.strip().lower()
        for value in payload.values():
            found = magnet_from_payload(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = magnet_from_payload(value)
            if found:
                return found
    return None


def extract_page_tokens(html: str) -> tuple[str | None, str | None]:
    page = PAGE_TOKEN_RE.search(html)
    csrf = CSRF_TOKEN_RE.search(html)
    return (page.group(1) if page else None, csrf.group(1) if csrf else None)


def compute_hmac(torrent_id: str, timestamp: int, page_token: str) -> str:
    data = f"{int(torrent_id)}|{int(timestamp)}|{page_token}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def magnet_endpoint_urls(
    detail_url: str,
    torrent_id: str,
    page_token: str,
    csrf_token: str,
    timestamp: int,
) -> list[tuple[str, str, dict[str, str] | None]]:
    parsed = urlparse(detail_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    hmac_token = compute_hmac(torrent_id, timestamp, page_token)
    out = []
    for method, path, _body in MAGNET_ENDPOINTS:
        url = base + path.format(id=quote_plus(torrent_id))
        data = {
            "torrent_id": str(int(torrent_id)),
            "download_type": "magnet",
            "timestamp": str(timestamp),
            "hmac": hmac_token,
            "sessid": csrf_token,
        }
        out.append((method, url, data))
    return out


def fetch_magnet_by_id(
    s: requests.Session,
    detail_url: str,
    torrent_id: str,
    timeout: int,
    page_token: str,
    csrf_token: str,
    timestamp: int,
    solver: FlareSolverrClient | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": detail_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    for method, url, data in magnet_endpoint_urls(detail_url, torrent_id, page_token, csrf_token, timestamp):
        try:
            if solver:
                status_code, text, final_url = solver.request(method, url, headers=headers, data=data)
            elif method == "POST":
                r = s.post(url, data=data, headers=headers, timeout=timeout, allow_redirects=True)
                status_code, text, final_url = r.status_code, r.text or "", r.url
            else:
                r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                status_code, text, final_url = r.status_code, r.text or "", r.url
            if is_cloudflare_challenge(status_code, text):
                raise ExtToBlocked(f"Cloudflare challenge at {final_url} ({status_code})")
            entry: dict[str, Any] = {"url": url, "method": method, "status_code": status_code}
            if status_code >= 400:
                entry["status"] = "http_error"
                attempts.append(entry)
                continue
            try:
                payload: Any = json.loads(text)
            except ValueError:
                payload = text
            magnet = magnet_from_payload(payload)
            entry["status"] = "ok" if magnet else "no_magnet"
            entry["bytes"] = len(text)
            attempts.append(entry)
            if magnet:
                return magnet, attempts
        except ExtToBlocked as exc:
            attempts.append({"url": url, "method": method, "status": "blocked", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - probe wants diagnostics
            attempts.append({"url": url, "method": method, "status": "error", "error": repr(exc)})
    return None, attempts


def extract_magnet(
    s: requests.Session,
    detail_url: str,
    html: str,
    timeout: int,
    solver: FlareSolverrClient | None = None,
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    direct = extract_direct_magnet(html)
    if direct:
        return direct, None, []
    info_hash = extract_info_hash(html)
    if info_hash:
        return f"magnet:?xt=urn:btih:{info_hash}", None, []
    endpoint_attempts: list[dict[str, Any]] = []
    ids = extract_magnet_ids(html)
    page_token, csrf_token = extract_page_tokens(html)
    if ids and (not page_token or not csrf_token):
        endpoint_attempts.append({"status": "missing_tokens", "has_page_token": bool(page_token), "has_csrf_token": bool(csrf_token)})
        return None, ids[0], endpoint_attempts
    timestamp = int(time.time())
    for torrent_id in ids:
        magnet, attempts = fetch_magnet_by_id(
            s, detail_url, torrent_id, timeout, page_token or "", csrf_token or "", timestamp, solver
        )
        endpoint_attempts.extend(attempts)
        if magnet:
            return magnet, torrent_id, endpoint_attempts
    return None, ids[0] if ids else None, endpoint_attempts


def redact_magnet(magnet: str | None) -> str | None:
    if not magnet:
        return None
    hash_match = re.search(r"btih:([a-z0-9]+)", magnet, re.I)
    if not hash_match:
        return "magnet:?xt=urn:btih:<redacted>"
    info_hash = hash_match.group(1)
    return "magnet:?xt=urn:btih:" + info_hash[:8] + "..." + info_hash[-6:]


def detail_hit_from_url(url: str) -> SearchHit:
    path = urlparse(url).path.strip("/")
    return SearchHit(title=path.rsplit("-", 1)[0].replace("-", " ").title(), url=url)


def scrape(
    query: str,
    limit: int,
    timeout: int,
    use_cloudscraper: bool,
    detail_url: str | None = None,
    flaresolverr_url: str | None = None,
) -> dict:
    s = session(use_cloudscraper)
    solver = FlareSolverrClient(flaresolverr_url, timeout) if flaresolverr_url else None
    attempts: list[dict[str, Any]] = []
    blocked: list[str] = []
    search_html = None
    search_url = None
    try:
        if detail_url:
            hits = [detail_hit_from_url(detail_url)]
        else:
            for url in candidate_search_urls(query, DEFAULT_BASES):
                try:
                    search_html = fetch_html(s, url, timeout, solver)
                    search_url = url
                    attempts.append({"url": url, "status": "ok", "bytes": len(search_html)})
                    break
                except ExtToBlocked as exc:
                    attempts.append({"url": url, "status": "blocked", "error": str(exc)})
                    blocked.append(str(exc))
                except Exception as exc:  # noqa: BLE001 - probe wants diagnostics
                    attempts.append({"url": url, "status": "error", "error": repr(exc)})
            if search_html is None or search_url is None:
                return {
                    "query": query,
                    "flaresolverr": bool(solver),
                    "ok": False,
                    "blocked": bool(blocked),
                    "attempts": attempts,
                    "results": [],
                }
            hits = parse_result_links(search_html, search_url, limit)

        fetched_any_detail = False
        for hit in hits:
            try:
                detail_html = fetch_html(s, hit.url, timeout, solver)
                fetched_any_detail = True
                magnet, magnet_id, endpoint_attempts = extract_magnet(s, hit.url, detail_html, timeout, solver)
                hit.magnet = magnet
                hit.magnet_id = magnet_id
                attempts.extend(endpoint_attempts)
            except ExtToBlocked as exc:
                attempts.append({"url": hit.url, "status": "blocked", "error": str(exc)})
                blocked.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                attempts.append({"url": hit.url, "status": "error", "error": repr(exc)})

        return {
            "query": query,
            "flaresolverr": bool(solver),
            "ok": bool(search_url or (detail_url and fetched_any_detail)),
            "blocked": bool(blocked),
            "search_url": search_url,
            "attempts": attempts,
            "results": [asdict(h) for h in hits],
            "magnet_count": sum(1 for h in hits if h.magnet),
        }
    finally:
        if solver:
            solver.close()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Probe EXT.to HTML search + magnet extraction")
    p.add_argument("query", nargs="?", default="interstellar")
    p.add_argument("--detail-url", help="skip search and probe one EXT.to detail page")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--timeout", type=int, default=25)
    p.add_argument("--no-cloudscraper", action="store_true")
    p.add_argument("--flaresolverr-url", help="optional FlareSolverr base URL, e.g. http://localhost:8191")
    p.add_argument("--show-magnets", action="store_true")
    args = p.parse_args(argv)

    report = scrape(
        args.query,
        max(1, args.limit),
        args.timeout,
        not args.no_cloudscraper,
        args.detail_url,
        args.flaresolverr_url,
    )
    if not args.show_magnets:
        for row in report.get("results", []):
            row["magnet"] = redact_magnet(row.get("magnet"))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))