"""Curated studio-family presets for the Studios watchlist.

A "family" is just a convenience grouping of individual production companies (e.g. Disney =
Pixar + Marvel + Lucasfilm + …). Following a family is PURELY SUGAR over the existing
per-studio follow: each member becomes its own ``video_watchlist`` studio row, so a user can
just as easily follow ONE member (only Pixar) and unfollow the rest. There is no "group"
entity — nothing here changes the watchlist schema or the scan; it only helps you find and
bulk-add a set of related studios.

Member tmdb ids are TMDB company ids, verified live. Pure data + accessors (no I/O), so the
API layer wires the followed-state + logos on top.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# id: a stable slug for the family; name/blurb for the picker; members are (tmdb_id, name).
_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "disney",
        "name": "Disney",
        "blurb": "Walt Disney Pictures, Pixar, Marvel, Lucasfilm, Disney Animation & 20th Century.",
        "members": [
            {"tmdb_id": 2, "name": "Walt Disney Pictures"},
            {"tmdb_id": 6125, "name": "Walt Disney Animation Studios"},
            {"tmdb_id": 3, "name": "Pixar"},
            {"tmdb_id": 420, "name": "Marvel Studios"},
            {"tmdb_id": 1, "name": "Lucasfilm Ltd."},
            {"tmdb_id": 127928, "name": "20th Century Studios"},
        ],
    },
    {
        "id": "universal",
        "name": "Universal",
        "blurb": "Universal Pictures, Illumination, DreamWorks Animation, Focus Features & Blumhouse.",
        "members": [
            {"tmdb_id": 33, "name": "Universal Pictures"},
            {"tmdb_id": 6704, "name": "Illumination"},
            {"tmdb_id": 521, "name": "DreamWorks Animation"},
            {"tmdb_id": 10146, "name": "Focus Features"},
            {"tmdb_id": 3172, "name": "Blumhouse Productions"},
        ],
    },
    {
        "id": "warner",
        "name": "Warner Bros.",
        "blurb": "Warner Bros. Pictures, New Line Cinema & Castle Rock.",
        "members": [
            {"tmdb_id": 174, "name": "Warner Bros. Pictures"},
            {"tmdb_id": 12, "name": "New Line Cinema"},
            {"tmdb_id": 97, "name": "Castle Rock Entertainment"},
        ],
    },
    {
        "id": "sony",
        "name": "Sony / Columbia",
        "blurb": "Columbia Pictures, Sony Pictures Animation & TriStar.",
        "members": [
            {"tmdb_id": 5, "name": "Columbia Pictures"},
            {"tmdb_id": 2251, "name": "Sony Pictures Animation"},
            {"tmdb_id": 559, "name": "TriStar Pictures"},
        ],
    },
    {
        "id": "paramount",
        "name": "Paramount",
        "blurb": "Paramount Pictures & Nickelodeon Movies.",
        "members": [
            {"tmdb_id": 4, "name": "Paramount Pictures"},
            {"tmdb_id": 2348, "name": "Nickelodeon Movies"},
        ],
    },
]


def studio_presets() -> List[Dict[str, Any]]:
    """The curated families, deep-ish copied so callers can annotate members freely."""
    return [{**p, "members": [dict(m) for m in p["members"]]} for p in _PRESETS]


def preset_member_ids() -> List[int]:
    """Every member tmdb id across all presets (deduped, order-stable) — for a single
    batched followed-state / logo lookup."""
    seen: set = set()
    out: List[int] = []
    for p in _PRESETS:
        for m in p["members"]:
            tid = m["tmdb_id"]
            if tid not in seen:
                seen.add(tid)
                out.append(tid)
    return out


def get_preset(preset_id: Any) -> Optional[Dict[str, Any]]:
    """One family by id (copied), or None."""
    for p in studio_presets():
        if p["id"] == preset_id:
            return p
    return None
