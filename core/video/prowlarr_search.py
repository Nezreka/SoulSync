"""Prowlarr-backed search for the VIDEO side — movies/TV via torrent + usenet indexers.

MUSIC-SAFE BY CONSTRUCTION: this module only READS the shared ``prowlarr.*`` config and CALLS
the shared ``core.prowlarr_client.ProwlarrClient`` (passing video Newznab categories via the
argument the client already accepts). It never modifies any music-side module. Results are
projected into the SAME hit shape ``core/video/slskd_search`` produces, so the ranking
(``_evaluate_hits``), the download UI, and the grab path stay source-agnostic — the torrent
magnet / NZB URL rides on the hit so the grab can hand it to the shared torrent/usenet client.
"""

from __future__ import annotations

from typing import Any, List

from utils.logging_config import get_logger

logger = get_logger("video.prowlarr_search")

# Newznab standard categories (Prowlarr uses these): Movies 2xxx, TV 5xxx.
_MOVIE_CATS = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060]
_TV_CATS = [5000, 5020, 5030, 5040, 5045, 5050, 5060]


def _categories(scope: str) -> List[int]:
    return _TV_CATS if str(scope or "").lower() in ("episode", "season", "series", "show") else _MOVIE_CATS


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
    from config.settings import config_manager
    raw = str(config_manager.get("prowlarr.indexer_ids", "") or "").strip()
    return [int(p) for p in (x.strip() for x in raw.split(",")) if p.isdigit()]


def prowlarr_search(scope: str, title: Any, *, year: Any = None, season: Any = None,
                    episode: Any = None, source: str = "torrent") -> dict:
    """Search Prowlarr for a video release. ``source`` picks the protocol to keep
    (``torrent`` | ``usenet``). Returns ``{configured, error?, hits:[...]}`` — the hit
    shape ``_evaluate_hits`` consumes, plus the download-URL carriers the grab needs."""
    from core.video.slskd_search import build_query
    client = _client()
    if not client.is_configured():
        return {"configured": False, "hits": []}
    q = build_query(scope, title, year=year, season=season, episode=episode)
    want_proto = "usenet" if str(source or "").lower() == "usenet" else "torrent"
    try:
        results = client._search_sync(q, _categories(scope), _indexer_ids(), 100)
    except Exception as e:   # noqa: BLE001 - surface the indexer error to the UI, don't crash
        logger.warning("prowlarr video search failed: %s", e, exc_info=True)
        return {"configured": True, "error": str(e), "hits": []}

    hits = []
    for r in results:
        if getattr(r, "protocol", "") != want_proto:
            continue
        url = getattr(r, "magnet_uri", None) or getattr(r, "download_url", None)
        if not url:
            continue
        size = int(getattr(r, "size", 0) or 0)
        seeders = getattr(r, "seeders", None)
        hits.append({
            "title": r.title,
            "size_bytes": size,
            "seeders": seeders,
            "peers": getattr(r, "leechers", None),
            "username": getattr(r, "indexer_name", None) or "indexer",   # shown as the "source"
            # availability ranks within a quality tier: torrent → seeders, usenet → grabs.
            "availability": (seeders if seeders is not None else (getattr(r, "grabs", 0) or 0)),
            "filename": r.title,                 # the grab uses the URL carriers below, not this
            "files": [], "file_count": 0, "folder_size_bytes": size,
            # torrent/usenet grab carriers (passed through by _evaluate_hits):
            "download_url": url,
            "protocol": getattr(r, "protocol", want_proto),
            "indexer_id": getattr(r, "indexer_id", None),
            "guid": getattr(r, "guid", None),
        })
    return {"configured": True, "hits": hits}
