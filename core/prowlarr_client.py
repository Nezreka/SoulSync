"""Prowlarr client — indexer aggregator.

Prowlarr is the indexer manager component of the *arr stack. It exposes
configured Usenet / torrent indexers behind a single Newznab-style API
so downstream apps (Lidarr, Sonarr, Radarr, SoulSync) don't have to
implement an indexer integration per provider.

This client is NOT a download source plugin. It does not implement
``DownloadSourcePlugin`` — Prowlarr only *searches*. The torrent /
usenet download plugins (built in subsequent commits) own the
add-to-client / poll-status / extract flow and call this client for
the search step.

Surface:
- ``is_configured()`` — URL + API key present.
- ``check_connection()`` — hits ``/api/v1/system/status``.
- ``get_indexers()`` — list of configured indexers (id, name, protocol,
  capabilities).
- ``search(query, categories, indexer_ids)`` — Newznab search across
  selected indexers. Music categories default to the full audio tree.

Auth: ``X-Api-Key`` header. Found in Prowlarr → Settings → General →
Security → API Key.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests as http_requests

from core.settings import config_manager
from core.async_utils import run_blocking
from utils.logging_config import get_logger

logger = get_logger("prowlarr_client")


# Newznab Music category tree. Prowlarr / Jackett / Newznab indexers
# all agree on these numeric IDs. 3000 is the parent — most indexers
# tag releases against the parent OR a leaf; searching the parent
# pulls everything.
MUSIC_CATEGORY_ALL = 3000
MUSIC_CATEGORY_MP3 = 3010
MUSIC_CATEGORY_VIDEO = 3020
MUSIC_CATEGORY_AUDIOBOOK = 3030
MUSIC_CATEGORY_LOSSLESS = 3040
MUSIC_CATEGORY_OTHER = 3050
MUSIC_CATEGORY_FOREIGN = 3060

# dd28-34: 3060 (Audio/Foreign) is where many indexers file non-Latin-script
# releases (J-Pop, K-Pop, C-Pop, Bollywood).  Leaving it out made those
# releases structurally unfindable over Prowlarr while Soulseek — which has no
# category filter at all — kept finding them, so the gap read as "Usenet is
# broken for this artist".  Audiobook (3030) and Video (3020) stay out: those
# are genuinely different media, not a script/region distinction.
# Ceiling on a per-indexer fan-out (#1151). Deliberately well under the
# shared slow-I/O pool's 16 workers: this must never be the thing that
# starves every other provider call in the app.
MAX_CONCURRENT_INDEXER_SEARCHES = 6

# Prowlarr's indexer priority scale, and how long a listing of it is reused.
# The value only breaks ties between otherwise equal releases, so a few minutes
# of staleness costs nothing and a per-search listing call would cost a request
# to Prowlarr on every query.
DEFAULT_INDEXER_PRIORITY = 25
INDEXER_PRIORITY_CACHE_SECONDS = 300

# Module level, not per instance: the video side builds a fresh ProwlarrClient
# for every request, so an instance cache never hit there and each search paid
# for its own listing. The lock makes a cold cache single flight — six searches
# starting together used to issue six `/indexer` calls.
_INDEXER_PRIORITY_CACHE: Optional[Dict[int, int]] = None
_INDEXER_PRIORITY_CACHED_AT: float = 0.0
_INDEXER_PRIORITY_LOCK = threading.Lock()

DEFAULT_MUSIC_CATEGORIES: tuple = (
    MUSIC_CATEGORY_ALL,
    MUSIC_CATEGORY_MP3,
    MUSIC_CATEGORY_LOSSLESS,
    MUSIC_CATEGORY_OTHER,
    MUSIC_CATEGORY_FOREIGN,
)


def canonical_protocol(raw: Any) -> str:
    """Lowercase, stripped protocol name.

    Prowlarr answers 'Torrent' as readily as 'torrent'. Normalising once here,
    at the parse boundary, is what lets the rest of the codebase compare with a
    plain ``result.protocol != 'torrent'``. Without it the plugin helper (which
    compared case-insensitively) kept a capitalised release, ended the
    relaxed-query ladder on it, and the caller's case-sensitive filter then
    dropped it — a search that found hits returning nothing.
    """
    return str(raw or '').strip().lower()


def _coerce_priority(raw: Any) -> int:
    """Prowlarr's 1-50 priority, or the neutral default for anything else."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INDEXER_PRIORITY
    return value if value > 0 else DEFAULT_INDEXER_PRIORITY


@dataclass
class ProwlarrIndexer:
    """One configured indexer exposed by Prowlarr."""

    id: int
    name: str
    # Always lowercase — normalized in `_parse_indexer`, see `canonical_protocol`.
    protocol: str          # "torrent" | "usenet"
    enable: bool
    privacy: str           # "public" | "private" | "semiPrivate"
    # 1 (highest) to 50 (lowest), 25 by default — Prowlarr's own scale, and the
    # user's answer to "which of my indexers do I trust more".
    priority: int = DEFAULT_INDEXER_PRIORITY
    categories: List[int] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProwlarrSearchResult:
    """One release returned by a Prowlarr search.

    ``download_url`` is the link the torrent / usenet client gets fed.
    For torrent indexers it may be either a ``.torrent`` HTTP URL or
    a magnet URI (sometimes both — ``magnet_uri`` is set when the
    indexer exposes the magnet separately).
    """

    guid: str
    title: str
    indexer_id: int
    indexer_name: str
    # Always lowercase — normalized in `_parse_result`, see `canonical_protocol`.
    protocol: str           # "torrent" | "usenet"
    download_url: Optional[str] = None
    magnet_uri: Optional[str] = None
    info_url: Optional[str] = None
    size: int = 0           # bytes
    seeders: Optional[int] = None
    leechers: Optional[int] = None
    grabs: Optional[int] = None
    publish_date: Optional[str] = None
    categories: List[int] = field(default_factory=list)
    # Stamped from the indexer's own definition after the search, because the
    # search resource does not carry it. Neutral default for an indexer
    # Prowlarr did not list — an unknown indexer must not sort to the bottom.
    indexer_priority: int = DEFAULT_INDEXER_PRIORITY
    raw: Dict[str, Any] = field(default_factory=dict)


class ProwlarrSearchError(RuntimeError):
    """A Prowlarr search did not complete.

    dd28-02: without this, a timed-out or erroring search was indistinguishable
    from a genuine zero-hit search — both surfaced as an empty result list, so
    a source that failed *every* time still looked like "nothing matched".
    """


class ProwlarrClient:
    """Thin sync-backed async wrapper around the Prowlarr v1 API."""

    # Light metadata calls (system status, indexer list) answer immediately.
    DEFAULT_TIMEOUT = 15
    # A search fans out to every enabled indexer and waits for the slowest one,
    # so it needs its own, much larger budget (dd28-02). Soulseek gets 60s+15s
    # and the frontend allows 90s; matching that here stops Prowlarr from being
    # the one source that silently gives up first.
    DEFAULT_SEARCH_TIMEOUT = 75

    def __init__(self) -> None:
        self._load_config()

    def _load_config(self) -> None:
        self._url = (config_manager.get('prowlarr.url', '') or '').rstrip('/')
        self._api_key = config_manager.get('prowlarr.api_key', '') or ''

    def reload_settings(self) -> None:
        self._load_config()
        logger.info("Prowlarr settings reloaded")

    def is_configured(self) -> bool:
        return bool(self._url and self._api_key)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        return await run_blocking(self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        data = self._api_get('system/status')
        return bool(data and 'version' in data)

    async def get_indexers(self) -> List[ProwlarrIndexer]:
        if not self.is_configured():
            return []
        return await run_blocking(self._get_indexers_sync)

    def _get_indexers_sync(self) -> List[ProwlarrIndexer]:
        data = self._api_get('indexer')
        if not isinstance(data, list):
            return []
        return [self._parse_indexer(entry) for entry in data if isinstance(entry, dict)]

    def indexer_priorities(self) -> Dict[int, int]:
        """``{indexer id: priority}``, cached, and empty when unavailable.

        Best-effort by contract: the priority is only a tiebreaker, so a
        Prowlarr that cannot list its indexers must cost a search nothing. An
        empty map leaves every result on the neutral default.

        Never call this from inside a search deadline — see ``_search_sync``.
        """
        global _INDEXER_PRIORITY_CACHE, _INDEXER_PRIORITY_CACHED_AT

        def _fresh() -> Optional[Dict[int, int]]:
            if _INDEXER_PRIORITY_CACHE is None:
                return None
            age = time.monotonic() - _INDEXER_PRIORITY_CACHED_AT
            return _INDEXER_PRIORITY_CACHE if age < INDEXER_PRIORITY_CACHE_SECONDS else None

        cached = _fresh()
        if cached is not None:
            return cached
        with _INDEXER_PRIORITY_LOCK:
            # Another thread may have filled it while this one waited.
            cached = _fresh()
            if cached is not None:
                return cached
            try:
                known = self._get_indexers_sync()
                priorities = {indexer.id: indexer.priority for indexer in known}
            except Exception as exc:  # noqa: BLE001 - never fail a search on this
                logger.debug("Prowlarr indexer priority lookup failed: %s", exc)
                priorities = {}
            _INDEXER_PRIORITY_CACHE = priorities
            _INDEXER_PRIORITY_CACHED_AT = time.monotonic()
            return priorities

    def stamp_indexer_priorities(
        self, results: List[ProwlarrSearchResult],
    ) -> List[ProwlarrSearchResult]:
        """Attach each result's indexer priority, in place, best effort."""
        if not results:
            return results
        priorities = self.indexer_priorities()
        if priorities:
            for result in results:
                result.indexer_priority = priorities.get(
                    result.indexer_id, DEFAULT_INDEXER_PRIORITY,
                )
        return results

    def indexer_ids_for_protocol(
        self, configured_ids: Sequence[int], protocol: str,
    ) -> List[int]:
        """Narrow a configured indexer allowlist to one protocol (dd28-37).

        ``prowlarr.indexer_ids`` is a single shared setting, but the usenet and
        torrent plugins are separate sources.  Filled with torrent indexer IDs,
        it made the usenet plugin search torrent-only indexers forever — zero
        usenet results, no error, no way to tell from the UI.  Sending an
        allowlist a protocol cannot satisfy is never what the user meant, so
        such a request falls back to "every enabled indexer" and the protocol
        filter in the plugin's result projection does the rest.
        """
        wanted = [int(i) for i in configured_ids or []]
        if not wanted:
            return []
        protocol = canonical_protocol(protocol)
        try:
            known = self._get_indexers_sync()
        except Exception as exc:  # noqa: BLE001 - never block a search on this
            logger.debug("Prowlarr indexer lookup for protocol filtering failed: %s", exc)
            return wanted
        if not known:
            return wanted
        by_id = {indexer.id: indexer for indexer in known}
        matching = [
            i for i in wanted
            if i in by_id and by_id[i].protocol == protocol
        ]
        unknown = [i for i in wanted if i not in by_id]
        # Unknown IDs are kept: Prowlarr may simply not have listed them (a
        # transient API hiccup), and dropping them would silently widen the
        # user's allowlist.
        resolved = matching + unknown
        if not resolved:
            logger.warning(
                "prowlarr.indexer_ids (%s) contains no %s indexer — searching all "
                "enabled %s indexers instead of returning nothing",
                ", ".join(str(i) for i in wanted), protocol, protocol,
            )
            return []
        return resolved

    def resolve_search_timeout(self, timeout: Optional[int] = None) -> int:
        """Effective per-search budget in seconds.

        dd28-02: precedence is caller > the existing user setting
        ``download_source.source_search_timeout`` > this client's own default.
        The setting was already wired into HiFi/Qobuz/Deezer/stream search but
        never reached Prowlarr, so the 15s constant could not be raised by any
        configuration at all.
        """
        try:
            explicit = int(timeout) if timeout else 0
        except (TypeError, ValueError):
            explicit = 0
        if explicit > 0:
            return explicit
        try:
            configured = config_manager.get_source_search_timeout()
        except Exception:  # noqa: BLE001 - a config problem must not block search
            configured = None
        if configured:
            return int(configured)
        return self.DEFAULT_SEARCH_TIMEOUT

    async def search(
        self,
        query: str,
        categories: Sequence[int] = DEFAULT_MUSIC_CATEGORIES,
        indexer_ids: Optional[Sequence[int]] = None,
        limit: int = 100,
        search_type: str = "search",
        extra_params: Optional[Sequence[tuple]] = None,
        timeout: Optional[int] = None,
        throttle: bool = True,
    ) -> List[ProwlarrSearchResult]:
        """Run a Newznab search across the selected indexers.

        ``indexer_ids`` is the list of Prowlarr internal indexer IDs to
        query. ``None`` means all enabled indexers.

        ``search_type`` selects the Newznab search mode — ``search`` (generic
        free-text, the default), ``tvsearch`` or ``movie`` (structured). For the
        structured modes, ``extra_params`` carries the id/season/ep hints
        (``[('season', 3), ('ep', 4), ('tvdbid', 12345)]``); Prowlarr passes each
        to the indexers that advertise support for it and falls back to the text
        ``query`` on those that don't. Both are additive — the existing music
        callers keep the plain free-text behaviour.
        """
        if not self.is_configured() or not query.strip():
            return []
        results = await run_blocking(
            self._search_sync, query, list(categories), list(indexer_ids or []),
            limit, search_type, list(extra_params or []),
            self.resolve_search_timeout(timeout), None, throttle,
        )
        return await run_blocking(self.stamp_indexer_priorities, results)

    async def resolve_search_indexers(
        self, indexer_ids: Sequence[int], protocol: str,
    ) -> List[int]:
        """The CONCRETE indexer ids a search should be fanned out across.

        An empty ``indexer_ids`` means "every enabled indexer" — fine for one
        aggregated request, useless for a per-indexer fan-out, so it is
        expanded here against Prowlarr's own indexer list and narrowed to the
        protocol. Querying the torrent plugin's search against a usenet
        indexer would be a guaranteed zero.

        Returns ``[]`` when the list cannot be resolved. The caller must read
        that as "fall back to the single aggregated request", never as "search
        nothing" — a Prowlarr that cannot list its indexers can still search.
        """
        wanted = [int(i) for i in indexer_ids or []]
        if wanted:
            return wanted
        protocol = canonical_protocol(protocol)
        try:
            known = await self.get_indexers()
        except Exception as exc:                        # noqa: BLE001
            logger.debug("Prowlarr indexer enumeration failed, using one request: %s", exc)
            return []
        return [i.id for i in known
                if i.enable and (not protocol or canonical_protocol(i.protocol) == protocol)]

    async def search_each_indexer(
        self,
        query: str,
        indexer_ids: Sequence[int],
        categories: Sequence[int] = DEFAULT_MUSIC_CATEGORIES,
        limit: int = 100,
        search_type: str = "search",
        extra_params: Optional[Sequence[tuple]] = None,
        timeout: Optional[int] = None,
    ) -> Tuple[List[ProwlarrSearchResult], List[str]]:
        """Search each indexer SEPARATELY and concurrently (#1151).

        Prowlarr's search endpoint takes every indexer id in one request and
        answers once, so a single slow or unreachable indexer holds the whole
        response past the read timeout — and a timed-out request yields
        NOTHING, discarding results the responsive indexers had already
        produced (Zombiehamser). One request per indexer means a failure costs
        only its own slot.

        Wall time is still the slowest indexer, deliberately: waiting is the
        price of never dropping a result that was going to arrive.

        Returns ``(results, failures)``. ``failures`` are human-readable and
        name the indexer, which the aggregated request structurally could not
        do — the report asks for exactly that.
        """
        # Deduped, order preserved. The allowlist is free text
        # (`prowlarr.indexer_ids`) and is not deduped upstream — harmless when
        # every id went into ONE request, but here a repeat means a second
        # request to the same indexer and its results merged in twice.
        ids: List[int] = []
        for raw in indexer_ids or []:
            value = int(raw)
            if value not in ids:
                ids.append(value)
        if not ids:
            return [], []

        # Bounded, because `search` runs on the SHARED slow-I/O thread pool
        # (16 workers, used by every provider in the app). An unbounded fan-out
        # would hold one worker per indexer for the whole timeout — starving
        # Spotify/Deezer/enrichment — and, once the pool is full, the requests
        # would QUEUE rather than run together, making this slower than the
        # single aggregated request it replaces. A responsive indexer frees its
        # slot in seconds, so the realistic cost of the cap is nil.
        gate = asyncio.Semaphore(MAX_CONCURRENT_INDEXER_SEARCHES)

        # ONE slot for the whole wave, not one per request. Each indexer gets
        # exactly one query out of this, which is what the budget is protecting;
        # billing per HTTP request would charge a single album search a slot per
        # indexer and leave somebody staring at a spinner for twenty seconds.
        # Reserved off-loop because the reservation sleeps.
        from core.prowlarr_throttle import wait_for_slot
        await run_blocking(wait_for_slot, None)

        async def _one(indexer_id: int):
            async with gate:
                return await self.search(
                    query, categories=categories, indexer_ids=[indexer_id],
                    limit=limit, search_type=search_type, extra_params=extra_params,
                    timeout=timeout, throttle=False,
                )

        budget = self.resolve_search_timeout(timeout)
        tasks = {asyncio.ensure_future(_one(i)): i for i in ids}

        # ONE overall deadline, the same budget the single aggregated request
        # had. Without it the fan-out is slower than what it replaces whenever
        # there are more indexers than the concurrency cap: batches run back to
        # back, so N dead indexers cost ceil(N/cap) x timeout instead of one.
        # `asyncio.wait` returns rather than raising, so whatever finished in
        # the budget is kept — which is the entire point of the change.
        done, pending = await asyncio.wait(set(tasks), timeout=budget)

        for task in pending:
            task.cancel()
            # Reap quietly. The task is abandoned, not awaited — awaiting would
            # wait out the very timeout the deadline exists to avoid — so a
            # late result or error would otherwise surface as an "exception was
            # never retrieved" warning. (The executor thread itself cannot be
            # cancelled; it ends at its own request timeout, which is this same
            # budget.)
            task.add_done_callback(lambda t: t.cancelled() or t.exception())

        results: List[ProwlarrSearchResult] = []
        failures: List[str] = []
        for task, indexer_id in tasks.items():
            if task in pending:
                failures.append(f"indexer {indexer_id}: no answer within {budget}s")
                continue
            if task.cancelled():
                # Cancelled from OUTSIDE (shutdown), not by our deadline —
                # those are in `pending`. Propagate rather than reporting a
                # phantom indexer failure.
                raise asyncio.CancelledError()
            error = task.exception()
            if error is not None:
                failures.append(f"indexer {indexer_id}: {error}")
                continue
            results.extend(task.result())
        # Outside the per-task deadline on purpose, see _search_sync.
        await run_blocking(self.stamp_indexer_priorities, results)
        return results, failures

    def _search_sync(
        self,
        query: str,
        categories: List[int],
        indexer_ids: List[int],
        limit: int,
        search_type: str = "search",
        extra_params: Optional[Sequence[tuple]] = None,
        timeout: Optional[int] = None,
        max_wait_seconds: Optional[float] = None,
        throttle: bool = True,
    ) -> List[ProwlarrSearchResult]:
        # Every Prowlarr search in the app funnels through here: the async
        # `search`, the per-indexer fan-out that calls it, and the video side
        # calling this directly. So this is where the pacing goes.
        #
        # Prowlarr hands each search straight to your indexers and shields them
        # from nothing. Nothing else in this app talks to a third party
        # unthrottled; this was the exception, on both sides.
        # `throttle=False` when the CALLER already reserved for this wave; see
        # search_each_indexer. The budget counts hits on an indexer, and a
        # fan-out gives each one exactly one, so charging it per HTTP request
        # would bill a single album search ten slots and add twenty seconds to
        # somebody waiting on a page.
        if throttle:
            from core.prowlarr_throttle import wait_for_slot
            if not wait_for_slot(max_wait_seconds=max_wait_seconds):
                # Only an interactive caller passes a bound, and only it can be
                # refused. Saying so beats holding a request worker for a minute
                # while a wishlist drain empties the window.
                raise ProwlarrSearchError(
                    "Prowlarr searches are rate limited right now — try again shortly"
                )

        # Prowlarr's search endpoint accepts repeated params: ``categories=3000&categories=3010``.
        # ``requests`` serializes lists in that exact form when passed as tuples of pairs.
        params: List[tuple] = [('query', query), ('type', search_type or 'search'), ('limit', limit)]
        for cat in categories:
            params.append(('categories', cat))
        for indexer_id in indexer_ids:
            params.append(('indexerIds', indexer_id))
        for key, value in (extra_params or []):
            if value is not None and value != '':
                params.append((key, value))

        data = self._api_get(
            'search', params=params,
            timeout=timeout or self.DEFAULT_SEARCH_TIMEOUT,
            raise_on_error=True,
        )
        if not isinstance(data, list):
            return []
        # Deliberately no priority lookup here. This runs inside the
        # per-indexer fan-out's deadline, where an extra synchronous request
        # does not merely cost time: it can push an already-finished search
        # past the budget, and those results are then discarded. The async
        # wrappers stamp priorities afterwards, outside the deadline; the
        # video side calls this directly and never reads the field.
        return [self._parse_result(entry) for entry in data if isinstance(entry, dict)]

    def _parse_indexer(self, entry: Dict[str, Any]) -> ProwlarrIndexer:
        return ProwlarrIndexer(
            id=int(entry.get('id') or 0),
            name=entry.get('name') or '',
            protocol=canonical_protocol(entry.get('protocol')),
            enable=bool(entry.get('enable', True)),
            privacy=entry.get('privacy') or '',
            priority=_coerce_priority(entry.get('priority')),
            categories=[int(c.get('id') or 0) for c in entry.get('capabilities', {}).get('categories', []) if isinstance(c, dict)],
            capabilities=entry.get('capabilities', {}) or {},
        )

    def _parse_result(self, entry: Dict[str, Any]) -> ProwlarrSearchResult:
        cats = entry.get('categories') or []
        category_ids: List[int] = []
        for cat in cats:
            if isinstance(cat, dict) and cat.get('id') is not None:
                try:
                    category_ids.append(int(cat['id']))
                except (TypeError, ValueError):
                    continue
            elif isinstance(cat, int):
                category_ids.append(cat)

        return ProwlarrSearchResult(
            guid=str(entry.get('guid') or entry.get('infoUrl') or entry.get('downloadUrl') or ''),
            title=entry.get('title') or '',
            indexer_id=int(entry.get('indexerId') or 0),
            indexer_name=entry.get('indexer') or '',
            protocol=canonical_protocol(entry.get('protocol')),
            download_url=entry.get('downloadUrl') or None,
            magnet_uri=entry.get('magnetUrl') or None,
            info_url=entry.get('infoUrl') or None,
            size=int(entry.get('size') or 0),
            seeders=entry.get('seeders'),
            leechers=entry.get('leechers'),
            grabs=entry.get('grabs'),
            publish_date=entry.get('publishDate'),
            categories=category_ids,
            raw=entry,
        )

    def _api_get(
        self,
        path: str,
        params=None,
        timeout: Optional[int] = None,
        raise_on_error: bool = False,
    ) -> Optional[Any]:
        """GET one Prowlarr endpoint.

        ``raise_on_error`` makes transport/HTTP/JSON failures raise
        :class:`ProwlarrSearchError` instead of returning ``None`` (dd28-02).
        The search path needs that distinction; the metadata endpoints keep
        their best-effort ``None`` behaviour.
        """
        if not self.is_configured():
            if raise_on_error:
                raise ProwlarrSearchError("Prowlarr is not configured")
            return None
        url = f"{self._url}/api/v1/{path.lstrip('/')}"
        try:
            resp = http_requests.get(
                url,
                headers={'X-Api-Key': self._api_key, 'Accept': 'application/json'},
                params=params,
                timeout=timeout or self.DEFAULT_TIMEOUT,
            )
            # getattr, not resp.status_code: this runs before the `resp.ok`
            # check below, so it is the FIRST thing to touch the response, and a
            # stub or an adapter that only implements `.ok` used to get this far
            # untouched. Reading an attribute nothing promised broke a test that
            # had every right to pass.
            if getattr(resp, 'status_code', None) == 429:
                # Prowlarr passes an indexer's rate limit back as a 429. Tell the
                # shared budget so BOTH sides back off, instead of the other half
                # of the app walking into the same wall a second later.
                try:
                    from core.prowlarr_throttle import note_rate_limited
                    note_rate_limited(resp.headers.get('Retry-After'))
                except Exception:  # noqa: BLE001 - never let bookkeeping sink a request
                    logger.debug("Prowlarr 429 cooldown could not be recorded", exc_info=True)
            if not resp.ok:
                logger.warning("Prowlarr %s returned HTTP %s", path, resp.status_code)
                if raise_on_error:
                    raise ProwlarrSearchError(
                        f"Prowlarr returned HTTP {resp.status_code}"
                    )
                return None
            return resp.json()
        except http_requests.exceptions.Timeout as e:
            logger.error("Prowlarr request to %s timed out: %s", path, e)
            if raise_on_error:
                raise ProwlarrSearchError(
                    f"Prowlarr did not answer within {timeout or self.DEFAULT_TIMEOUT}s"
                ) from e
            return None
        except http_requests.exceptions.RequestException as e:
            logger.error("Prowlarr request to %s failed: %s", path, e)
            if raise_on_error:
                raise ProwlarrSearchError(f"Prowlarr request failed: {e}") from e
            return None
        except ValueError as e:
            logger.error("Prowlarr response to %s was not JSON: %s", path, e)
            if raise_on_error:
                raise ProwlarrSearchError("Prowlarr returned a malformed response") from e
            return None
