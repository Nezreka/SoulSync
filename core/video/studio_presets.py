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

# id: a stable slug for the family; name/blurb for the picker; members are (tmdb_id, name,
# logo). Logos are baked in (verified TMDB logo URLs) so the picker opens INSTANTLY — no
# per-open TMDB round-trips for ~20 companies. If a logo URL ever 404s the UI falls back to
# the studio name, so a stale path degrades gracefully rather than breaking.
_L = "https://image.tmdb.org/t/p/w500"
_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "disney",
        "name": "Disney",
        "blurb": "Walt Disney Pictures, Pixar, Marvel, Lucasfilm, Disney Animation & 20th Century.",
        "members": [
            {"tmdb_id": 2, "name": "Walt Disney Pictures", "logo": _L + "/wdrCwmRnLFJhEoH8GSfymY85KHT.png"},
            {"tmdb_id": 6125, "name": "Walt Disney Animation Studios", "logo": _L + "/8bH86UPmMP8hlITzXV3XgV9eaAc.png"},
            {"tmdb_id": 3, "name": "Pixar", "logo": _L + "/1TjvGVDMYsj6JBxOAkUHpPEwLf7.png"},
            {"tmdb_id": 420, "name": "Marvel Studios", "logo": _L + "/hUzeosd33nzE5MCNsZxCGEKTXaQ.png"},
            {"tmdb_id": 1, "name": "Lucasfilm Ltd.", "logo": _L + "/tlVSws0RvvtPBwViUyOFAO0vcQS.png"},
            {"tmdb_id": 127928, "name": "20th Century Studios", "logo": _L + "/h0rjX5vjW5r8yEnUBStFarjcLT4.png"},
        ],
    },
    {
        "id": "universal",
        "name": "Universal",
        "blurb": "Universal Pictures, Illumination, DreamWorks Animation, Focus Features & Blumhouse.",
        "members": [
            {"tmdb_id": 33, "name": "Universal Pictures", "logo": _L + "/8lvHyhjr8oUKOOy2dKXoALWKdp0.png"},
            {"tmdb_id": 6704, "name": "Illumination", "logo": _L + "/fOG2oY4m1YuYTQh4bMqqZkmgOAI.png"},
            {"tmdb_id": 521, "name": "DreamWorks Animation", "logo": _L + "/3BPX5VGBov8SDqTV7wC1L1xShAS.png"},
            {"tmdb_id": 10146, "name": "Focus Features", "logo": _L + "/xnFIOeq5cKw09kCWqV7foWDe4AA.png"},
            {"tmdb_id": 3172, "name": "Blumhouse Productions", "logo": _L + "/rzKluDcRkIwHZK2pHsiT667A2Kw.png"},
        ],
    },
    {
        "id": "warner",
        "name": "Warner Bros.",
        "blurb": "Warner Bros. Pictures, New Line Cinema & Castle Rock.",
        "members": [
            {"tmdb_id": 174, "name": "Warner Bros. Pictures", "logo": _L + "/zhD3hhtKB5qyv7ZeL4uLpNxgMVU.png"},
            {"tmdb_id": 12, "name": "New Line Cinema", "logo": _L + "/2ycs64eqV5rqKYHyQK0GVoKGvfX.png"},
            {"tmdb_id": 97, "name": "Castle Rock Entertainment", "logo": _L + "/smyTD67uNsdaySnnjVDmh0kyotp.png"},
        ],
    },
    {
        "id": "sony",
        "name": "Sony / Columbia",
        "blurb": "Columbia Pictures, Sony Pictures Animation & TriStar.",
        "members": [
            {"tmdb_id": 5, "name": "Columbia Pictures", "logo": _L + "/71BqEFAF4V3qjjMPCpLuyJFB9A.png"},
            {"tmdb_id": 2251, "name": "Sony Pictures Animation", "logo": _L + "/5ilV5mH3gxTEU7p5wjxptHvXkyr.png"},
            {"tmdb_id": 559, "name": "TriStar Pictures", "logo": _L + "/eC0bWHVjnjUducyA6YFoEFqnPMC.png"},
        ],
    },
    {
        "id": "paramount",
        "name": "Paramount",
        "blurb": "Paramount Pictures & Nickelodeon Movies.",
        "members": [
            {"tmdb_id": 4, "name": "Paramount Pictures", "logo": _L + "/jay6WcMgagAklUt7i9Euwj1pzTF.png"},
            {"tmdb_id": 2348, "name": "Nickelodeon Movies", "logo": _L + "/m31fQvZJuUvAgxoqTiCGYFBfZYe.png"},
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
