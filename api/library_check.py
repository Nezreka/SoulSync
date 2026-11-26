"""
batched library check for page badges (the browser companion).

one POST answers a whole page of artist names: is each one in the library,
its library id, its albums (when asked), and whether the profile watches it.
before this the companion asked once per artist: a fuzzy artist search
(LIKE '%name%' ordered by name, first five rows, so "Low" lost to "Afterglow"
and "Barlow" on a big library), a second call for the albums, and a metadata
provider search per artist just to learn an id for the watchlist check.

matching is exact: the artist's name key (indexed), then the punctuation and
accent free key (kept in memory by database/fold_index), against the same
artists the Library page lists. no provider calls, nothing written.
"""

from __future__ import annotations

from flask import request

from database.music_database import get_database
from utils.logging_config import get_logger

from .auth import require_api_key
from .helpers import api_error, api_success, parse_profile_id

logger = get_logger("api.library_check")

# a page of cards, a few rails deep. more than this is a probe
MAX_ARTISTS = 200
MAX_NAME_LEN = 300


def _items(raw):
    """[{name, provider_id, source, albums}] from strings or objects, deduped
    on the exact text, capped."""
    out, seen = [], set()
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()[:MAX_NAME_LEN]
        if not name or name in seen:
            continue
        seen.add(name)
        pid = it.get("provider_id")
        out.append({
            "name": name,
            "provider_id": str(pid).strip() if pid not in (None, "") else "",
            "albums": it.get("albums") is True,
        })
        if len(out) >= MAX_ARTISTS:
            break
    return out


def _member_ids(conn):
    """the artists the Library page lists, as canonical ids"""
    from core.library2.member_version import cached_member_ids
    from core.library2.sql_util import library_index_artist_ids_sql
    sql = library_index_artist_ids_sql()
    ids = cached_member_ids(conn, sql)
    if ids is None:
        ids = [int(r[0]) for r in conn.execute(sql) if r[0] is not None]
    return set(ids)


def _visible(conn, ids):
    """the subset of canonical ``ids`` this library scope may see (all of
    them while the scope is every library)"""
    from core.library2.sql_util import scope_visibility_sql
    visible = scope_visibility_sql("artist", "va")
    if not visible or not ids:
        return set(ids)
    out = set()
    ids = list(ids)
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        marks = ",".join("?" for _ in part)
        out.update(int(r[0]) for r in conn.execute(
            f"SELECT a.id FROM lib2_artists a WHERE a.id IN ({marks}) AND EXISTS ("
            f"SELECT 1 FROM lib2_artists va WHERE COALESCE(va.canonical_artist_id, va.id) = a.id "
            f"AND {visible})", part))
    return out


def match_artists(db, conn, names):
    """{name: (canonical id, library name)} for the names the library lists.
    the indexed name key first (case and spacing), then the folded key
    (punctuation and accents: "ACDC" for "AC/DC", "Bjork" for "Björk")."""
    from core.library2.importer import normalize_name
    from core.text.normalize import normalize_key
    from database import fold_index

    cand = {}
    keys = {}
    for name in names:
        keys.setdefault(normalize_name(name), []).append(name)
    key_list = list(keys)
    for i in range(0, len(key_list), 400):
        part = key_list[i:i + 400]
        marks = ",".join("?" for _ in part)
        for r in conn.execute(
                f"SELECT id, name_key FROM lib2_artists WHERE name_key IN ({marks})", part):
            for name in keys.get(r[1], ()):
                cand.setdefault(name, [[], []])[0].append(int(r[0]))
    for name in names:
        if name in cand:
            continue
        key = normalize_key(name)
        if not key:
            continue
        ids = fold_index.ids_with_key(conn, str(db.database_path), fold_index.ARTIST_KEYS, key)
        if ids:
            cand.setdefault(name, [[], []])[1].extend(int(i) for i in ids)
    if not cand:
        return {}

    every = {i for tiers in cand.values() for tier in tiers for i in tier}
    canon, label = {}, {}
    every = list(every)
    for i in range(0, len(every), 400):
        part = every[i:i + 400]
        marks = ",".join("?" for _ in part)
        for r in conn.execute(
                f"SELECT a.id, COALESCE(a.canonical_artist_id, a.id), c.name FROM lib2_artists a "
                f"JOIN lib2_artists c ON c.id = COALESCE(a.canonical_artist_id, a.id) "
                f"WHERE a.id IN ({marks})", part):
            canon[int(r[0])] = int(r[1])
            label[int(r[1])] = r[2]
    members = _member_ids(conn)
    listed = _visible(conn, {c for c in canon.values() if c in members})

    out = {}
    for name, tiers in cand.items():
        for tier in tiers:
            hits = sorted({canon[i] for i in tier if canon.get(i) in listed})
            if hits:
                # two artists with one name: the one spelled exactly as asked
                exact = [h for h in hits if label.get(h) == name]
                pick = (exact or hits)[0]
                out[name] = (pick, label.get(pick) or name)
                break
    return out


def watch_state(conn, profile_id, items, library_provider_ids):
    """{name: (watching, watchlist id)} by a provider id the page knows, an id
    the library artist carries, or the watched name (folded key). one read of
    the profile's watchlist for the whole batch."""
    from core.text.normalize import normalize_key
    from core.watchlist_sources import ARTIST_ID_COLUMNS

    cols = ", ".join(ARTIST_ID_COLUMNS)
    by_id, by_name = {}, {}
    for r in conn.execute(
            f"SELECT artist_name, {cols} FROM watchlist_artists WHERE profile_id = ?",
            (int(profile_id),)):
        ids = [str(v) for v in r[1:] if v not in (None, "")]
        row_id = ids[0] if ids else ""
        for v in ids:
            by_id.setdefault(v, row_id)
        key = normalize_key(r[0] or "")
        if key:
            by_name.setdefault(key, row_id)

    out = {}
    for it in items:
        name = it["name"]
        hit = None
        for pid in [it["provider_id"], *library_provider_ids.get(name, ())]:
            if pid and pid in by_id:
                hit = by_id[pid]
                break
        if hit is None:
            hit = by_name.get(normalize_key(name))
        out[name] = (hit is not None, hit or None)
    return out


def _library_provider_ids(conn, matched):
    """{name: [provider ids]} of each matched library artist (#1308: a watch
    added from the artist page stored the library artist's provider id)"""
    from core.watchlist_sources import library_artist_provider_ids
    out = {}
    cur = conn.cursor()
    for name, (aid, _label) in matched.items():
        try:
            out[name] = [str(v) for v in library_artist_provider_ids(cur, str(aid)) if v]
        except Exception:  # noqa: BLE001 - the name match still answers
            logger.debug("library provider ids failed for %s", aid, exc_info=True)
    return out


def register_routes(bp):
    @bp.route("/library/check", methods=["POST"])
    @require_api_key
    def library_check():
        """Batch artist check for page badges.

        Body: {"artists": ["Name", {"name", "provider_id"?, "albums"?: true}, ...]}
        (at most 200). Answers {"artists": [{name, in_library, artist_id,
        library_name, watching, watch_id, albums?: [{id, title}]}]} in the
        order asked. Exact name matching only, no provider calls.
        """
        body = request.get_json(silent=True) or {}
        items = _items(body.get("artists"))
        if not items:
            return api_error("BAD_REQUEST", "Provide 'artists': a list of names.", 400)
        profile_id = parse_profile_id(request)
        from contextlib import closing

        from core.library_scope import _explicit_scope, library_scope_for_profile, set_library_scope

        token = set_library_scope(library_scope_for_profile(profile_id))
        try:
            db = get_database()
            with closing(db._get_connection()) as conn:
                names = [it["name"] for it in items]
                matched = match_artists(db, conn, names)
                watched = watch_state(conn, profile_id, items, _library_provider_ids(conn, matched))
                from core.library2 import v1_ids
                pub = {}
                if matched:
                    natives = sorted({aid for aid, _l in matched.values()})
                    rows = v1_ids.publicize(conn, "artist", [{"id": aid} for aid in natives])
                    pub = {aid: str(row["id"]) for aid, row in zip(natives, rows, strict=True)}
            out = []
            for it in items:
                name = it["name"]
                hit = matched.get(name)
                watching, watch_id = watched.get(name, (False, None))
                row = {
                    "name": name,
                    "in_library": hit is not None,
                    "artist_id": pub.get(hit[0]) if hit else None,
                    "library_name": hit[1] if hit else None,
                    "watching": watching,
                    "watch_id": watch_id,
                }
                if it["albums"]:
                    row["albums"] = _albums(db, hit[0]) if hit else []
                out.append(row)
            return api_success({"artists": out})
        except Exception:
            logger.exception("library check failed")
            return api_error("LIBRARY_ERROR", "Library check failed.", 500)
        finally:
            _explicit_scope.reset(token)


def _albums(db, native_artist_id):
    """[{id, title}] of one artist's albums, under the ids v1 shows"""
    from .library import _pub
    rows = db.api_get_albums_by_artist(native_artist_id)
    return [{"id": str(a.get("id")), "title": a.get("title") or ""}
            for a in _pub("album", rows) if a.get("id") is not None]
