"""SoulSync's own bookkeeping markers, kept out of artist biographies.

The legacy Plex metadata updater (core/plex_client.py, update_artist_biography)
remembers when it last touched an artist by writing ``-updatedAt<date>`` (and
``-IgnoreUpdate`` for an artist it should skip) into the artist's Plex
summary. The library import copied that summary as the biography, so 443
artists on Boulder's install showed "-updatedAt2026-04-13" as their bio, and
the real biography providers never filled the field in because it wasn't
empty (Nov 4 2026).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

_MARKERS = re.compile(r"\s*-(?:updatedAt\d{4}-\d{2}-\d{2}|IgnoreUpdate)\b\s*")


def clean_summary(text: Optional[str]) -> Optional[str]:
    """``text`` without SoulSync's markers; None when nothing else is left."""
    if text is None:
        return None
    cleaned = _MARKERS.sub("\n\n", str(text)).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or None


def repair_artist_summaries(cursor: Any) -> Dict[str, int]:
    """Strip the markers from biographies already stored. Rows that held only
    a marker go back to NULL so a provider can fill them. Idempotent: a
    cleaned row no longer matches."""
    rows = cursor.execute(
        "SELECT id, summary FROM lib2_artists"
        " WHERE summary LIKE '%-updatedAt%' OR summary LIKE '%-IgnoreUpdate%'").fetchall()
    cleared = cleaned = 0
    for row in rows:
        new = clean_summary(row[1])
        if new == row[1]:
            continue
        cursor.execute("UPDATE lib2_artists SET summary=? WHERE id=?", (new, row[0]))
        if new is None:
            cleared += 1
        else:
            cleaned += 1
    return {"cleared": cleared, "cleaned": cleaned}


__all__ = ["clean_summary", "repair_artist_summaries"]
