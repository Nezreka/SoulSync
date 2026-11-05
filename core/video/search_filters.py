"""Saved filters for the video manual search (SeadogsBooty).

The filtering and sorting itself runs in the browser
(webui/static/video/search-filters.js) on the results already on screen; this
keeps each profile's named filters and makes sure a stored one can only name
fields and operators that script knows. Display only: nothing here touches
the automatic ranking or retries.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# field -> kind, the same names and kinds as search-filters.js
FIELDS = {
    "title": "text", "resolution": "number", "codec": "text", "source": "text",
    "group": "text", "indexer": "text", "size_gb": "number", "age_days": "number",
    "format_score": "number", "seeders": "number", "grabs": "number", "hdr": "text",
    "audio": "text", "meets_profile": "bool",
}
OPS = {
    "contains": ("text",), "not_contains": ("text",),
    "equals": ("text", "number", "bool"), "not_equals": ("text", "number", "bool"),
    "gt": ("number",), "lt": ("number",),
}
MAX_FILTERS = 30
MAX_CONDITIONS = 12
_KEY = "search_filters.%d"


def normalize_filter(raw: Any) -> Dict[str, Any] | None:
    """One saved filter, cleaned, or None when it has no name or no usable
    condition. Conditions naming an unknown field, or an operator that field
    can't take, are dropped."""
    if not isinstance(raw, dict):
        return None
    name = " ".join(str(raw.get("name") or "").split())[:60]
    if not name:
        return None
    conditions: List[Dict[str, str]] = []
    for c in raw.get("conditions") or []:
        if not isinstance(c, dict):
            continue
        field, op = str(c.get("field") or ""), str(c.get("op") or "")
        if field not in FIELDS or FIELDS[field] not in OPS.get(op, ()):
            continue
        conditions.append({"field": field, "op": op, "value": str(c.get("value") or "")[:120]})
        if len(conditions) >= MAX_CONDITIONS:
            break
    if not conditions:
        return None
    return {"name": name, "match": "any" if raw.get("match") == "any" else "all",
            "conditions": conditions}


def normalize_filters(raw: Any) -> List[Dict[str, Any]]:
    """A list of saved filters, cleaned; a later filter with the same name
    replaces the earlier one."""
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw if isinstance(raw, list) else []:
        f = normalize_filter(item)
        if f:
            out.pop(f["name"].lower(), None)
            out[f["name"].lower()] = f
    return list(out.values())[-MAX_FILTERS:]


def load(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    try:
        return normalize_filters(json.loads(db.get_setting(_KEY % int(profile_id)) or "[]"))
    except (TypeError, ValueError):
        return []


def save(db: Any, profile_id: int, filters: Any) -> List[Dict[str, Any]]:
    clean = normalize_filters(filters)
    db.set_setting(_KEY % int(profile_id), json.dumps(clean))
    return clean


__all__ = ["FIELDS", "OPS", "MAX_FILTERS", "normalize_filter", "normalize_filters", "load", "save"]
