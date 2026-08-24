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
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any, Iterable
from urllib.parse import quote_plus, urljoin, urlparse

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
    ("GET", "/getTorrentMagnet/{id}", None),
    ("GET", "/getTorrentMagnet?id={id}", None),
    ("GET", "/ajax/getTorrentMagnet/{id}", None),
    ("GET", "/ajax/getTorrentMagnet?id={id}", None),
    ("POST", "/ajax/getTorrentMagnet", {"id": "{id}"}),
    ("GET", "/download/getTorrentMagnet/{id}", None),
    ("GET", "/torrent/getTorrentMagnet/{id}", None),
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SoulSync-ExtTo-Probe/0.2"
)
MAGNET_RE = re.compile(r"magnet:\?xt=urn:[^\s\"'<>]+", re.I)
DETAIL_PATH_RE = re.compile(r"^/[a-z0-9][a-z0-9-]+-\d+/?$", re.I)
INFO_HASH_RE = re.compile(r"\b([a-f0-9]{40})\b", re.I)


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


def fetch_html(s: requests.Session, url: str, timeout: int) -> str:
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


def magnet_endpoint_urls(detail_url: str, torrent_id: str) -> list[tuple[str, str, dict[str, str] | None]]:
    parsed = urlparse(detail_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    out = []
    for method, path, body in MAGNET_ENDPOINTS:
        url = base + path.format(id=quote_plus(torrent_id))
        data = {k: v.format(id=torrent_id) for k, v in body.items()} if body else None
        out.append((method, url, data))
    return out


def fetch_magnet_by_id(
    s: requests.Session, detail_url: str, torrent_id: str, timeout: int
) -> tuple[str | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": detail_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    for method, url, data in magnet_endpoint_urls(detail_url, torrent_id):
        try:
            if method == "POST":
                r = s.post(url, data=data, headers=headers, timeout=timeout, allow_redirects=True)
            else:
                r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            text = r.text or ""
            if is_cloudflare_challenge(r.status_code, text):
                raise ExtToBlocked(f"Cloudflare challenge at {r.url} ({r.status_code})")
            entry: dict[str, Any] = {"url": url, "method": method, "status_code": r.status_code}
            if r.status_code >= 400:
                entry["status"] = "http_error"
                attempts.append(entry)
                continue
            try:
                payload: Any = r.json()
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
    s: requests.Session, detail_url: str, html: str, timeout: int
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    direct = extract_direct_magnet(html)
    if direct:
        return direct, None, []
    info_hash = extract_info_hash(html)
    if info_hash:
        return f"magnet:?xt=urn:btih:{info_hash}", None, []
    endpoint_attempts: list[dict[str, Any]] = []
    ids = extract_magnet_ids(html)
    for torrent_id in ids:
        magnet, attempts = fetch_magnet_by_id(s, detail_url, torrent_id, timeout)
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


def scrape(query: str, limit: int, timeout: int, use_cloudscraper: bool, detail_url: str | None = None) -> dict:
    s = session(use_cloudscraper)
    attempts: list[dict[str, Any]] = []
    blocked: list[str] = []
    search_html = None
    search_url = None
    if detail_url:
        hits = [detail_hit_from_url(detail_url)]
    else:
        for url in candidate_search_urls(query, DEFAULT_BASES):
            try:
                search_html = fetch_html(s, url, timeout)
                search_url = url
                attempts.append({"url": url, "status": "ok", "bytes": len(search_html)})
                break
            except ExtToBlocked as exc:
                attempts.append({"url": url, "status": "blocked", "error": str(exc)})
                blocked.append(str(exc))
            except Exception as exc:  # noqa: BLE001 - probe wants diagnostics
                attempts.append({"url": url, "status": "error", "error": repr(exc)})
        if search_html is None or search_url is None:
            return {"query": query, "ok": False, "blocked": bool(blocked), "attempts": attempts, "results": []}
        hits = parse_result_links(search_html, search_url, limit)

    fetched_any_detail = False
    for hit in hits:
        try:
            detail_html = fetch_html(s, hit.url, timeout)
            fetched_any_detail = True
            magnet, magnet_id, endpoint_attempts = extract_magnet(s, hit.url, detail_html, timeout)
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
        "ok": bool(search_url or (detail_url and fetched_any_detail)),
        "blocked": bool(blocked),
        "search_url": search_url,
        "attempts": attempts,
        "results": [asdict(h) for h in hits],
        "magnet_count": sum(1 for h in hits if h.magnet),
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Probe EXT.to HTML search + magnet extraction")
    p.add_argument("query", nargs="?", default="interstellar")
    p.add_argument("--detail-url", help="skip search and probe one EXT.to detail page")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--timeout", type=int, default=25)
    p.add_argument("--no-cloudscraper", action="store_true")
    p.add_argument("--show-magnets", action="store_true")
    args = p.parse_args(argv)

    report = scrape(args.query, max(1, args.limit), args.timeout, not args.no_cloudscraper, args.detail_url)
    if not args.show_magnets:
        for row in report.get("results", []):
            row["magnet"] = redact_magnet(row.get("magnet"))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))