"""Wishlist lookup helpers for search and library checks."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def presence_key(title: str, artist: str) -> str:
    """The ``title|||artist`` identity every presence comparison folds to.

    One function rather than one per caller, because the wishlist keys and the
    catalogue keys are compared against the *same* search result: if they folded
    differently, a track could be reported wishlisted but not owned purely
    because of how each side spelled its lowercase.

    ``normalize_key`` rather than the catalogue's ``normalize_name``: both sides
    of every comparison pass through this function, so the fold only has to be
    consistent, and it can therefore be the stronger one. ``normalize_name``
    casefolds but keeps accents and punctuation, so a library "Björk" never
    answered a searched "Bjork" and "AC/DC" never answered "ACDC" -- upstream
    dfaff5a0a found both, on the label watchlist where provider names and
    library names rarely agree on either.
    """
    from core.text.normalize import normalize_key

    return f"{normalize_key(str(title or ''))}|||{normalize_key(str(artist or ''))}"


# a trailing bracket that only says which pressing this is. "(Live)",
# "(Remixes)" and "(Taylor's Version)" are other records and stay.
_EDITION_TAG = re.compile(
    r"\s*[\[\(][^\]\)]*\b(deluxe|special|expanded|extended|bonus|remaster(?:ed)?|"
    r"anniversary|collector'?s?|limited|edition|explicit|clean|re-?issue|mono|stereo)\b"
    r"[^\]\)]*[\]\)]\s*$",
    re.IGNORECASE,
)
# "Kindred EP", "Kindred - EP", "Kindred (EP)", "Hello - Single"
_FORMAT_TAG = re.compile(
    r"(?:\s*[-\u2013\u2014:]\s*(?:ep|single)|\s*[\[\(]\s*(?:ep|single)\s*[\]\)]|\s+ep)\s*$",
    re.IGNORECASE,
)
_ARTIST_SEPARATOR = re.compile(r"^\s*[-\u2013\u2014:]\s+")


def artist_presence_key(name: str) -> str:
    """An artist name as ``presence_key`` folds it, so "Bjork", "BJÖRK" and
    "Björk " are one artist. A name with no letters or digits stays literal."""
    from core.text.normalize import normalize_key

    raw = str(name or "").strip()
    return normalize_key(raw) or raw.lower()


def album_presence_key(title: str, artist: str) -> str:
    """``presence_key`` for an album: the same record keys the same however a
    source punctuates it or names its pressing (#2083).

    "Nevermind (30th Anniversary Super Deluxe)" is Nevermind, "It's A Mother"
    is "It\u2019s a Mother", "Kindred EP" is Kindred, and a title that repeats
    its artist ("Max Cooper - Traum Collection") is the title alone. Only
    bracketed pressing tags come off: ``get_base_album_name`` also drops a bare
    last word, which folds "ABBA Gold" to "abba" and would hide a record that
    really is missing behind a shorter title by the same artist.
    """
    raw = str(title or "").strip()
    artist = str(artist or "").strip()
    base = raw
    if artist and base.lower().startswith(artist.lower()):
        rest = _ARTIST_SEPARATOR.sub("", base[len(artist):], count=1)
        if rest != base[len(artist):] and rest.strip():
            base = rest
    while True:  # "X (Deluxe) (2011 Remaster)", "X - EP (Remastered)"
        stripped = _FORMAT_TAG.sub("", _EDITION_TAG.sub("", base))
        if stripped == base or not stripped.strip():
            break
        base = stripped
    key = presence_key(base, artist)
    if key.startswith("|||"):  # "!!!" and "..." fold to nothing: keep them literal
        return f"{raw.lower()}|||{key[3:]}"
    return key


def load_wishlist_keys(cursor, profile_id: int, artist_aliases=None) -> set[str]:
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
                    if artist_aliases and wa:
                        for alias in artist_aliases(wa) or []:
                            keys.add(presence_key(wname, alias))
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


__all__ = ["album_presence_key", "artist_presence_key", "load_wishlist_keys", "presence_key"]
