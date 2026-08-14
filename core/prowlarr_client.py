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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

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


@dataclass
class ProwlarrIndexer:
    """One configured indexer exposed by Prowlarr."""

    id: int
    name: str
    # Always lowercase — normalized in `_parse_indexer`, see `canonical_protocol`.
    protocol: str          # "torrent" | "usenet"
    enable: bool
    privacy: str           # "public" | "private" | "semiPrivate"
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
        return await run_blocking(
            self._search_sync, query, list(categories), list(indexer_ids or []),
            limit, search_type, list(extra_params or []),
            self.resolve_search_timeout(timeout),
        )

    def _search_sync(
        self,
        query: str,
        categories: List[int],
        indexer_ids: List[int],
        limit: int,
        search_type: str = "search",
        extra_params: Optional[Sequence[tuple]] = None,
        timeout: Optional[int] = None,
    ) -> List[ProwlarrSearchResult]:
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
        return [self._parse_result(entry) for entry in data if isinstance(entry, dict)]

    def _parse_indexer(self, entry: Dict[str, Any]) -> ProwlarrIndexer:
        return ProwlarrIndexer(
            id=int(entry.get('id') or 0),
            name=entry.get('name') or '',
            protocol=canonical_protocol(entry.get('protocol')),
            enable=bool(entry.get('enable', True)),
            privacy=entry.get('privacy') or '',
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
