"""free sources for what an artist page used to get from Last.fm alone.

last.fm keys need a pro subscription from december 2026, so most instances
lose the bio, tags and listener numbers. this fills those three fields from
free sources, only for the fields last.fm has nothing for:

- bio: theaudiodb's biography, else the english wikipedia intro, found through
  the artist's musicbrainz id (musicbrainz url relations, then wikidata)
- tags: musicbrainz genres, else musicbrainz tags
- popularity: listenbrainz listeners and listens, and deezer fans

every source writes its own bucket in ``lib2_artists.enrichment``, never the
``lastfm`` one, so stored last.fm data is never touched and wins again the
moment last.fm works. what was asked and when sits in ``about_fallback``, and
nothing is asked again for 30 days (``artist_about.fallback_due``).

the last.fm worker drives this: right after last.fm had nothing for an artist,
and as a sweep whenever it has no last.fm work (no key, or the queue is empty).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, Optional, Set, Tuple
from urllib.parse import quote, unquote

from core.library2.artist_about import (
    CHECK_KEY, check_stamp, fallback_due, load_enrichment,
)
from utils.logging_config import get_logger

logger = get_logger("lastfm_fallback")

USER_AGENT = "SoulSync/2 (https://github.com/Nezreka/SoulSync)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
LISTENBRAINZ_POPULARITY = "https://api.listenbrainz.org/1/popularity/artist"

# wikimedia and listenbrainz have no client in the repo. one request a second
# each, well under what either asks of a polite client
_MIN_INTERVAL = 1.0
_last_call: Dict[str, float] = {}
_call_lock = threading.Lock()


class SourceError(Exception):
    """a source failed (network, 5xx, 429). not the same as having nothing:
    the ask is not stamped, so it is tried again on the next sweep."""


def _throttle(host: str) -> None:
    with _call_lock:
        wait = _last_call.get(host, 0.0) + _MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()


def _http_json(method: str, url: str, *, params=None, json_body=None,
               host: str = "") -> Optional[Any]:
    """json, or None on a 404. anything else that is not a 200 raises
    SourceError."""
    import requests

    _throttle(host or url.split("/")[2])
    try:
        response = requests.request(
            method, url, params=params, json=json_body, timeout=10,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    except requests.RequestException as exc:
        raise SourceError(str(exc)) from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SourceError(f"{url} answered {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"{url} sent no json") from exc


# ── musicbrainz ──────────────────────────────────────────────────────────────

def _ranked(items: Any, *, need_votes: bool) -> list:
    rows = [i for i in (items or []) if isinstance(i, dict) and i.get("name")]
    if need_votes:
        rows = [i for i in rows if int(i.get("count") or 0) > 0]
    rows.sort(key=lambda i: -int(i.get("count") or 0))
    return [str(i["name"]) for i in rows[:10]]


_WIKIDATA_ID = re.compile(r"wikidata\.org/wiki/(Q\d+)", re.I)
_ENWIKI = re.compile(r"//en\.wikipedia\.org/wiki/([^?#]+)", re.I)


def wiki_links(mb_artist: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(english wikipedia title, wikidata id) from an artist's url relations."""
    title = qid = None
    for rel in mb_artist.get("relations") or []:
        resource = str(((rel or {}).get("url") or {}).get("resource") or "")
        found = _ENWIKI.search(resource)
        if found and not title:
            title = unquote(found.group(1)).replace("_", " ")
        found = _WIKIDATA_ID.search(resource)
        if found and not qid:
            qid = found.group(1)
    return title, qid


# ── the fetcher ──────────────────────────────────────────────────────────────

class ArtistAboutFallback:
    """asks the free sources for one artist. clients are injectable so tests
    never touch the network."""

    def __init__(self, *, mb_client=None, audiodb_client=None,
                 deezer_client=None, http: Optional[Callable[..., Any]] = None):
        self._mb = mb_client
        self._audiodb = audiodb_client
        self._deezer = deezer_client
        self._http = http or _http_json

    # lazily built: the worker imports this module whether or not it runs
    def mb(self):
        if self._mb is None:
            from core.musicbrainz_client import MusicBrainzClient
            self._mb = MusicBrainzClient("SoulSync", "2")
        return self._mb

    def audiodb(self):
        if self._audiodb is None:
            from core.audiodb_client import AudioDBClient
            self._audiodb = AudioDBClient()
        return self._audiodb

    def deezer(self):
        if self._deezer is None:
            from core.deezer_client import DeezerClient
            self._deezer = DeezerClient()
        return self._deezer

    def _mb_artist(self, mbid: str) -> Optional[Dict[str, Any]]:
        from core.http_error_status import http_error_status
        try:
            return self.mb().get_artist(
                mbid, includes=["genres", "tags", "url-rels"], raise_on_error=True)
        except Exception as exc:  # noqa: BLE001
            if http_error_status(exc) == 404:
                return None
            raise SourceError(f"musicbrainz: {exc}") from exc

    def _audiodb_bio(self, audiodb_id: Optional[str], mbid: Optional[str]) -> Optional[str]:
        client = self.audiodb()
        data = None
        if audiodb_id:
            data = client.lookup_artist_by_id(str(audiodb_id))
        if not data and mbid:
            # exact by mbid: it cannot land on a namesake
            data = client.lookup_artist_by_mbid(str(mbid))
        bio = (data or {}).get("strBiographyEN")
        return bio.strip() if isinstance(bio, str) and bio.strip() else None

    def _wikipedia(self, title: Optional[str], qid: Optional[str]) -> Optional[Dict[str, Any]]:
        if not title and qid:
            data = self._http("GET", WIKIDATA_API, params={
                "action": "wbgetentities", "ids": qid, "props": "sitelinks",
                "sitefilter": "enwiki", "format": "json"}, host="www.wikidata.org")
            entity = (((data or {}).get("entities") or {}).get(qid) or {})
            title = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title")
        if not title:
            return None
        page = self._http("GET", WIKIPEDIA_SUMMARY + quote(title.replace(" ", "_"), safe=""),
                          host="en.wikipedia.org")
        if not page or page.get("type") == "disambiguation":
            return None
        extract = str(page.get("extract") or "").strip()
        if not extract:
            return None
        url = (((page.get("content_urls") or {}).get("desktop") or {}).get("page")
               or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}")
        return {"bio": extract, "url": url, "title": page.get("title") or title}

    def _listenbrainz(self, mbid: str) -> Optional[Dict[str, Any]]:
        data = self._http("POST", LISTENBRAINZ_POPULARITY,
                          json_body={"artist_mbids": [mbid]}, host="api.listenbrainz.org")
        rows = data.get("payload") if isinstance(data, dict) else data
        for row in rows or []:
            if not isinstance(row, dict) or str(row.get("artist_mbid") or "") != mbid:
                continue
            out = {"listeners": row.get("total_user_count"),
                   "listens": row.get("total_listen_count")}
            out = {k: int(v) for k, v in out.items() if isinstance(v, (int, float)) and v > 0}
            return out or None
        return None

    def _deezer_fans(self, deezer_id: str) -> Optional[int]:
        info = self.deezer().get_artist_info(str(deezer_id)) or {}
        fans = (info.get("followers") or {}).get("total")
        if not fans:
            fans = (info.get("_raw_data") or {}).get("nb_fan")
        try:
            fans = int(fans)
        except (TypeError, ValueError):
            return None
        return fans if fans > 0 else None

    def fetch(self, *, needs: Set[str], mbid: Optional[str] = None,
              audiodb_id: Optional[str] = None,
              deezer_id: Optional[str] = None) -> Tuple[Dict[str, Dict[str, Any]], bool]:
        """(buckets to write, whether any source failed).

        each source is its own try: one being down costs that one field. a
        source that answered with nothing writes nothing, so a miss never
        blanks what an earlier ask stored.
        """
        buckets: Dict[str, Dict[str, Any]] = {}
        failed = False

        mb_artist = None
        if mbid and needs & {"bio", "tags"}:
            try:
                mb_artist = self._mb_artist(mbid)
            except SourceError as exc:
                logger.debug("musicbrainz lookup failed for %s: %s", mbid, exc)
                failed = True

        if "tags" in needs and mb_artist:
            genres = _ranked(mb_artist.get("genres"), need_votes=False)
            tags = _ranked(mb_artist.get("tags"), need_votes=True)
            if genres or tags:
                buckets["musicbrainz"] = {"genres": genres or None, "tags": tags or None}

        if "bio" in needs:
            bio = None
            if audiodb_id or mbid:
                try:
                    bio = self._audiodb_bio(audiodb_id, mbid)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("theaudiodb bio failed: %s", exc)
                    failed = True
            if bio:
                buckets["audiodb"] = {"bio": bio}
            elif mb_artist:
                try:
                    page = self._wikipedia(*wiki_links(mb_artist))
                except SourceError as exc:
                    logger.debug("wikipedia lookup failed for %s: %s", mbid, exc)
                    page, failed = None, True
                if page:
                    buckets["wikipedia"] = page

        if "popularity" in needs:
            if mbid:
                try:
                    counts = self._listenbrainz(mbid)
                except SourceError as exc:
                    logger.debug("listenbrainz popularity failed for %s: %s", mbid, exc)
                    counts, failed = None, True
                if counts:
                    buckets["listenbrainz"] = counts
            if deezer_id:
                try:
                    fans = self._deezer_fans(deezer_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("deezer fans failed for %s: %s", deezer_id, exc)
                    fans, failed = None, True
                if fans:
                    buckets["deezer"] = {"fans": fans}
        return buckets, failed


# ── reading and writing the catalogue ────────────────────────────────────────

_COLUMNS = "e.id, e.name, e.musicbrainz_id, e.external_ids, e.enrichment"


def _sweep_filter() -> str:
    """the artists the enrichment workers look at: owned, canonical, and not
    locked by a review (same rules as core.library2.worker_queue)."""
    from core.library2.locks import unlocked_sql
    from core.library2.sql_util import ANY_OWNER, owned_sql
    return (f"e.canonical_artist_id IS NULL "
            f"AND {owned_sql('artist', 'e', scope=ANY_OWNER)} "
            f"AND {unlocked_sql('artist', 'e', 'metadata')}")


def _ids(row) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    from core.library2.provider_ids import parse_external_ids
    ids = parse_external_ids(row["external_ids"])
    mbid = row["musicbrainz_id"] or ids.get("musicbrainz")
    return (str(mbid) if mbid else None, ids.get("audiodb"), ids.get("deezer"))


def _due(row) -> Set[str]:
    mbid, _audiodb, _deezer = _ids(row)
    return fallback_due(load_enrichment(row["enrichment"]), mbid)


def next_due_artist(conn, after_id: int, *, batch: int = 500,
                    max_batches: int = 20):
    """(row, cursor) for the next artist past ``after_id`` whose fallbacks are
    due. row is None with cursor None at the end of the table; row None with a
    cursor means "scanned this far, nothing due yet". walks the id index in
    batches; the due check is python because it reads the json."""
    cursor = int(after_id or 0)
    where = _sweep_filter()
    for _ in range(max_batches):
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM lib2_artists e WHERE e.id > ? AND {where} "
            f"ORDER BY e.id LIMIT ?", (cursor, batch)).fetchall()
        if not rows:
            return None, None
        for row in rows:
            if _due(row):
                return row, int(row["id"])
        cursor = int(rows[-1]["id"])
    return None, cursor


def load_artist(conn, artist_id: int):
    return conn.execute(
        f"SELECT {_COLUMNS} FROM lib2_artists e WHERE e.id = ? AND {_sweep_filter()}",
        (int(artist_id),)).fetchone()


def fill_artist(connect: Callable[[], Any], artist_id: int,
                fetcher: Optional[ArtistAboutFallback] = None) -> bool:
    """ask the free sources for one artist if its fallbacks are due, and
    store what came back. returns whether anything was asked.

    the network calls run with no connection open; the row is read again
    before writing, so last.fm data that landed meanwhile still wins.
    """
    from core.library2.provider_writes import write_provider_enrichment

    conn = connect()
    try:
        row = load_artist(conn, artist_id)
        if row is None:
            return False
        needs = _due(row)
        if not needs:
            return False
        mbid, audiodb_id, deezer_id = _ids(row)
    finally:
        conn.close()

    buckets, failed = (fetcher or ArtistAboutFallback()).fetch(
        needs=needs, mbid=mbid, audiodb_id=audiodb_id, deezer_id=deezer_id)

    conn = connect()
    try:
        row = load_artist(conn, artist_id)
        if row is None:
            return True
        for service, payload in buckets.items():
            if service == "lastfm":  # never, whatever a fetcher says
                continue
            write_provider_enrichment(conn, entity_type="artist", entity_id=int(artist_id),
                                      service=service, payload=payload)
        if not failed:
            write_provider_enrichment(conn, entity_type="artist", entity_id=int(artist_id),
                                      service=CHECK_KEY, payload=check_stamp(mbid))
        conn.commit()
    finally:
        conn.close()
    if buckets:
        logger.debug("artist #%s filled from %s", artist_id, ", ".join(sorted(buckets)))
    return True


__all__ = [
    "ArtistAboutFallback", "SourceError", "fill_artist", "load_artist",
    "next_due_artist", "wiki_links",
]
