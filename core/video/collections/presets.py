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

import threading
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.presets")

# Entries at/above this owned count are pre-checked in the picker UI.
_SUGGEST_MIN = {"genres": 5, "decades": 5, "franchises": 2, "studios": 4,
                "networks": 3, "directors": 3, "essentials": 3,
                "seasonal": 3, "stories": 3, "universes": 2}

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
    "charts": {
        "title": "Charts",
        "blurb": "Top Rated 250, Most Popular, Trending — living charts that re-resolve on every sync, and can wishlist what you're missing.",
        "icon": "charts", "media": ("movie", "show"),
    },
    "seasonal": {
        "title": "Seasonal",
        "blurb": "Christmas, Halloween, Valentine's… your holiday shelves, ready before the holiday is.",
        "icon": "seasonal", "media": ("movie", "show"),
    },
    "stories": {
        "title": "Based On…",
        "blurb": "Books, comics, true stories, video games — what your titles were made from.",
        "icon": "stories", "media": ("movie", "show"),
    },
    "universes": {
        "title": "Universes",
        "blurb": "The MCU, Middle-earth, the Wizarding World… whole cinematic universes — TMDB franchises only cover single series, these span them.",
        "icon": "universes", "media": ("movie",),
    },
}

_PACK_ORDER = ["charts", "genres", "franchises", "universes", "decades",
               "seasonal", "stories", "studios", "networks", "directors", "essentials"]


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


# Studio/network strings vary per title — one real-world brand hides behind many
# labels ("Walt Disney Productions" / "Walt Disney Pictures" / "Disney";
# "Hallmark Channel" / "Hallmark Media"). Exact-match entries fragmented brands
# (Boulder: a 23-item Hallmark, a 22-item Disney missing every classic). Group
# by SHARED DISTINCTIVE TOKENS: strip the generic industry words, then merge
# groups that share any real brand word — 'Disney' ∩ 'Walt Disney Pictures'
# = {disney} → one group. Slight over-merge (the Fox family unifies) beats
# silently splitting a brand across eras.
_GENERIC_TOKENS = {
    "the", "a", "an", "of", "and", "&", "pictures", "picture", "studios", "studio",
    "entertainment", "films", "film", "animation", "animations", "television", "tv",
    "media", "channel", "channels", "network", "networks", "productions", "production",
    "company", "co", "inc", "llc", "ltd", "corp", "corporation", "group", "home",
    "video", "distribution", "international", "worldwide", "interactive", "originals",
}


def _brand_tokens(name: str) -> set:
    import re
    words = re.split(r"[^\w]+", name.casefold())
    return {w for w in words if w and w not in _GENERIC_TOKENS}


def _group_brands(rows) -> List[dict]:
    """Union-find over (name, count) rows: variants sharing any distinctive
    token join one brand group. Returns [{key, label, names, total}]."""
    entries = []
    for name, count in rows:
        toks = _brand_tokens(name)
        entries.append({"name": name, "count": count,
                        "toks": toks or {name.casefold()}})   # all-generic → own group
    parent = list(range(len(entries)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_token: Dict[str, int] = {}
    for i, e in enumerate(entries):
        for t in e["toks"]:
            if t in by_token:
                parent[find(i)] = find(by_token[t])
            else:
                by_token[t] = i
    groups: Dict[int, list] = {}
    for i in range(len(entries)):
        groups.setdefault(find(i), []).append(entries[i])

    out = []
    for members in groups.values():
        members.sort(key=lambda e: -e["count"])
        names = sorted(e["name"] for e in members)
        shared = set.intersection(*(e["toks"] for e in members))
        if len(members) == 1:
            label = members[0]["name"]
        elif shared:
            # Title the shared brand word(s) in the order the top variant uses.
            top_words = members[0]["name"].split()
            ordered = [w for w in top_words if _brand_tokens(w) & shared] or [next(iter(shared)).title()]
            label = " ".join(ordered)
        else:
            label = members[0]["name"]      # chained merge with no common token
        out.append({"key": "-".join(sorted(shared)) or names[0].casefold(),
                    "label": label, "names": names,
                    "total": sum(e["count"] for e in members)})
    return out


def _expand_brand_pack(db, rows, *, pack: str, field: str, noun: str) -> List[Dict[str, Any]]:
    grouped = _group_brands([(str(r.get("value") or "").strip(), int(r.get("count") or 0))
                             for r in rows if str(r.get("value") or "").strip()])
    out = []
    for g in grouped:
        out.append(_entry(
            f"{field}:" + g["key"], g["label"], g["total"],
            f"{noun} from {g['label']}." +
            (f" Covers {len(g['names'])} label variants." if len(g["names"]) > 1 else ""),
            definition=_smart([{"field": field, "op": "in", "value": g["names"]}]),
            pack=pack))
    out.sort(key=lambda e: -e["count"])
    return out


def _expand_studios(db, mt: str) -> List[Dict[str, Any]]:
    if mt != "movie":
        return []
    return _expand_brand_pack(db, db.owned_studio_counts(limit=100) or [],
                              pack="studios", field="studio", noun="Movies")


def _expand_networks(db, mt: str) -> List[Dict[str, Any]]:
    if mt != "show":
        return []
    return _expand_brand_pack(db, db.owned_network_counts(limit=100) or [],
                              pack="networks", field="network", noun="Shows")


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
    ("unwatched", "Unwatched",
     lambda mt: f"{_media_word(mt).capitalize()} you haven't watched yet — shrinks as you watch.",
     [{"field": "watched", "op": "is", "value": False}]),
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


# ── remote packs (charts / seasonal / stories) ──────────────────────────────
# These need the list fetcher: entries are TMDB-backed list definitions, and the
# browse counts are "owned ∩ fetched" — the same intersection sync will push.
# Fetches run concurrently (each is 1-13 cached TMDB calls); a failed fetch
# yields count=None ("resolves on sync") rather than hiding the entry.

# Each row carries its full list definition — TMDB charts and the keyless IMDb
# scrape (the real Top 250; IMDb has no API, we read the chart page like
# Kometa does) live side by side.
_CHART_ENTRIES = {
    "movie": [
        ("imdb-top", "IMDb Top 250",
         {"source": "imdb_chart", "chart": "top"},
         "The real IMDb Top 250 — no API key needed. Re-resolves on every sync."),
        ("top",      "Top Rated 250",
         {"source": "tmdb_chart", "chart": "top_movies", "limit": 250},
         "TMDB's top-rated chart. Re-resolves on every sync."),
        ("popular",  "Most Popular",
         {"source": "tmdb_chart", "chart": "popular_movies", "limit": 100},
         "The most popular movies right now. Re-resolves on every sync."),
        ("trending", "Trending This Week",
         {"source": "tmdb_chart", "chart": "trending_movies", "limit": 20},
         "This week's trending movies. Re-resolves on every sync."),
        ("theaters", "In Theaters",
         {"source": "tmdb_chart", "chart": "now_playing", "limit": 40},
         "Playing in theaters right now. Re-resolves on every sync."),
    ],
    "show": [
        ("imdb-top", "IMDb Top 250 TV",
         {"source": "imdb_chart", "chart": "toptv"},
         "The real IMDb Top 250 TV shows — no API key needed. Re-resolves on every sync."),
        ("top",      "Top Rated 250",
         {"source": "tmdb_chart", "chart": "top_shows", "limit": 250},
         "TMDB's top-rated chart. Re-resolves on every sync."),
        ("popular",  "Most Popular",
         {"source": "tmdb_chart", "chart": "popular_shows", "limit": 100},
         "The most popular shows right now. Re-resolves on every sync."),
        ("trending", "Trending This Week",
         {"source": "tmdb_chart", "chart": "trending_shows", "limit": 20},
         "This week's trending shows. Re-resolves on every sync."),
        ("air",      "On The Air",
         {"source": "tmdb_chart", "chart": "on_the_air", "limit": 40},
         "Currently airing shows. Re-resolves on every sync."),
    ],
}

# (key, name, tmdb keyword, seasonal window MM-DD→MM-DD). The window makes the
# shelf LIVE: the collection appears on the server when the season starts and
# is removed when it ends (Kometa's schedule ranges, minus the YAML).
_SEASONAL = [
    ("christmas",    "Christmas",       "christmas",       ("11-20", "01-06")),
    ("halloween",    "Halloween",       "halloween",       ("09-15", "11-02")),
    ("valentine",    "Valentine's Day", "valentine's day", ("01-25", "02-15")),
    ("easter",       "Easter",          "easter",          ("03-01", "04-30")),
    ("thanksgiving", "Thanksgiving",    "thanksgiving",    ("11-01", "11-30")),
    ("newyear",      "New Year's Eve",  "new year's eve",  ("12-26", "01-08")),
]

_STORIES = [
    ("book",  "Based on a Book",       "based on novel or book"),
    ("comic", "Based on a Comic",      "based on comic"),
    ("true",  "Based on a True Story", "based on true story"),
    ("game",  "Based on a Video Game", "based on video game"),
]


def _owned_counts(db, mt: str, specs, fetcher) -> List[Optional[tuple]]:
    """(owned, total) per (source, ref) spec — concurrent, None on fetch failure."""
    if fetcher is None:
        return [None] * len(specs)

    def one(spec):
        try:
            full = fetcher(spec[0], spec[1]) or []
        except Exception:   # noqa: BLE001 - count is a nicety; sync still resolves
            return None
        if not full:
            return None
        ids = [i.get("tmdb_id") for i in full if i.get("tmdb_id") is not None]
        try:
            owned = db.owned_by_tmdb_ids(mt, ids)
        except Exception:   # noqa: BLE001
            return None
        return (len(owned), len(ids))

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(one, specs))


def _remote_entry(key, name, count, summary, definition, pack, mt) -> Dict[str, Any]:
    # Movies wishlist as movie rows; shows expand into aired-episode rows.
    e = _entry(key, name, (count or (0, 0))[0], summary, kind="list",
               definition=definition, pack=pack, wishlist_capable=True)
    if count is None:
        e["count"] = None            # fetch failed/offline — resolves on sync
        e["suggested"] = pack in ("charts", "universes")
    else:
        e["of_total"] = count[1]
        if pack == "charts":
            e["suggested"] = True    # charts are the marquee — always pre-checked
    return e


def _expand_charts(db, mt: str, fetcher=None) -> List[Dict[str, Any]]:
    rows = _CHART_ENTRIES.get(mt) or []
    specs = [(definition["source"], dict(definition, kind=mt))
             for _, _, definition, _ in rows]
    counts = _owned_counts(db, mt, specs, fetcher)
    return [
        _remote_entry("chart:" + key, name, counts[i], blurb, dict(definition),
                      "charts", mt)
        for i, (key, name, definition, blurb) in enumerate(rows)
    ]


def _expand_keyword_pack(db, mt: str, fetcher, rows, pack: str, blurb_fmt) -> List[Dict[str, Any]]:
    # 250 (not 100): a theme's owned deep-cuts live in the popularity long tail —
    # a Christmas movie the user owns must land in the Christmas collection.
    specs = [("tmdb_keyword", {"kind": mt, "query": r[2], "limit": 250}) for r in rows]
    counts = _owned_counts(db, mt, specs, fetcher)
    out = []
    for i, row in enumerate(rows):
        key, name, q = row[0], row[1], row[2]
        e = _remote_entry(pack + ":" + key, name, counts[i], blurb_fmt(name, mt),
                          {"source": "tmdb_keyword", "query": q, "limit": 250}, pack, mt)
        if len(row) > 3 and row[3]:
            e["window"] = row[3]        # (start MM-DD, end MM-DD) — seasonal shelf
        out.append(e)
    return out


# Curated universes (movies): TMDB collections cover single series only, so a
# universe is a UNION — franchise ids where TMDB defines them cleanly, keyword
# themes where it doesn't (the MCU/DCEU/MonsterVerse carry maintained TMDB
# keywords; ids resolve at runtime, nothing hardcoded to rot).
_UNIVERSES = [
    # Keyword-only universes have no TMDB collection (no title art) — the
    # "logo" hint gives them their studio's mark as context art instead.
    ("mcu", "Marvel Cinematic Universe",
     {"keywords": ["marvel cinematic universe"], "logo": "Marvel Studios"},
     "Every MCU film, phase by phase."),
    ("dc", "DC Extended Universe",
     {"keywords": ["dc extended universe"], "logo": "DC Films"},
     "The DCEU, from Man of Steel on."),
    ("middle-earth", "Middle-earth",
     {"collections": [119, 121938]},
     "The Lord of the Rings and The Hobbit trilogies."),
    ("wizarding", "Wizarding World",
     {"collections": [1241, 435259]},
     "Harry Potter and Fantastic Beasts."),
    ("star-wars", "Star Wars Saga",
     {"collections": [10]},
     "The nine-film Skywalker saga."),
    ("monsterverse", "MonsterVerse",
     {"keywords": ["monsterverse"]},
     "Godzilla, Kong, and the Titans."),
]


def _expand_universes(db, mt: str, fetcher=None) -> List[Dict[str, Any]]:
    if mt != "movie":
        return []
    specs = [("tmdb_union", dict(spec, kind="movie", limit=200))
             for _, _, spec, _ in _UNIVERSES]
    counts = _owned_counts(db, mt, specs, fetcher)
    return [
        _remote_entry("universe:" + key, name, counts[i], blurb,
                      dict({"source": "tmdb_union", "limit": 200}, **spec), "universes", mt)
        for i, (key, name, spec, blurb) in enumerate(_UNIVERSES)
    ]


def _expand_seasonal(db, mt: str, fetcher=None) -> List[Dict[str, Any]]:
    return _expand_keyword_pack(
        db, mt, fetcher, _SEASONAL, "seasonal",
        lambda name, m: f"{name} {_media_word(m)} — on the server for the season, gone after it.")


def _expand_stories(db, mt: str, fetcher=None) -> List[Dict[str, Any]]:
    return _expand_keyword_pack(
        db, mt, fetcher, _STORIES, "stories",
        lambda name, m: f"{name.replace('Based on', 'Adapted from')} — refreshes on every sync.")


_EXPANDERS = {
    "genres": _expand_genres, "decades": _expand_decades,
    "franchises": _expand_franchises, "studios": _expand_studios,
    "networks": _expand_networks, "directors": _expand_directors,
    "essentials": _expand_essentials,
    "charts": _expand_charts, "seasonal": _expand_seasonal, "stories": _expand_stories,
    "universes": _expand_universes,
}
_REMOTE_PACKS = {"charts", "seasonal", "stories", "universes"}


# ── public surface ──────────────────────────────────────────────────────────
def expand_pack(db, pack_id: str, media_type: str, fetcher=None) -> List[Dict[str, Any]]:
    """Expand one pack against the library. Each entry carries a stable ``key``
    (what apply selects by), the owned ``count`` (None when a remote pack can't
    fetch — it still resolves on sync), and the full definition it would create.
    Entries whose name matches an existing definition (same media type,
    case-insensitive) get ``exists: true`` so apply is idempotent. ``fetcher``
    (the list fetcher) powers the remote packs' owned-∩-chart counts."""
    meta = _PACKS.get(pack_id)
    expander = _EXPANDERS.get(pack_id)
    if meta is None or expander is None or media_type not in meta["media"]:
        return []
    if pack_id in _REMOTE_PACKS:
        entries = expander(db, media_type, fetcher)
    else:
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


def pack_catalog(media_type: str) -> List[Dict[str, Any]]:
    """Just the pack identities (title/blurb/icon) — no DB, no network. The
    browser paints these instantly as skeletons while the expansion runs."""
    return [{"id": pid, "title": _PACKS[pid]["title"], "blurb": _PACKS[pid]["blurb"],
             "icon": _PACKS[pid]["icon"], "media_type": media_type}
            for pid in _PACK_ORDER if media_type in _PACKS[pid]["media"]]


def list_packs(db, media_type: str, fetcher=None) -> List[Dict[str, Any]]:
    """The full preset browser payload for one media type: every applicable pack
    with its expanded entries, available/item totals, and exists-marking.
    Packs expand CONCURRENTLY — on a large library the aggregate queries
    (credits joins, genre scans) are the slow part, and they're independent."""
    pids = [pid for pid in _PACK_ORDER if media_type in _PACKS[pid]["media"]]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        expanded = list(ex.map(lambda pid: expand_pack(db, pid, media_type, fetcher), pids))
    out = []
    for pid, entries in zip(pids, expanded, strict=False):
        meta = _PACKS[pid]
        out.append({
            "id": pid, "title": meta["title"], "blurb": meta["blurb"],
            "icon": meta["icon"], "media_type": media_type,
            "available": len(entries),
            "item_total": sum(e["count"] or 0 for e in entries),
            "entries": entries,
        })
    return out


def apply_pack(db, pack_id: str, media_type: str, keys, *,
               wishlist_missing: bool = True, fetcher=None) -> Dict[str, Any]:
    """Create collection definitions for the selected entry keys. Existing names
    are skipped (idempotent — re-applying a pack never duplicates). List-kind
    entries (franchises/charts/seasonal/stories) get ``wishlist_missing`` per the
    caller's choice; smart entries never do (they have no missing set).
    Returns {created: [{id, name}], skipped: [name]}."""
    wanted = {str(k) for k in (keys or [])}
    created, skipped = [], []
    for e in expand_pack(db, pack_id, media_type, fetcher):
        if e["key"] not in wanted:
            continue
        if e.get("exists"):
            skipped.append(e["name"])
            continue
        window = e.get("window") or (None, None)
        cid = db.create_collection_definition(
            e["name"], kind=e["kind"], media_type=media_type,
            definition=e["definition"], summary=e["summary"],
            sort_order="release", sync_mode="sync",
            wishlist_missing=bool(wishlist_missing and e.get("wishlist_capable")),
            enabled=True, window_start=window[0], window_end=window[1])
        if cid is None:
            skipped.append(e["name"])
            continue
        created.append({"id": cid, "name": e["name"]})
    return {"created": created, "skipped": skipped}


# ── franchise-id backfill ────────────────────────────────────────────────────
# The Franchises pack expands from movies.tmdb_collection_id, but that column is
# only backfilled lazily (20 per Discover-page visit) for movies matched before
# it existed — so the pack silently under-reports owned franchises (the "where's
# my LOTR collection?" problem). Browsing presets drains the WHOLE backlog in a
# background thread instead (one cached TMDB call per movie, singleton-guarded).
_backfill_lock = threading.Lock()
_backfill_running = [False]


def backfill_missing_franchises(db, engine=None, batch: int = 50, cap: int = 4000) -> int:
    """Fill tmdb_collection_id for every owned, TMDB-matched movie that lacks it.
    Returns how many movies were checked. Synchronous — callers thread it."""
    if engine is None:
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            engine = get_video_enrichment_engine()
        except Exception:   # noqa: BLE001 - no engine → nothing to backfill with
            return 0
    if engine is None:
        return 0
    checked = 0
    seen: set = set()          # failed lookups stay in the backlog — never re-loop them
    while checked < cap:
        rows = [r for r in (db.movies_missing_collection(limit=batch + len(seen)) or [])
                if r["id"] not in seen]
        if not rows:
            break
        for mv in rows[:batch]:
            seen.add(mv["id"])
            coll = engine.movie_collection(mv["tmdb_id"])
            # None = lookup failed (leave for retry); {'id': None} = genuinely no
            # franchise — record it so this movie leaves the backlog.
            if coll is not None:
                db.set_movie_collection(mv["id"], coll.get("id"), coll.get("name"))
            checked += 1
    return checked


def kick_franchise_backfill(db) -> bool:
    """Run the backfill in a background thread (at most one at a time)."""
    with _backfill_lock:
        if _backfill_running[0]:
            return False
        _backfill_running[0] = True

    def run():
        try:
            n = backfill_missing_franchises(db)
            if n:
                logger.info("franchise backfill checked %d movies", n)
        except Exception:   # noqa: BLE001 - background nicety
            logger.exception("franchise backfill failed")
        finally:
            _backfill_running[0] = False

    threading.Thread(target=run, name="franchise-backfill", daemon=True).start()
    return True


__all__ = ["list_packs", "expand_pack", "apply_pack", "pack_catalog",
           "backfill_missing_franchises", "kick_franchise_backfill"]
