"""Compile a smart-collection rule set into a parameterized SQL WHERE clause.

Pure and I/O-free so it's fully unit-testable: give it a definition and a media
type, get back ``(where_sql, params)`` that the DB layer ANDs onto the owned-item
base query. The DB never sees raw rule input — every value is a bound parameter
(field/column names come from this module's own registry, never from input), so
this is injection-safe by construction.

Definition shape (JSON stored in ``collection_definitions.definition``)::

    {
      "match": "all",          # "all" (AND) | "any" (OR); default "all"
      "rules": [
        {"field": "genre",  "op": "in",      "value": ["Action", "Sci-Fi"]},
        {"field": "year",   "op": "between", "value": [1980, 1989]},
        {"field": "rating", "op": "gte",     "value": 7.0},
        {"field": "director","op": "is",     "value": "Christopher Nolan"}
      ]
    }
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

_TABLE = {"movie": "movies", "show": "shows"}


class SmartFilterError(ValueError):
    """A rule set that can't be compiled (unknown field/op, bad value, empty)."""


# ── Column-backed fields ────────────────────────────────────────────────────
# name -> {col, type, media}. ``col`` is a SQL expression (or a per-media dict).
_NUM = "number"
_TXT = "text"
_DATE = "date"

_COLUMN_FIELDS: Dict[str, Dict[str, Any]] = {
    "year":           {"col": "{t}.year",            "type": _NUM,  "media": ("movie", "show")},
    "rating":         {"col": "{t}.rating",          "type": _NUM,  "media": ("movie", "show")},
    "critic_rating":  {"col": "movies.rating_critic","type": _NUM,  "media": ("movie",)},
    "imdb_rating":    {"col": "{t}.imdb_rating",     "type": _NUM,  "media": ("movie", "show")},
    "rt_rating":      {"col": "{t}.rt_rating",       "type": _NUM,  "media": ("movie", "show")},
    "runtime":        {"col": "{t}.runtime_minutes", "type": _NUM,  "media": ("movie", "show")},
    "studio":         {"col": "movies.studio",       "type": _TXT,  "media": ("movie",)},
    "network":        {"col": "shows.network",       "type": _TXT,  "media": ("show",)},
    "content_rating": {"col": "{t}.content_rating",  "type": _TXT,  "media": ("movie", "show")},
    "status":         {"col": "{t}.status",          "type": _TXT,  "media": ("movie", "show")},
    "title":          {"col": "{t}.title",           "type": _TXT,  "media": ("movie", "show")},
    "added":          {"col": "{t}.added_at",        "type": _DATE, "media": ("movie", "show")},
    "released":       {"col": {"movie": "movies.release_date", "show": "shows.first_air_date"},
                       "type": _DATE, "media": ("movie", "show")},
}

_OPS_BY_TYPE = {
    _NUM:  {"is", "gte", "lte", "between"},
    _TXT:  {"is", "is_not", "in", "not_in", "contains"},
    _DATE: {"before", "after", "in_last_days"},
}

# Join/EXISTS-backed fields and franchise handled specially in _build_rule.
_SPECIAL_FIELDS = {"genre", "director", "actor", "resolution", "source", "decade",
                   "franchise", "watched"}

_TRUTHY = {True, "1", "true", "True", "yes", "watched"}


def _col_for(spec: Dict[str, Any], media_type: str) -> str:
    col = spec["col"]
    if isinstance(col, dict):
        return col[media_type]
    return col.format(t=_TABLE[media_type])


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SmartFilterError(f"expected a number, got {value!r}") from None


def _build_column_rule(field: str, spec: Dict[str, Any], op: str, value: Any, mt: str) -> Tuple[str, list]:
    ftype = spec["type"]
    if op not in _OPS_BY_TYPE[ftype]:
        raise SmartFilterError(f"operator {op!r} is not valid for {field!r}")
    col = _col_for(spec, mt)

    if ftype == _NUM:
        if op == "is":
            return f"{col} = ?", [_num(value)]
        if op == "gte":
            return f"{col} >= ?", [_num(value)]
        if op == "lte":
            return f"{col} <= ?", [_num(value)]
        if op == "between":
            lo, hi = _as_list(value)[:2] if len(_as_list(value)) >= 2 else (None, None)
            if lo is None or hi is None:
                raise SmartFilterError(f"'between' needs [low, high], got {value!r}")
            return f"{col} BETWEEN ? AND ?", [_num(lo), _num(hi)]

    if ftype == _TXT:
        if op == "is":
            return f"LOWER({col}) = LOWER(?)", [str(value)]
        if op == "is_not":
            return f"({col} IS NULL OR LOWER({col}) <> LOWER(?))", [str(value)]
        if op == "contains":
            return f"{col} LIKE ? ESCAPE '\\'", [f"%{_like_escape(str(value))}%"]
        if op in ("in", "not_in"):
            vals = [str(v) for v in _as_list(value)]
            if not vals:
                raise SmartFilterError(f"{op!r} needs at least one value")
            placeholders = ", ".join("LOWER(?)" for _ in vals)
            frag = f"LOWER({col}) IN ({placeholders})"
            if op == "not_in":
                frag = f"({col} IS NULL OR NOT ({frag}))"
            return frag, vals

    if ftype == _DATE:
        if op == "after":
            return f"{col} >= ?", [str(value)]
        if op == "before":
            return f"{col} <= ?", [str(value)]
        if op == "in_last_days":
            days = int(_num(value))
            return f"{col} >= datetime('now', ?)", [f"-{days} days"]

    raise SmartFilterError(f"unhandled {field!r}/{op!r}")


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _person_exists(mt: str, department: str, job: str | None, op: str, value: Any) -> Tuple[str, list]:
    fk = "movie_id" if mt == "movie" else "show_id"
    owner = f"{_TABLE[mt]}.id"
    names = [str(v) for v in _as_list(value)] if op in ("is", "in") else None
    if op in ("is", "in"):
        if not names:
            raise SmartFilterError("person filter needs a name")
        name_clause = "LOWER(p.name) IN (" + ", ".join("LOWER(?)" for _ in names) + ")"
    else:
        raise SmartFilterError(f"operator {op!r} is not valid for a person filter")
    job_clause = " AND c.job = ?" if job else ""
    sql = (
        f"EXISTS (SELECT 1 FROM credits c JOIN people p ON p.id = c.person_id "
        f"WHERE c.{fk} = {owner} AND c.department = ?{job_clause} AND {name_clause})"
    )
    params: list = [department]
    if job:
        params.append(job)
    params.extend(names)
    return sql, params


def _media_files_exists(mt: str, column: str, op: str, value: Any) -> Tuple[str, list]:
    if op not in ("in", "not_in"):
        raise SmartFilterError(f"operator {op!r} is not valid for a media-file filter")
    vals = [str(v) for v in _as_list(value)]
    if not vals:
        raise SmartFilterError(f"{op!r} needs at least one value")
    placeholders = ", ".join("LOWER(?)" for _ in vals)
    if mt == "movie":
        inner = (
            f"SELECT 1 FROM media_files mf WHERE mf.movie_id = movies.id "
            f"AND LOWER(mf.{column}) IN ({placeholders})"
        )
    else:
        inner = (
            f"SELECT 1 FROM media_files mf JOIN episodes e ON e.id = mf.episode_id "
            f"WHERE e.show_id = shows.id AND LOWER(mf.{column}) IN ({placeholders})"
        )
    frag = f"EXISTS ({inner})"
    if op == "not_in":
        frag = f"NOT {frag}"
    return frag, vals


def _named_link_exists(mt: str, field: str, op: str, value: Any) -> Tuple[str, list]:
    """EXISTS over a normalised name-link table (studios / networks) — the multi-valued
    replacement for the old single studio/network column, so a title matches EVERY company
    it was made by. Supports the same ops the text column did (is/is_not/in/not_in/contains)."""
    if field == "studio":
        if mt != "movie":
            raise SmartFilterError("field 'studio' does not apply to shows")
        link, owner, ref, fk = "movie_studios", "movie_id", "studios", "studio_id"
    else:  # network
        if mt != "show":
            raise SmartFilterError("field 'network' does not apply to movies")
        link, owner, ref, fk = "show_networks", "show_id", "networks", "network_id"
    base = (f"SELECT 1 FROM {link} lt JOIN {ref} r ON r.id = lt.{fk} "
            f"WHERE lt.{owner} = {_TABLE[mt]}.id")
    if op in ("in", "not_in"):
        vals = [str(v) for v in _as_list(value) if str(v).strip()]
        if not vals:
            raise SmartFilterError(f"{field!r} needs at least one value")
        ph = ", ".join("LOWER(?)" for _ in vals)
        frag = f"EXISTS ({base} AND LOWER(r.name) IN ({ph}))"
        return (f"NOT {frag}" if op == "not_in" else frag), vals
    if op in ("is", "is_not"):
        frag = f"EXISTS ({base} AND LOWER(r.name) = LOWER(?))"
        return (f"NOT {frag}" if op == "is_not" else frag), [str(value)]
    if op == "contains":
        frag = f"EXISTS ({base} AND LOWER(r.name) LIKE LOWER(?) ESCAPE '\\')"
        return frag, ["%" + _like_escape(str(value)) + "%"]
    raise SmartFilterError(f"operator {op!r} is not valid for {field!r}")


def _build_rule(rule: Dict[str, Any], mt: str) -> Tuple[str, list]:
    field = rule.get("field")
    op = rule.get("op")
    value = rule.get("value")
    if not field or not op:
        raise SmartFilterError(f"rule missing field/op: {rule!r}")

    # studio/network are still listed in _COLUMN_FIELDS (for the field-picker schema + op
    # validation) but resolve through their link tables now, not the legacy scalar column.
    if field in ("studio", "network"):
        spec = _COLUMN_FIELDS[field]
        if mt not in spec["media"]:
            raise SmartFilterError(f"field {field!r} does not apply to {mt}s")
        if op not in _OPS_BY_TYPE[_TXT]:
            raise SmartFilterError(f"operator {op!r} is not valid for {field!r}")
        return _named_link_exists(mt, field, op, value)

    if field in _COLUMN_FIELDS:
        spec = _COLUMN_FIELDS[field]
        if mt not in spec["media"]:
            raise SmartFilterError(f"field {field!r} does not apply to {mt}s")
        return _build_column_rule(field, spec, op, value, mt)

    if field == "genre":
        if op not in ("in", "not_in"):
            raise SmartFilterError(f"operator {op!r} is not valid for 'genre'")
        link, owner = ("movie_genres", "movie_id") if mt == "movie" else ("show_genres", "show_id")
        vals = [str(v) for v in _as_list(value)]
        if not vals:
            raise SmartFilterError("'genre' needs at least one value")
        placeholders = ", ".join("LOWER(?)" for _ in vals)
        frag = (
            f"EXISTS (SELECT 1 FROM {link} lt JOIN genres g ON g.id = lt.genre_id "
            f"WHERE lt.{owner} = {_TABLE[mt]}.id AND LOWER(g.name) IN ({placeholders}))"
        )
        if op == "not_in":
            frag = f"NOT {frag}"
        return frag, vals

    if field == "director":
        return _person_exists(mt, "crew", "Director", op, value)
    if field == "actor":
        return _person_exists(mt, "cast", None, op, value)
    if field == "resolution":
        return _media_files_exists(mt, "resolution", op, value)
    if field == "source":
        return _media_files_exists(mt, "release_source", op, value)

    if field == "decade":
        col = f"{_TABLE[mt]}.year"
        decades = [int(_num(v)) for v in _as_list(value)]
        if not decades:
            raise SmartFilterError("'decade' needs at least one decade start year")
        parts = [f"({col} >= ? AND {col} <= ?)" for _ in decades]
        params: list = []
        for d in decades:
            base = (d // 10) * 10
            params.extend([base, base + 9])
        return "(" + " OR ".join(parts) + ")", params

    if field == "watched":
        # Server watch state: a movie is watched when it has plays; a show when
        # any episode has been viewed (Plex viewedLeafCount / Jellyfin UserData).
        if op != "is":
            raise SmartFilterError(f"operator {op!r} is not valid for 'watched'")
        col = ("COALESCE(movies.play_count, 0) > 0" if mt == "movie"
               else "COALESCE(shows.watched_episodes, 0) > 0")
        want = value in _TRUTHY
        return (col if want else f"NOT ({col})"), []

    if field == "franchise":
        if mt != "movie":
            raise SmartFilterError("'franchise' only applies to movies")
        if op == "exists":
            return "movies.tmdb_collection_id IS NOT NULL", []
        if op in ("is", "in"):
            ids = [int(_num(v)) for v in _as_list(value)]
            if not ids:
                raise SmartFilterError("'franchise' needs a collection id")
            placeholders = ", ".join("?" for _ in ids)
            return f"movies.tmdb_collection_id IN ({placeholders})", ids
        raise SmartFilterError(f"operator {op!r} is not valid for 'franchise'")

    raise SmartFilterError(f"unknown field {field!r}")


def compile_rules(definition: Dict[str, Any], media_type: str) -> Tuple[str, list]:
    """Compile a smart definition into ``(where_sql, params)``.

    ``where_sql`` is a single parenthesized boolean expression combining every
    rule with AND (``match == 'all'``) or OR (``match == 'any'``). Raises
    :class:`SmartFilterError` on an empty/invalid rule set so a bad definition is
    surfaced rather than silently matching everything.
    """
    if media_type not in _TABLE:
        raise SmartFilterError(f"media_type must be 'movie' or 'show', got {media_type!r}")
    rules = (definition or {}).get("rules") or []
    if not rules:
        raise SmartFilterError("smart collection has no rules")

    match = str((definition or {}).get("match", "all")).lower()
    joiner = " OR " if match in ("any", "or") else " AND "

    frags: List[str] = []
    params: list = []
    for rule in rules:
        frag, ps = _build_rule(rule, media_type)
        frags.append(frag)
        params.extend(ps)

    return "(" + joiner.join(frags) + ")", params


def known_fields(media_type: str) -> List[str]:
    """The field names usable for a given media type (for the UI's field picker)."""
    out = [f for f, spec in _COLUMN_FIELDS.items() if media_type in spec["media"]]
    out += ["genre", "director", "actor", "resolution", "source", "decade", "watched"]
    if media_type == "movie":
        out.append("franchise")
    return sorted(out)


# UI metadata for the non-column (join/special) fields: (value-widget kind, ops).
_SPECIAL_META = {
    "genre":      ("multi",     ["in", "not_in"]),
    "director":   ("person",    ["is", "in"]),
    "actor":      ("person",    ["is", "in"]),
    "resolution": ("multi",     ["in", "not_in"]),
    "source":     ("multi",     ["in", "not_in"]),
    "decade":     ("decade",    ["in"]),
    "franchise":  ("franchise", ["exists", "is", "in"]),
    "watched":    ("bool",      ["is"]),
}
_FIELD_LABELS = {
    "year": "Year", "rating": "Audience rating", "critic_rating": "Critic rating",
    "imdb_rating": "IMDb rating", "rt_rating": "Rotten Tomatoes", "runtime": "Runtime (min)",
    "studio": "Studio", "network": "Network", "content_rating": "Content rating",
    "status": "Status", "title": "Title", "added": "Date added", "released": "Release date",
    "genre": "Genre", "director": "Director", "actor": "Actor", "resolution": "Resolution",
    "source": "Release source", "decade": "Decade", "franchise": "Franchise",
    "watched": "Watched",
}
# Suggested value options for the pick-list widgets.
_STATIC_OPTIONS = {
    "resolution": ["480p", "720p", "1080p", "2160p"],
    "source": ["bluray", "web-dl", "webrip", "hdtv"],
    "decade": [1960, 1970, 1980, 1990, 2000, 2010, 2020],
}


def field_schema(media_type: str) -> List[Dict[str, Any]]:
    """Per-field UI metadata (label, value type, allowed ops, static options) for
    the rule builder — derived from the same registry the compiler uses, so the
    UI and the SQL never drift."""
    out = []
    for f in known_fields(media_type):
        if f in _COLUMN_FIELDS:
            ftype = _COLUMN_FIELDS[f]["type"]
            ops = sorted(_OPS_BY_TYPE[ftype])
            widget = ftype
        else:
            widget, ops = _SPECIAL_META[f]
        entry = {"field": f, "label": _FIELD_LABELS.get(f, f.replace("_", " ").title()),
                 "type": widget, "ops": ops}
        if f in _STATIC_OPTIONS:
            entry["options"] = _STATIC_OPTIONS[f]
        out.append(entry)
    return out


__all__ = ["compile_rules", "known_fields", "field_schema", "SmartFilterError"]
