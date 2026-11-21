"""a studio or a network by name, as tmdb knows it: its id, its card (logo,
about, where it's from), and everything it made, through the video side's
own discover.

the library stores studios and networks by name only. a studio's id comes
from tmdb's company search (the exact name first); a network has no search
on tmdb, so its id comes from the detail of a show you own that airs on it.
ids are kept for the life of the process: they never change.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("player.makers")

_ids: Dict[tuple, Optional[int]] = {}
_cards: Dict[tuple, Optional[dict]] = {}


def _norm(name: str) -> str:
    return " ".join((name or "").casefold().split())


def pick_company(results: Iterable[dict], name: str) -> Optional[int]:
    """the exact name if tmdb has it, else its first answer"""
    results = [r for r in results or [] if r.get("tmdb_id")]
    for r in results:
        if _norm(r.get("title") or "") == _norm(name):
            return int(r["tmdb_id"])
    return int(results[0]["tmdb_id"]) if results else None


def pick_network(networks: Iterable[dict], name: str) -> Optional[int]:
    for n in networks or []:
        if n.get("id") and _norm(n.get("name") or "") == _norm(name):
            return int(n["id"])
    return None


def _tmdb(engine):
    w = engine.workers.get("tmdb")
    return w.client if w and w.enabled else None


def studio_id(engine, name: str) -> Optional[int]:
    key = ("studio", _norm(name))
    if key not in _ids:
        _ids[key] = pick_company(engine.company_search(name) or [], name)
    return _ids[key]


def network_id(engine, name: str, show_tmdb_ids: Iterable[int]) -> Optional[int]:
    key = ("network", _norm(name))
    if key in _ids:
        return _ids[key]
    client = _tmdb(engine)
    if client is None:
        return None
    import requests
    found = None
    for tid in list(show_tmdb_ids)[:3]:
        try:
            r = requests.get(f"{client.BASE}/tv/{int(tid)}", params={"api_key": client.api_key}, timeout=15)
            if r.ok:
                found = pick_network((r.json() or {}).get("networks") or [], name)
        except Exception:  # noqa: BLE001 - try the next show
            logger.debug("network lookup via show %s failed", tid, exc_info=True)
        if found:
            break
    _ids[key] = found
    return found


def network_card(engine, network: int) -> Optional[dict]:
    key = ("network", network)
    if key in _cards:
        return _cards[key]
    client = _tmdb(engine)
    card = None
    if client is not None:
        import requests
        try:
            r = requests.get(f"{client.BASE}/network/{int(network)}",
                             params={"api_key": client.api_key}, timeout=15)
            if r.ok:
                d = r.json() or {}
                card = {"tmdb_id": d.get("id"), "name": d.get("name"),
                        "logo": (client.LOGO + d["logo_path"]) if d.get("logo_path") else None,
                        "headquarters": d.get("headquarters") or None,
                        "origin_country": d.get("origin_country") or None,
                        "homepage": d.get("homepage") or None}
        except Exception:  # noqa: BLE001 - the page still shows the titles
            logger.debug("network card %s failed", network, exc_info=True)
    _cards[key] = card
    return card


SORTS = {"popular": "popularity.desc", "newest": "primary_release_date.desc",
         "rated": "vote_average.desc"}


def titles(engine, kind: str, maker: int, sort: str, page: int) -> list:
    """one page of what it made. tv sorts by first air date, not release."""
    sort_by = SORTS.get(sort, SORTS["popular"])
    if kind == "show" and sort_by.startswith("primary_release_date"):
        sort_by = "first_air_date.desc"
    extra: Dict[str, Any] = {"companies": str(maker)} if kind == "movie" else {"networks": str(maker)}
    if sort_by == "vote_average.desc":
        extra["vote_count_min"] = 200   # a 10/10 from three votes isn't top rated
    items = engine.discover_filter(kind, sort_by=sort_by, page=page, **extra) or []
    return [{**it, "kind": kind} for it in items]
