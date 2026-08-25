"""Prowlarr-backed search for the VIDEO side — movies/TV via torrent + usenet indexers.

Best-in-class (Sonarr/Radarr-parity) query strategy: for each search we run BOTH

  1. a STRUCTURED Newznab search (``tvsearch`` / ``movie``) carrying season/ep +
     external ids (tvdb/imdb/tmdb) — the precise, id-aware form the *arr apps use;
     Prowlarr routes each hint to the indexers that support it, and
  2. the SCENE-FORMATTED free-text search ("Title SxxExx" / "Title Year") — which is
     often tighter than a structured query on public trackers that only do text.

Results are merged + deduped (by guid / download URL), then the shared ranker
(``_evaluate_hits`` → ``evaluate_release`` / scope validation) filters out anything
that doesn't actually match the requested movie / season / episode — so a broad
structured result set is cleaned up exactly like Sonarr cleans up its own.

MUSIC-SAFE BY CONSTRUCTION: only READS the shared ``prowlarr.*`` config and CALLS the
shared ``ProwlarrClient`` (via arguments it already accepts); never modifies a music
module. Hits are projected into the SAME shape ``core/video/slskd_search`` produces so
the ranking, UI, and grab path stay source-agnostic.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video.prowlarr_search")

# Newznab standard categories (Prowlarr uses these): Movies 2xxx, TV 5xxx.
_MOVIE_CATS = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060]
_TV_CATS = [5000, 5020, 5030, 5040, 5045, 5050, 5060]

_TV_SCOPES = ("episode", "season", "series", "show")


def _categories(scope: str) -> List[int]:
    return _TV_CATS if str(scope or "").lower() in _TV_SCOPES else _MOVIE_CATS


def _client():
    from core.prowlarr_client import ProwlarrClient
    return ProwlarrClient()


def is_configured() -> bool:
    """True when Prowlarr's URL + key are set (shared music config)."""
    try:
        return bool(_client().is_configured())
    except Exception:   # noqa: BLE001 - a config read never blocks the caller
        return False


def _indexer_ids() -> List[int]:
    """The optional Prowlarr indexer allowlist (shared ``prowlarr.indexer_ids``)."""
    from core.settings import config_manager
    raw = str(config_manager.get("prowlarr.indexer_ids", "") or "").strip()
    return [int(p) for p in (x.strip() for x in raw.split(",")) if p.isdigit()]


def _norm_indexer_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _indexer_ids_matching(names: Any, protocol: str) -> Optional[List[int]]:
    """Resolve a Basic Search provider label (e.g. 1337x) to Prowlarr indexer ids.

    ``None`` means no provider filter was requested. ``[]`` means a filter was
    requested but no enabled matching indexer exists, so callers should show a
    clear configured-but-missing message rather than widening the search.
    """
    if not names:
        return None
    wanted_raw = names if isinstance(names, (list, tuple, set)) else [names]
    wanted = [_norm_indexer_name(n) for n in wanted_raw if _norm_indexer_name(n)]
    if not wanted:
        return None
    try:
        known = _client()._get_indexers_sync()
    except Exception as exc:  # noqa: BLE001 - surface as no concrete source match
        logger.warning("Prowlarr indexer lookup failed for Basic Search provider %s: %s", wanted, exc)
        return []
    protocol = str(protocol or "torrent").lower()
    matched = []
    for indexer in known or []:
        if not getattr(indexer, "enable", False):
            continue
        if str(getattr(indexer, "protocol", "") or "").lower() != protocol:
            continue
        name = _norm_indexer_name(getattr(indexer, "name", ""))
        if any(w in name or name in w for w in wanted):
            matched.append(int(indexer.id))
    return matched


def _imdb_num(imdb_id: Any) -> Optional[str]:
    """Newznab wants the imdb id as digits, no ``tt`` prefix ('tt0111161' → '0111161')."""
    s = str(imdb_id or "").strip()
    if not s:
        return None
    m = re.match(r"^(?:tt)?(\d{6,9})$", s, re.IGNORECASE)
    return m.group(1) if m else None


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build_strategies(scope: str, title: Any, *, year: Any = None, season: Any = None,
                     episode: Any = None, imdb_id: Any = None, tmdb_id: Any = None,
                     tvdb_id: Any = None, air_date: Any = None, absolute: Any = None,
                     series_type: Any = None) -> List[Tuple[str, str, List[tuple]]]:
    """The set of Prowlarr searches to run for one request, as ``(type, query, extra)``.

    Pure (no I/O) so it's unit-tested. Always includes the scene-formatted text search;
    adds the structured tv/movie search (with whatever ids we have) so id-aware indexers
    resolve exactly. Identical strategies are collapsed."""
    from core.video.slskd_search import build_query
    t = str(title or "").strip()
    scope = str(scope or "movie").lower()
    imdb, tmdb, tvdb = _imdb_num(imdb_id), _as_int(tmdb_id), _as_int(tvdb_id)
    s_i, e_i = _as_int(season), _as_int(episode)
    strat: List[Tuple[str, str, List[tuple]]] = []

    if scope == "movie":
        extra: List[tuple] = []
        if year:
            extra.append(("year", year))
        if imdb:
            extra.append(("imdbid", imdb))
        if tmdb:
            extra.append(("tmdbid", tmdb))
        strat.append(("movie", t, extra))
        strat.append(("search", build_query("movie", t, year=year), []))
    elif scope == "episode":
        extra = []
        if s_i is not None:
            extra.append(("season", s_i))
        if e_i is not None:
            extra.append(("ep", e_i))
        if tvdb:
            extra.append(("tvdbid", tvdb))
        if imdb:
            extra.append(("imdbid", imdb))
        strat.append(("tvsearch", t, extra))
        # The text search speaks the scene's naming for this SERIES TYPE (P8):
        # daily → 'Title 2026.07.08', anime → 'Title 1071'. The plain SxxExx text
        # query stays as an extra strategy (some indexers normalize numbering).
        q_typed = build_query("episode", t, season=season, episode=episode,
                              air_date=air_date, absolute=absolute, series_type=series_type)
        strat.append(("search", q_typed, []))
        q_std = build_query("episode", t, season=season, episode=episode)
        if q_std != q_typed:
            strat.append(("search", q_std, []))
    elif scope == "season":
        extra = []
        if s_i is not None:
            extra.append(("season", s_i))
        if tvdb:
            extra.append(("tvdbid", tvdb))
        if imdb:
            extra.append(("imdbid", imdb))
        strat.append(("tvsearch", t, extra))
        strat.append(("search", build_query("season", t, season=season), []))
    else:   # series / whole show
        extra = []
        if tvdb:
            extra.append(("tvdbid", tvdb))
        if imdb:
            extra.append(("imdbid", imdb))
        strat.append(("tvsearch", t, extra))
        strat.append(("search", t, []))

    # Collapse identical (type, query, extra) — e.g. a movie with no year makes the
    # structured 'movie' query and the text 'search' query the same term.
    seen, out = set(), []
    for st_type, q, extra in strat:
        if not str(q or "").strip():
            continue
        keyv = (st_type, q, tuple(extra))
        if keyv in seen:
            continue
        seen.add(keyv)
        out.append((st_type, q, extra))
    return out


def _project(r: Any, url: str, want_proto: str) -> dict:
    """One Prowlarr result → the slskd-shaped hit ``_evaluate_hits`` consumes.

    ``magnet_uri`` rides along beside ``download_url`` (#1139, video side). The
    URL is now preferred so SoulSync can fetch the real .torrent server-side and
    push the file; the magnet is what ``add_torrent_smart`` falls back to when
    that fetch fails, so preferring the URL is never a downgrade."""
    size = int(getattr(r, "size", 0) or 0)
    seeders = getattr(r, "seeders", None)
    return {
        "title": r.title,
        "size_bytes": size,
        "seeders": seeders,
        "peers": getattr(r, "leechers", None),
        "username": getattr(r, "indexer_name", None) or "indexer",   # shown as the "source"
        "availability": (seeders if seeders is not None else (getattr(r, "grabs", 0) or 0)),
        "filename": r.title,                 # the grab uses the URL carriers below, not this
        "files": [], "file_count": 0, "folder_size_bytes": size,
        "download_url": url,
        "magnet_uri": getattr(r, "magnet_uri", None),
        "protocol": getattr(r, "protocol", want_proto),
        "indexer_id": getattr(r, "indexer_id", None),
        "guid": getattr(r, "guid", None),
        # Facts the indexer already stated and this projection used to drop on the
        # floor. An indexer cannot give a poster or a rating, but it CAN say how old
        # a release is and how many people have taken it - which is most of what you
        # judge a release on when there is no artwork to look at.
        "published_at": getattr(r, "publish_date", None),
        "grabs": getattr(r, "grabs", None),
        "info_url": getattr(r, "info_url", None),
    }


def prowlarr_search(scope: str, title: Any, *, year: Any = None, season: Any = None,
                    episode: Any = None, source: str = "torrent", imdb_id: Any = None,
                    tmdb_id: Any = None, tvdb_id: Any = None, air_date: Any = None,
                    absolute: Any = None, series_type: Any = None,
                    max_wait_seconds: Any = None, indexer_names: Any = None) -> dict:
    """Search Prowlarr for a video release with the multi-strategy (structured + text)
    approach. ``source`` picks the protocol to keep (``torrent`` | ``usenet``). Returns
    ``{configured, error?, hits:[...]}`` — the hit shape ``_evaluate_hits`` consumes.

    ``max_wait_seconds`` bounds how long this will sit in the shared Prowlarr
    budget (core.prowlarr_throttle) before giving up. Pass it from anything a
    person is waiting on: the background drain is happy to queue, a manual
    search should say "busy" rather than hold a request worker for a minute."""
    client = _client()
    if not client.is_configured():
        return {"configured": False, "hits": []}
    want_proto = "usenet" if str(source or "").lower() == "usenet" else "torrent"
    # Basic Search is intentionally old-school: when the UI targets a named public
    # tracker, do not send Radarr/Sonarr category filters. Some Jackett/Prowlarr
    # public tracker adapters either ignore or reject those categories, which made
    # obvious searches like "interstellar" return zero on The Pirate Bay.
    cats = [] if indexer_names else _categories(scope)
    target_ids = _indexer_ids_matching(indexer_names, want_proto)
    if target_ids == []:
        label = ", ".join(str(n) for n in (indexer_names if isinstance(indexer_names, (list, tuple, set)) else [indexer_names]))
        return {"configured": True, "error": "No enabled Prowlarr indexer matches " + label, "hits": []}
    ids = target_ids if target_ids is not None else _indexer_ids()
    strategies = build_strategies(scope, title, year=year, season=season, episode=episode,
                                  imdb_id=imdb_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id,
                                  air_date=air_date, absolute=absolute, series_type=series_type)
    if not strategies:
        return {"configured": True, "hits": []}

    def _run(strat):
        st_type, q, extra = strat
        try:
            return client._search_sync(q, cats, ids, 100, search_type=st_type,
                                       extra_params=extra,
                                       max_wait_seconds=max_wait_seconds)
        except Exception as e:   # noqa: BLE001 - one strategy failing shouldn't sink the rest
            logger.warning("prowlarr %s search failed for %r: %s", st_type, q, e)
            return e

    # The strategies are independent Prowlarr calls — fan them out so the extra recall
    # doesn't cost extra wall-clock (each is a blocking HTTP round-trip).
    with ThreadPoolExecutor(max_workers=min(4, len(strategies))) as ex:
        outcomes = list(ex.map(_run, strategies))

    hits: dict = {}          # dedupe key (guid or url) → projected hit; first wins
    errors: List[str] = []
    for res in outcomes:
        if isinstance(res, Exception):
            errors.append(str(res))
            continue
        for r in res:
            if getattr(r, "protocol", "") != want_proto:
                continue
            # .torrent URL first, magnet second (#1139): a magnet hands the
            # client an info-hash and nothing else, and one that cannot reach
            # the swarm parks on "downloading metadata" indefinitely.
            url = getattr(r, "download_url", None) or getattr(r, "magnet_uri", None)
            if not url:
                continue
            keyv = getattr(r, "guid", None) or url
            if keyv in hits:
                continue
            hits[keyv] = _project(r, url, want_proto)

    if not hits and errors:
        return {"configured": True, "error": errors[0], "hits": []}
    return {"configured": True, "hits": list(hits.values())}
