"""Probe EXT.to search scraping and magnet extraction.

This is intentionally a standalone test script. It does not integrate with
SoulSync yet; it answers one question: can we fetch EXT.to HTML, discover result
pages for a query, and extract magnet links from those detail pages?

Usage:
    python scripts/extto_scrape_probe.py interstellar
    python scripts/extto_scrape_probe.py "interstellar 2014" --show-magnets
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html import unescape
from typing import Iterable
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
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SoulSync-ExtTo-Probe/0.1"
)
MAGNET_RE = re.compile(r"magnet:\?xt=urn:[^\s\"'<>]+", re.I)
DETAIL_PATH_RE = re.compile(r"^/[a-z0-9][a-z0-9-]+-\d+/?$", re.I)


@dataclass
class SearchHit:
    title: str
    url: str
    seeders: int | None = None
    leechers: int | None = None
    size: str | None = None
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
        seeders = parse_int(re.search(r"(?:seeders?|seeds?)\D+(\d[\d,]*)", lower).group(1) if re.search(r"(?:seeders?|seeds?)\D+(\d[\d,]*)", lower) else None)
        leechers = parse_int(re.search(r"(?:leechers?|peers?)\D+(\d[\d,]*)", lower).group(1) if re.search(r"(?:leechers?|peers?)\D+(\d[\d,]*)", lower) else None)
        size_match = re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)\b", row_text, re.I)
        hits.append(SearchHit(title=title, url=url, seeders=seeders, leechers=leechers, size=size_match.group(0) if size_match else None))
        seen.add(url)
        if len(hits) >= limit:
            break
    return hits


def extract_magnet(html: str) -> str | None:
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


def redact_magnet(magnet: str | None) -> str | None:
    if not magnet:
        return None
    hash_match = re.search(r"btih:([a-z0-9]+)", magnet, re.I)
    if not hash_match:
        return "magnet:?xt=urn:btih:<redacted>"
    info_hash = hash_match.group(1)
    return "magnet:?xt=urn:btih:" + info_hash[:8] + "..." + info_hash[-6:]


def scrape(query: str, limit: int, timeout: int, use_cloudscraper: bool) -> dict:
    s = session(use_cloudscraper)
    attempts = []
    blocked = []
    search_html = None
    search_url = None
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
    for hit in hits:
        try:
            detail_html = fetch_html(s, hit.url, timeout)
            hit.magnet = extract_magnet(detail_html)
        except ExtToBlocked as exc:
            attempts.append({"url": hit.url, "status": "blocked", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            attempts.append({"url": hit.url, "status": "error", "error": repr(exc)})

    return {
        "query": query,
        "ok": True,
        "search_url": search_url,
        "attempts": attempts,
        "results": [asdict(h) for h in hits],
        "magnet_count": sum(1 for h in hits if h.magnet),
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Probe EXT.to HTML search + magnet extraction")
    p.add_argument("query", nargs="?", default="interstellar")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--timeout", type=int, default=25)
    p.add_argument("--no-cloudscraper", action="store_true")
    p.add_argument("--show-magnets", action="store_true")
    args = p.parse_args(argv)

    report = scrape(args.query, max(1, args.limit), args.timeout, not args.no_cloudscraper)
    if not args.show_magnets:
        for row in report.get("results", []):
            row["magnet"] = redact_magnet(row.get("magnet"))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))