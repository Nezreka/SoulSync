"""EXT.to homepage Fresh Releases scraper via FlareSolverr.

The homepage renders the Movies and TV torrent sections for Day, Week, and
Month in one document. This module parses those tables into a small read-only
shape for SoulSync's Search page. Rows also carry the homepage magnet URL and
release parser hints so the UI can identify the title before handing the exact
release to the normal download pipeline.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from core.video.extto_search import (
    BASES,
    DEFAULT_FLARESOLVERR_URL,
    ExtToBlocked,
    FlareSolverrClient,
    _clean,
    _fetch,
    _int,
    _size_bytes,
    _soup,
    flaresolverr_url,
)
from core.video.release_parse import extract_title, parse_release
from utils.logging_config import get_logger

logger = get_logger("video.extto_fresh")

PERIODS = ("day", "week", "month")
SECTIONS = {
    "movies": {"title": "Movies", "heading": "Movies Torrents", "suffix": "1"},
    "tv": {"title": "TV Series", "heading": "TV Torrents", "suffix": "2"},
}


@dataclass
class FreshRelease:
    title: str
    url: str
    size_text: str = ""
    size_bytes: int = 0
    files: int | None = None
    age: str = ""
    seeders: int | None = None
    leechers: int | None = None
    category: str = ""
    period: str = ""
    source: str = "EXT.to"
    download_url: str = ""
    magnet_uri: str = ""
    protocol: str = "torrent"
    indexer_id: str = "extto"
    username: str = "EXT.to"
    search_title: str = ""
    year: int | None = None
    resolution: str | None = None
    quality_label: str | None = None
    release_source: str | None = None
    codec: str | None = None
    audio: str | None = None
    hdr: str | None = None
    group: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    is_season_pack: bool = False
    is_series_pack: bool = False


def _cell_value(td: Tag | None, label: str | None = None) -> str:
    if td is None:
        return ""
    if label:
        for wrapper in td.select(".add-block-wrapper"):
            marker = _clean(wrapper.select_one(".add-block").get_text(" ") if wrapper.select_one(".add-block") else "")
            if marker.lower().rstrip("s") == label.lower().rstrip("s"):
                spans = wrapper.find_all("span")
                if spans:
                    return _clean(spans[-1].get_text(" "))
    chunks = [_clean(s.get_text(" ")) for s in td.find_all("span")]
    chunks = [c for c in chunks if c and c.lower() not in {"size", "files", "age", "seeds", "leechs", "leeches"}]
    return chunks[-1] if chunks else _clean(td.get_text(" "))


def _search_title(title: str) -> str:
    value = extract_title(title) or title
    value = re.sub(r"[\s(\[{._-]+$", "", value or "").strip()
    return value or title

def _magnet_url(row: Tag) -> str:
    node = row.select_one('a.torrent-dwn[href^="magnet:"]') or row.select_one('a[href^="magnet:"]')
    return str(node.get("href") or "").strip() if node else ""

def _parse_row(row: Tag, category: str, period: str, base_url: str) -> FreshRelease | None:
    cells = row.find_all("td", recursive=False)
    if not cells:
        return None
    title_link = cells[0].select_one(".float-left a[href]") or cells[0].select_one("a[href]")
    if not title_link:
        return None
    title = _clean(title_link.get_text(" "))
    if not title:
        return None
    detail_url = urljoin(base_url, str(title_link.get("href") or ""))
    size_text = _cell_value(cells[1] if len(cells) > 1 else None, "Size")
    files_text = _cell_value(cells[2] if len(cells) > 2 else None, "Files")
    age = _cell_value(cells[3] if len(cells) > 3 else None, "Age")
    seeds_text = _cell_value(cells[4] if len(cells) > 4 else None, "Seeds")
    leech_text = _cell_value(cells[5] if len(cells) > 5 else None, "Leechs")

    if not size_text:
        size_text = _cell_value(cells[0], "Size")
    if not age:
        age = _cell_value(cells[0], "Age")
    if not seeds_text:
        seeds_node = cells[0].select_one(".mobile-info .text-success")
        seeds_text = _clean(seeds_node.get_text(" ") if seeds_node else "")

    magnet = _magnet_url(row)
    parsed = parse_release(title)
    search_title = _search_title(title)
    quality_label = " ".join(str(x) for x in (parsed.get("resolution"), parsed.get("source")) if x) or None
    return FreshRelease(
        title=title,
        url=detail_url,
        size_text=size_text,
        size_bytes=_size_bytes(size_text),
        files=_int(files_text),
        age=age,
        seeders=_int(seeds_text),
        leechers=_int(leech_text),
        category=category,
        period=period,
        download_url=magnet,
        magnet_uri=magnet,
        search_title=search_title,
        year=parsed.get("year"),
        resolution=parsed.get("resolution"),
        quality_label=quality_label,
        release_source=parsed.get("source"),
        codec=parsed.get("codec"),
        audio=parsed.get("audio"),
        hdr=parsed.get("hdr"),
        group=parsed.get("group"),
        season=parsed.get("season"),
        episode=parsed.get("episode"),
        episode_end=parsed.get("episode_end"),
        is_season_pack=bool(parsed.get("is_season_pack")),
        is_series_pack=bool(parsed.get("is_series_pack")),
    )


def _section_card(soup: BeautifulSoup, heading: str) -> Tag | None:
    for h4 in soup.select("h4.titleH4"):
        if heading.lower() not in _clean(h4.get_text(" ")).lower():
            continue
        card = h4.find_parent("div", class_="card")
        if card:
            return card
    return None


def parse_fresh_releases(html: str, base_url: str = BASES[0]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    soup = _soup(html)
    sections: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for section_id, meta in SECTIONS.items():
        card = _section_card(soup, meta["heading"])
        periods: dict[str, list[dict[str, Any]]] = {p: [] for p in PERIODS}
        if card:
            for period in PERIODS:
                pane = card.select_one("#torrents-%s-%s" % (period, meta["suffix"]))
                if not pane:
                    continue
                rows = []
                for tr in pane.select("tbody tr"):
                    item = _parse_row(tr, section_id, period, base_url)
                    if item:
                        rows.append(asdict(item))
                periods[period] = rows
        sections[section_id] = periods
    return sections


def extto_fresh_releases(timeout: int = 25, flaresolverr: str | None = None) -> dict[str, Any]:
    solver = (flaresolverr if flaresolverr is not None else flaresolverr_url()).rstrip("/")
    if not solver:
        return {"configured": False, "live": False, "source": "EXT.to", "sections": {}, "error": "FlareSolverr is not configured"}
    client = FlareSolverrClient(solver or DEFAULT_FLARESOLVERR_URL, timeout=timeout)
    try:
        url = BASES[0] + "/"
        html = _fetch(client, url)
        sections = parse_fresh_releases(html, BASES[0])
        total = sum(len(rows) for section in sections.values() for rows in section.values())
        return {"configured": True, "live": True, "source": "EXT.to", "sections": sections, "total": total}
    except ExtToBlocked as exc:
        logger.warning("EXT.to fresh releases blocked: %s", exc)
        return {"configured": True, "live": False, "source": "EXT.to", "sections": {}, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive network boundary
        logger.warning("EXT.to fresh releases failed: %s", exc)
        return {"configured": True, "live": False, "source": "EXT.to", "sections": {}, "error": str(exc)}
