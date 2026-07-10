"""Preset packs — Kometa-style one-click collections, expanded against the
user's OWN library.

A pack is a template ("Genres", "Decades", "Franchises", …) that expands into
concrete collection candidates with real owned counts, so the studio can show
"Action · 142" before anything is created. Applying a pack just calls
``create_collection_definition`` per selected entry — a preset collection is a
completely normal definition afterwards (editable, syncable, deletable).

Pure orchestration over the DB layer's aggregate helpers; no network, no server
I/O. The wishlist superpower rides along for free: franchise entries are
``kind='list'`` definitions, so ``wishlist_missing`` works exactly as it does
for hand-built list collections.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.presets")

# Entries at/above this owned count are pre-checked in the picker UI.
_SUGGEST_MIN = {"genres": 5, "decades": 5, "franchises": 2, "studios": 4,
                "networks": 3, "directors": 3, "essentials": 3}

# ── pack catalog ────────────────────────────────────────────────────────────
# id -> {title, blurb, icon, media: tuple of media types the pack supports}
_PACKS: Dict[str, Dict[str, Any]] = {
    "genres": {
        "title": "Genres",
        "blurb": "One collection per genre you own — Action, Horror, Sci-Fi…",
        "icon": "genres", "media": ("movie", "show"),
    },
    "decades": {
        "title": "Decades",
        "blurb": "80s, 90s, 2000s… only the decades in your library.",
        "icon": "decades", "media": ("movie", "show"),
    },
    "franchises": {
        "title": "Franchises",
        "blurb": "Every film series you've started — and it can wishlist the entries you're missing.",
        "icon": "franchises", "media": ("movie",),
    },
    "studios": {
        "title": "Studios",
        "blurb": "Pixar, A24, Ghibli… the studios behind your movies.",
        "icon": "studios", "media": ("movie",),
    },
    "networks": {
        "title": "Networks",
        "blurb": "HBO, AMC, Netflix… the networks behind your shows.",
        "icon": "networks", "media": ("show",),
    },
    "directors": {
        "title": "Directors",
        "blurb": "The filmmakers you own the most movies from.",
        "icon": "directors", "media": ("movie",),
    },
    "essentials": {
        "title": "Essentials",
        "blurb": "4K, Critically Acclaimed, Recently Added — living shelves that update on every sync.",
        "icon": "essentials", "media": ("movie", "show"),
    },
}

_PACK_ORDER = ["genres", "decades", "franchises", "studios", "networks",
               "directors", "essentials"]


def _norm(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _media_word(media_type: str, plural: bool = True) -> str:
    if media_type == "show":
        return "shows" if plural else "show"
    return "movies" if plural else "movie"


def _smart(rules: List[Dict[str, Any]], match: str = "all") -> Dict[str, Any]:
    return {"match": match, "rules": rules}


def _entry(key: str, name: str, count: int, summary: str, *, kind: str = "smart",
           definition: Optional[Dict[str, Any]] = None, pack: str = "",
           wishlist_capable: bool = False) -> Dict[str, Any]:
    return {
        "key": key, "name": name, "count": int(count or 0), "summary": summary,
        "kind": kind, "definition": definition or {},
        "suggested": int(count or 0) >= _SUGGEST_MIN.get(pack, 3),
        "wishlist_capable": wishlist_capable,
    }


def _strip_collection_suffix(name: str) -> str:
    n = (name or "").strip()
    for suffix in (" Collection", " collection"):
        if n.endswith(suffix) and len(n) > len(suffix):
            return n[: -len(suffix)].strip()
    return n


# ── per-pack expansion ──────────────────────────────────────────────────────
def _expand_genres(db, mt: str) -> List[Dict[str, Any]]:
    out = []
    for g in db.owned_genre_counts(mt, limit=60) or []:
        name = str(g.get("value") or "").strip()
        if not name:
            continue
        out.append(_entry(
            "genre:" + name, name, g.get("count"),
            f"Every {name} {_media_word(mt, False)} in your library.",
            definition=_smart([{"field": "genre", "op": "in", "value": [name]}]),
            pack="genres"))
    return out


def _expand_decades(db, mt: str) -> List[Dict[str, Any]]:
    out = []
    for d in db.owned_decade_counts(mt) or []:
        try:
            decade = int(d.get("value"))
        except (TypeError, ValueError):
            continue
        label = f"{decade}s"
        out.append(_entry(
            "decade:" + str(decade), label, d.get("count"),
            f"{_media_word(mt).capitalize()} released between {decade} and {decade + 9}.",
            definition=_smart([{"field": "decade", "op": "in", "value": [decade]}]),
            pack="decades"))
    return out


def _expand_franchises(db, mt: str) -> List[Dict[str, Any]]:
    if mt != "movie":
        return []
    out = []
    for f in db.owned_movie_collections(limit=100) or []:
        cid = f.get("collection_id")
        if not cid:
            continue
        name = _strip_collection_suffix(f.get("name") or "") or f"Franchise {cid}"
        out.append(_entry(
            "franchise:" + str(cid), name, f.get("owned_count"),
            f"The complete {name} series.",
            kind="list",
            definition={"source": "tmdb_collection", "collection_id": int(cid)},
            pack="franchises", wishlist_capable=True))
    return out


def _expand_studios(db, mt: str) -> List[Dict[str, Any]]:
    if mt != "movie":
        return []
    out = []
    for s in db.owned_studio_counts(limit=40) or []:
        name = str(s.get("value") or "").strip()
        if not name:
            continue
        out.append(_entry(
            "studio:" + name, name, s.get("count"),
            f"Movies from {name}.",
            definition=_smart([{"field": "studio", "op": "is", "value": name}]),
            pack="studios"))
    return out


def _expand_networks(db, mt: str) -> List[Dict[str, Any]]:
    if mt != "show":
        return []
    out = []
    for n in db.owned_network_counts(limit=40) or []:
        name = str(n.get("value") or "").strip()
        if not name:
            continue
        out.append(_entry(
            "network:" + name, name, n.get("count"),
            f"Shows from {name}.",
            definition=_smart([{"field": "network", "op": "is", "value": name}]),
            pack="networks"))
    return out


def _expand_directors(db, mt: str) -> List[Dict[str, Any]]:
    if mt != "movie":
        return []
    out = []
    for p in db.top_owned_people(jobs=("Director",), min_titles=2, limit=24) or []:
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        out.append(_entry(
            "director:" + name, name, p.get("owned_count"),
            f"Directed by {name}.",
            definition=_smart([{"field": "director", "op": "is", "value": name}]),
            pack="directors"))
    return out


# Fixed essentials candidates: (key, name, summary factory, rules).
_ESSENTIALS = [
    ("4k", "4K Ultra HD",
     lambda mt: f"Every {_media_word(mt, False)} you own in 2160p.",
     [{"field": "resolution", "op": "in", "value": ["2160p"]}]),
    ("acclaimed", "Critically Acclaimed",
     lambda mt: f"{_media_word(mt).capitalize()} rated 7.5 or higher.",
     [{"field": "rating", "op": "gte", "value": 7.5}]),
    ("recent", "Recently Added",
     lambda mt: "Added to your library in the last 30 days — refreshes on every sync.",
     [{"field": "added", "op": "in_last_days", "value": 30}]),
    ("new", "New Releases",
     lambda mt: "Released in the last year — refreshes on every sync.",
     [{"field": "released", "op": "in_last_days", "value": 365}]),
]


def _expand_essentials(db, mt: str) -> List[Dict[str, Any]]:
    out = []
    for key, name, blurb, rules in _ESSENTIALS:
        definition = _smart(list(rules))
        try:
            count = db.count_smart_members(mt, definition)
        except Exception:   # noqa: BLE001 - a bad count shouldn't hide the entry
            logger.debug("essentials count failed for %s/%s", mt, key, exc_info=True)
            count = 0
        out.append(_entry("essential:" + key, name, count, blurb(mt),
                          definition=definition, pack="essentials"))
    return out


_EXPANDERS = {
    "genres": _expand_genres, "decades": _expand_decades,
    "franchises": _expand_franchises, "studios": _expand_studios,
    "networks": _expand_networks, "directors": _expand_directors,
    "essentials": _expand_essentials,
}


# ── public surface ──────────────────────────────────────────────────────────
def expand_pack(db, pack_id: str, media_type: str) -> List[Dict[str, Any]]:
    """Expand one pack against the library. Each entry carries a stable ``key``
    (what apply selects by), the owned ``count``, and the full definition it
    would create. Entries whose name matches an existing definition (same media
    type, case-insensitive) get ``exists: true`` so apply is idempotent."""
    meta = _PACKS.get(pack_id)
    expander = _EXPANDERS.get(pack_id)
    if meta is None or expander is None or media_type not in meta["media"]:
        return []
    entries = expander(db, media_type)

    existing = set()
    try:
        for c in db.list_collection_definitions() or []:
            if (c.get("media_type") or "movie") == media_type:
                existing.add(_norm(c.get("name")))
    except Exception:   # noqa: BLE001 - exists-marking is best-effort
        logger.debug("existing-definition scan failed", exc_info=True)
    for e in entries:
        e["exists"] = _norm(e["name"]) in existing
    return entries


def list_packs(db, media_type: str) -> List[Dict[str, Any]]:
    """The full preset browser payload for one media type: every applicable pack
    with its expanded entries, available/item totals, and exists-marking."""
    out = []
    for pid in _PACK_ORDER:
        meta = _PACKS[pid]
        if media_type not in meta["media"]:
            continue
        entries = expand_pack(db, pid, media_type)
        out.append({
            "id": pid, "title": meta["title"], "blurb": meta["blurb"],
            "icon": meta["icon"], "media_type": media_type,
            "available": len(entries),
            "item_total": sum(e["count"] for e in entries),
            "entries": entries,
        })
    return out


def apply_pack(db, pack_id: str, media_type: str, keys, *,
               wishlist_missing: bool = True) -> Dict[str, Any]:
    """Create collection definitions for the selected entry keys. Existing names
    are skipped (idempotent — re-applying a pack never duplicates). Franchise
    entries get ``wishlist_missing`` per the caller's choice; smart entries never
    do (they have no missing set). Returns {created: [{id, name}], skipped: [name]}."""
    wanted = {str(k) for k in (keys or [])}
    created, skipped = [], []
    for e in expand_pack(db, pack_id, media_type):
        if e["key"] not in wanted:
            continue
        if e.get("exists"):
            skipped.append(e["name"])
            continue
        cid = db.create_collection_definition(
            e["name"], kind=e["kind"], media_type=media_type,
            definition=e["definition"], summary=e["summary"],
            sort_order="release", sync_mode="sync",
            wishlist_missing=bool(wishlist_missing and e.get("wishlist_capable")),
            enabled=True)
        if cid is None:
            skipped.append(e["name"])
            continue
        created.append({"id": cid, "name": e["name"]})
    return {"created": created, "skipped": skipped}


__all__ = ["list_packs", "expand_pack", "apply_pack"]
