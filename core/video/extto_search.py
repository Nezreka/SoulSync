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
from bs4 import BeautifulSoup, Tag

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
    files: int | None = None
    age: str = ""
    magnet_id: str | None = None
    magnet: str | None = None


class ExtToBlocked(RuntimeError):
    pass


class FlareSolverrClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = timeout
        self.session_id = "soulsync-extto"
        # The clearance FlareSolverr solved, kept so a DIRECT fetch (image bytes,
        # which FlareSolverr cannot stream) can reuse it. See `clearance()`.
        self.last_cookies: dict[str, str] = {}
        self.last_user_agent: str = ""

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
            pass
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


def _soup(html: str) -> BeautifulSoup:
    """Parse ext.to markup. lxml is faster and more forgiving, but it is a compiled
    wheel we never declared, so on a plain `pip install -r requirements.txt` box
    BeautifulSoup raises FeatureNotFound and every EXT.to feature dies at the parse
    step. Fall back to the stdlib parser instead of taking the site down."""
    try:
        return BeautifulSoup(html or "", "lxml")
    except Exception:   # noqa: BLE001 - bs4 raises FeatureNotFound when lxml is absent
        return BeautifulSoup(html or "", "html.parser")


def _cell_value(td: "Tag | None", label: str | None = None) -> str:
    """One stat out of an ext.to results-table cell.

    The cells are ``<span class="add-block">Size</span><span>2.26 GB</span>`` pairs,
    so the LABEL is what identifies the value - column order differs between the
    homepage and the search page. Falls back to the cell's last non-label span.
    """
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
    soup = _soup(html)
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
        # Size/seeds/leechs live in the ROW's stat cells, not next to the link. This
        # used to read a.find_parent(), which is the title div ('<div class="float-left
        # has-movie">' — title, IMDb badge, uploader) and never contains a single stat,
        # so every search hit came back with no size and no swarm: shown as "Size
        # unknown / Health unknown", ranked at availability 0, and rejected by any
        # profile with a seeder floor. The search table is the SAME markup as the
        # homepage, so it reads through the same labelled-cell reader.
        row = a.find_parent("tr")
        cells = row.find_all("td", recursive=False) if row else []
        size_text = _cell_value(cells[1] if len(cells) > 1 else None, "Size")
        seeds_text = _cell_value(cells[4] if len(cells) > 4 else None, "Seeds")
        leech_text = _cell_value(cells[5] if len(cells) > 5 else None, "Leechs")
        files_text = _cell_value(cells[2] if len(cells) > 2 else None, "Files")
        age = _cell_value(cells[3] if len(cells) > 3 else None, "Age")
        if cells and not size_text:
            # a row that lays its columns out differently still labels them
            size_text = _cell_value(cells[0], "Size")
        if cells and not seeds_text:
            seeds_text = _cell_value(cells[0], "Seeds")
        if not size_text and not seeds_text:
            # No results TABLE (a mirror rendering cards, say) — scrape the widest
            # container we have. This is the original behaviour, kept as the fallback
            # rather than the primary: on ext.to itself it reads the title div, which
            # holds no stats at all.
            container = row or a.find_parent()
            row_text = _clean(container.get_text(" ")) if container else ""
            lower = row_text.lower()
            seeds = re.search(r"(?:seeders?|seeds?)\D+(\d[\d,]*)", lower)
            peers = re.search(r"(?:leechers?|peers?)\D+(\d[\d,]*)", lower)
            size = re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)\b", row_text, re.I)
            size_text = size.group(0) if size else ""
            seeds_text = seeds.group(1) if seeds else ""
            leech_text = peers.group(1) if peers else ""
        # The magnet button's data-id is the torrent id, carried so a grab can resolve
        # the magnet later without re-finding the row.
        btn = row.select_one("[data-id]") if row else None
        out.append(ExtToHit(title=title, url=url, size_text=size_text or None,
                            seeders=_int(seeds_text), leechers=_int(leech_text),
                            files=_int(files_text), age=age or "",
                            magnet_id=str(btn.get("data-id")) if btn and btn.get("data-id") else None))
        seen.add(url)
        if len(out) >= limit:
            break
    return out


def extract_magnet_ids(html: str) -> list[str]:
    soup = _soup(html)
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


def _json_body(resp: str) -> Any:
    """The JSON out of a FlareSolverr response, or the raw text if there is none.

    getTorrentMagnet.php answers with plain JSON, but FlareSolverr hands back the
    RENDERED DOM — so what arrives is Chrome's JSON viewer wrapping the payload in
    ``<html>...<pre>{...}</pre>...</html>``. json.loads fails on that, and the old
    fallback regex-scraped the magnet straight out of the markup: it stopped at the
    first space (magnets carry a `dn=` display name full of them) and left `\u0026`
    undecoded, yielding `magnet:?xt=urn:btih:<hash>\u0026dn=Interstellar` — the
    infohash intact but the name mangled and EVERY TRACKER DROPPED, which is a
    torrent that has to find its swarm on DHT alone.

    Unwrapping the <pre> and letting the JSON decoder do the unescaping gives the
    magnet exactly as ext.to's own page script uses it.
    """
    text = str(resp or "")
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S | re.I)
    if m:
        try:
            return json.loads(unescape(m.group(1)).strip())
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(unescape(text[start:end + 1]))
        except ValueError:
            pass
    return text


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
    return _magnet_from_payload(_json_body(resp))


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
        "file_count": hit.files or 0,
        "age": hit.age or "",
        "folder_size_bytes": _size_bytes(hit.size_text),
        "download_url": magnet,
        "magnet_uri": magnet,
        "protocol": "torrent",
        "info_url": hit.url,
        "magnet_id": hit.magnet_id,
        "indexer_id": "extto",
        "guid": guid or ("extto:" + str(hit.magnet_id or hit.url)),
    }


_clearance: dict = {"at": 0.0, "cookies": {}, "ua": ""}
CLEARANCE_TTL = 900          # seconds; a cf_clearance lasts far longer, this just re-solves lazily


def clearance(*, timeout: int = 45, flaresolverr: Optional[str] = None,
              max_age: int = CLEARANCE_TTL) -> tuple:
    """Cloudflare clearance cookies + the matching User-Agent for a DIRECT fetch.

    Needed for ext.to's poster art, which cannot be reached any other way:

      * the browser cannot load it, because ext.to sends
        ``Cross-Origin-Resource-Policy: same-origin`` (the console reports
        ERR_BLOCKED_BY_RESPONSE.NotSameOrigin on a perfectly valid URL), and
      * a plain server-side GET is refused 403 by Cloudflare, and
      * FlareSolverr returns a rendered DOM, so it cannot hand back image bytes.

    Reusing the clearance it already solved bridges that: same cookies, same UA,
    ordinary streaming GET. Cached in-process so viewing a board of posters costs
    one solve, not one per image. Returns ``({}, "")`` if it cannot be obtained -
    the caller then just fails to show a picture.
    """
    import time as _time
    now = _time.time()
    if _clearance["cookies"] and (now - _clearance["at"]) < max_age:
        return _clearance["cookies"], _clearance["ua"]
    solver = str(flaresolverr if flaresolverr is not None else flaresolverr_url()).rstrip("/")
    if not solver:
        return {}, ""
    try:
        client = FlareSolverrClient(solver, timeout=timeout)
        _fetch(client, BASES[0] + "/")
        if client.last_cookies:
            _clearance.update({"at": now, "cookies": client.last_cookies,
                               "ua": client.last_user_agent})
    except Exception:   # noqa: BLE001 - no clearance just means no picture
        logger.debug("EXT.to clearance solve failed", exc_info=True)
    return _clearance["cookies"], _clearance["ua"]


def resolve_magnet(info_url: Any, *, magnet_id: Any = None, timeout: int = 40,
                   flaresolverr: Optional[str] = None) -> dict:
    """Resolve ONE ext.to release's magnet from its detail page.

    Basic Search lists releases without magnets on purpose: the search page carries
    a per-row torrent id but NOT the window.pageToken / window.csrfToken the magnet
    endpoint is signed with, so every magnet costs its own detail-page fetch through
    Cloudflare. Resolving 25 of them to render a result list would take minutes, so
    the list stays link-less and the ONE release you actually pick is resolved here,
    at grab time.

    Returns ``{ok, magnet}`` or ``{ok: False, error}``. Never raises.
    """
    url = str(info_url or "").strip()
    solver = str(flaresolverr if flaresolverr is not None else flaresolverr_url()).rstrip("/")
    if not url:
        return {"ok": False, "error": "This release has no EXT.to page to resolve a magnet from."}
    if not solver:
        return {"ok": False, "error": "EXT.to requires FlareSolverr — set flaresolverr.url."}
    # Two passes. Every EXT.to call shares one FlareSolverr session id, and the magnet
    # endpoint answers {"success":false,"error":"Invalid session"} once that session's
    # cookies go stale — after which EVERY resolve fails until something re-establishes
    # it. A grab is a single deliberate click, so it must not lose to a stale cookie:
    # the second pass destroys the session and starts a clean browser.
    last = "EXT.to did not hand over a magnet for this release."
    for attempt in (1, 2):
        client = FlareSolverrClient(solver, timeout=timeout)
        try:
            if attempt == 2:
                client.close()          # drop the poisoned session, then solve fresh
            html = _fetch(client, url)
            ids = extract_magnet_ids(html)
            torrent_id = str(magnet_id) if magnet_id else (ids[0] if ids else None)
            magnet = fetch_magnet(client, url, html, torrent_id) if torrent_id else _direct_magnet(html)
            if magnet:
                return {"ok": True, "magnet": magnet}
            logger.info("EXT.to magnet resolve attempt %d gave nothing for %s", attempt, url)
        except ExtToBlocked as exc:
            logger.warning("EXT.to magnet resolve blocked: %s", exc)
            return {"ok": False, "error": str(exc)}      # a challenge won't fix itself on a retry
        except Exception as exc:   # noqa: BLE001 - network boundary; the grab reports it
            logger.warning("EXT.to magnet resolve attempt %d failed for %s: %s", attempt, url, exc)
            last = "EXT.to: " + str(exc)
    return {"ok": False, "error": last}


def extto_search(query: Any, *, limit: int = 20, timeout: int = 30,
                 flaresolverr: Optional[str] = None, resolve_magnets: bool = True,
                 max_candidates: int | None = None) -> dict:
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
        candidates = _candidate_urls(q)
        if max_candidates is not None:
            candidates = candidates[:max(1, int(max_candidates))]
        for candidate in candidates:
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
            if not resolve_magnets:
                out.append(_project(hit))
                continue
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
    "resolve_magnet", "clearance",
]
