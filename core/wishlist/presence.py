"""Wishlist lookup helpers for search and library checks."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def presence_key(title: str, artist: str) -> str:
    """The ``title|||artist`` identity every presence comparison folds to.

    One function rather than one per caller, because the wishlist keys and the
    catalogue keys are compared against the *same* search result: if they folded
    differently, a track could be reported wishlisted but not owned purely
    because of how each side spelled its lowercase. ``normalize_name`` is the
    catalogue's own dedup fold (casefold, not ``lower()``, so non-ASCII names
    actually fold), which is what makes the two sides comparable.
    """
    from core.library2.importer import normalize_name

    return f"{normalize_name(str(title or ''))}|||{normalize_name(str(artist or ''))}"


def load_wishlist_keys(cursor, profile_id: int) -> set[str]:
    """Build a set of `name|||artist` keys from the wishlist for fast lookup.

    Try the profile-aware schema first; fall back to the legacy schema if
    profile_id column is missing (older DBs). Errors at any level are
    swallowed — wishlist annotation is best-effort.
    """
    keys: set[str] = set()

    def _absorb(rows):
        for wr in rows:
            try:
                wd = json.loads(wr[0]) if isinstance(wr[0], str) else {}
                wname = wd.get("name") or ""
                wartists = wd.get("artists", [])
                if wartists:
                    first = wartists[0]
                    wa = first.get("name", "") if isinstance(first, dict) else str(first)
                else:
                    wa = ""
                if wname:
                    keys.add(presence_key(wname, wa))
            except Exception as e:
                logger.debug("parse wishlist row failed: %s", e)

    try:
        cursor.execute("SELECT spotify_data FROM wishlist_tracks WHERE profile_id = ?", (profile_id,))
        _absorb(cursor.fetchall())
        return keys
    except Exception as e:
        logger.debug("profile-aware wishlist query failed: %s", e)

    try:
        cursor.execute("SELECT spotify_data FROM wishlist_tracks")
        _absorb(cursor.fetchall())
    except Exception as e:
        logger.debug("legacy wishlist query failed: %s", e)
    return keys


__all__ = ["load_wishlist_keys", "presence_key"]
