"""Resolve a collection definition into its member set.

Returns the OWNED items (what gets pushed to the server as the collection) plus
the members you don't own yet (the wishlist tie-in for list/franchise kinds).

Pure orchestration over the DB layer. The only external I/O — fetching a TMDB
franchise/list or Trakt list to learn the FULL membership — is INJECTED via
``list_fetcher`` so this module is unit-testable without network. Callers wire a
real fetcher (TMDB collection endpoint / list APIs) in the sync + automation
phases; franchise OWNED members resolve straight from the DB, so franchise
collections still populate even with no fetcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.video.collections.smart_filter import SmartFilterError

# fetcher(source, ref) -> list of {tmdb_id, title?, year?, poster_url?}
ListFetcher = Callable[[str, Any], List[Dict[str, Any]]]

_FRANCHISE_SOURCES = {"tmdb_collection", "franchise"}


@dataclass
class ResolvedCollection:
    media_type: str
    owned: List[Dict[str, Any]] = field(default_factory=list)     # rows with server_id
    missing: List[Dict[str, Any]] = field(default_factory=list)   # unowned list members
    error: Optional[str] = None                                   # set when unresolvable

    @property
    def server_ids(self) -> List[str]:
        return [str(m["server_id"]) for m in self.owned if m.get("server_id")]

    @property
    def ok(self) -> bool:
        return self.error is None


def _missing_from(full: List[Dict[str, Any]], owned_tmdb: set) -> List[Dict[str, Any]]:
    out = []
    for item in full or []:
        tid = item.get("tmdb_id")
        if tid is None or int(tid) in owned_tmdb:
            continue
        out.append({
            "tmdb_id": int(tid),
            "title": item.get("title"),
            "year": item.get("year"),
            "poster_url": item.get("poster_url"),
        })
    return out


def resolve_collection(db, definition: Dict[str, Any], *,
                       list_fetcher: Optional[ListFetcher] = None) -> ResolvedCollection:
    """Resolve ``definition`` (a row from ``get_collection_definition``) to its
    members. Never raises for a bad definition — returns a ResolvedCollection
    with ``error`` set so a nightly batch can skip one bad collection and carry
    on. Manual overrides (``include``/``exclude`` tmdb-id lists in the body)
    apply LAST, on top of whatever the builder resolved — every kind supports
    "perfect, except that one movie"."""
    media_type = (definition or {}).get("media_type") or "movie"
    if media_type not in ("movie", "show"):
        return ResolvedCollection(media_type="movie", error=f"bad media_type {media_type!r}")

    kind = (definition or {}).get("kind") or "smart"
    body = (definition or {}).get("definition") or {}

    if kind == "smart":
        try:
            owned = db.resolve_smart_members(media_type, body)
        except SmartFilterError as e:
            return ResolvedCollection(media_type=media_type, error=str(e))
        except Exception as e:   # noqa: BLE001 - a DB hiccup shouldn't crash the batch
            return ResolvedCollection(media_type=media_type, error=f"resolve failed: {e}")
        res = ResolvedCollection(media_type=media_type, owned=owned)
    elif kind == "list":
        res = _resolve_list(db, media_type, body, list_fetcher)
    else:
        return ResolvedCollection(media_type=media_type, error=f"unknown collection kind {kind!r}")

    return _apply_overrides(db, media_type, body, res) if res.ok else res


def _override_ids(body: Dict[str, Any], key: str) -> set:
    out = set()
    for v in body.get(key) or []:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return out


def _apply_overrides(db, media_type: str, body: Dict[str, Any],
                     res: ResolvedCollection) -> ResolvedCollection:
    """Pin/exclude specific titles on top of the resolved set. Exclude wins over
    include; both act on the missing set too (an excluded title must never be
    wishlisted)."""
    include = _override_ids(body, "include")
    exclude = _override_ids(body, "exclude")
    if not include and not exclude:
        return res
    owned = list(res.owned)
    if include:
        have = {int(m["tmdb_id"]) for m in owned if m.get("tmdb_id") is not None}
        want = [i for i in include if i not in have]
        if want:
            try:
                owned.extend(db.owned_by_tmdb_ids(media_type, want))
            except Exception:   # noqa: BLE001 - includes are additive; never kill the resolve
                pass
    if exclude:
        owned = [m for m in owned
                 if m.get("tmdb_id") is None or int(m["tmdb_id"]) not in exclude]
    missing = [m for m in res.missing
               if int(m["tmdb_id"]) not in exclude and int(m["tmdb_id"]) not in include]
    return ResolvedCollection(media_type=res.media_type, owned=owned, missing=missing)


def _resolve_list(db, media_type: str, body: Dict[str, Any],
                  list_fetcher: Optional[ListFetcher]) -> ResolvedCollection:
    source = str(body.get("source") or "").lower()

    if source in _FRANCHISE_SOURCES:
        if media_type != "movie":
            return ResolvedCollection(media_type=media_type,
                                      error="franchise collections are movies only")
        cid = body.get("collection_id")
        if cid is None:
            return ResolvedCollection(media_type=media_type, error="franchise: no collection_id")
        try:
            owned = db.franchise_owned_members(int(cid))
        except Exception as e:   # noqa: BLE001
            return ResolvedCollection(media_type=media_type, error=f"resolve failed: {e}")
        missing = []
        if list_fetcher is not None:
            owned_tmdb = {int(m["tmdb_id"]) for m in owned if m.get("tmdb_id") is not None}
            try:
                full = list_fetcher("tmdb_collection", int(cid))
                missing = _missing_from(full, owned_tmdb)
            except Exception:   # noqa: BLE001 - missing set is best-effort; owned still valid
                missing = []
        return ResolvedCollection(media_type=media_type, owned=owned, missing=missing)

    if source == "static":
        # A pinned membership snapshot (adopted server collections): portable
        # tmdb ids resolved against the library; no remote list, no missing set.
        ids = body.get("tmdb_ids") or []
        try:
            owned = db.owned_by_tmdb_ids(media_type, ids)
        except Exception as e:   # noqa: BLE001
            return ResolvedCollection(media_type=media_type, error=f"resolve failed: {e}")
        return ResolvedCollection(media_type=media_type, owned=owned)

    # tmdb_chart / tmdb_keyword / tmdb_list / trakt_list — need the fetcher.
    if list_fetcher is None:
        return ResolvedCollection(media_type=media_type,
                                  error=f"list source {source!r} needs a list fetcher")
    if source == "tmdb_chart":
        if not body.get("chart"):
            return ResolvedCollection(media_type=media_type, error="chart source: no chart chosen")
        ref: Any = dict(body)
    elif source == "tmdb_keyword":
        if not (body.get("query") or "").strip():
            return ResolvedCollection(media_type=media_type, error="keyword source: no keyword")
        ref = dict(body, kind=media_type)
    elif source == "tmdb_union":
        if not (body.get("collections") or body.get("keywords")):
            return ResolvedCollection(media_type=media_type,
                                      error="universe source: no franchises or keywords")
        ref = dict(body, kind=media_type)
    elif source in ("imdb_chart", "imdb_list"):
        if source == "imdb_chart" and not body.get("chart"):
            return ResolvedCollection(media_type=media_type, error="imdb chart: no chart chosen")
        if source == "imdb_list" and not (body.get("url") or "").strip():
            return ResolvedCollection(media_type=media_type, error="imdb list: no list URL")
        ref = dict(body, kind=media_type)
    else:
        ref = body.get("list_id") or body.get("url") or body.get("ref")
        if not ref:
            return ResolvedCollection(media_type=media_type,
                                      error=f"list source {source!r}: no reference")
    try:
        full = list_fetcher(source, ref)
    except Exception as e:   # noqa: BLE001
        return ResolvedCollection(media_type=media_type, error=f"list fetch failed: {e}")

    tmdb_ids = [item.get("tmdb_id") for item in (full or []) if item.get("tmdb_id") is not None]
    try:
        owned = db.owned_by_tmdb_ids(media_type, tmdb_ids)
    except Exception as e:   # noqa: BLE001
        return ResolvedCollection(media_type=media_type, error=f"resolve failed: {e}")
    # ``owned_by_tmdb_ids`` uses ``WHERE tmdb_id IN (…)`` — no ORDER BY, so it returns rows in
    # table order, discarding the LIST's own order (e.g. the IMDb Top 250 rank). Re-sort owned
    # back into the fetched order so the collection syncs + displays in rank, not by release date.
    pos = {}
    for i, item in enumerate(full or []):
        t = item.get("tmdb_id")
        if t is not None:
            pos.setdefault(int(t), i)
    tail = len(pos)
    owned.sort(key=lambda m: pos.get(int(m["tmdb_id"]), tail) if m.get("tmdb_id") is not None else tail)
    owned_tmdb = {int(m["tmdb_id"]) for m in owned if m.get("tmdb_id") is not None}
    missing = _missing_from(full, owned_tmdb)
    return ResolvedCollection(media_type=media_type, owned=owned, missing=missing)


__all__ = ["resolve_collection", "ResolvedCollection", "ListFetcher"]
