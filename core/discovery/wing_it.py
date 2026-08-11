"""Wing It stubs — the stand-in a track gets when discovery finds no catalogue match.

When a provider search returns nothing confident enough, discovery stores a stub
``matched_data`` built from the *source's own* artist/title so the track still
appears in the mirror and still flows through the download pipeline. The stub is
identified by an id carrying the ``wing_it_`` prefix.

The id used to be ``f"wing_it_{hash(f'{artist}_{track}') % 100000}"``. ``hash()`` on
a str is salted per interpreter (PEP 456), so the same track got a different id in
every process — and ``% 100000`` collides freely on top of that. A stub id is written
to ``mirrored_playlist_tracks.extra_data.matched_data.id`` and outlives the process
that produced it, so it has to be reproducible to identify anything at all.
"""

from __future__ import annotations

import hashlib
from typing import Any

STUB_ID_PREFIX = "wing_it_"

# Names that carry no signal — searching for them finds noise, not the track.
# Compared case- and whitespace-insensitively against artist AND title.
_PLACEHOLDER_NAMES = frozenset({
    "",
    "-",
    "n/a",
    "none",
    "null",
    "unknown",
    "unknown artist",
    "unknown title",
    "unknown track",
    "va",
    "various artists",
})


def stub_track_id(artist_name: Any, track_name: Any) -> str:
    """Deterministic id for the Wing It stub of ``artist_name`` / ``track_name``.

    Stable across processes and releases: the same pair always yields the same id,
    so a stub written to the database still resolves after a restart."""
    artist = str(artist_name or "")
    track = str(track_name or "")
    # Length-prefixed, not just underscore-joined — ("A_B", "C") and ("A", "B_C")
    # must not collide just because they'd concatenate to the same string.
    key = f"{len(artist)}:{artist}{len(track)}:{track}"
    digest = hashlib.md5(key.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{STUB_ID_PREFIX}{digest}"


def is_stub_id(value: Any) -> bool:
    """Is ``value`` the id of a Wing It stub (either id scheme)?"""
    return str(value or "").startswith(STUB_ID_PREFIX)


def _is_placeholder(value: Any) -> bool:
    return " ".join(str(value or "").split()).casefold() in _PLACEHOLDER_NAMES


def stub_is_searchable(artist_name: Any, track_name: Any) -> bool:
    """Is there enough here to search a source with?

    "No catalogue match" is not the same as "no metadata". A stub built from a
    YouTube Music catalog response carries the real artist, title and duration —
    it just has no provider *release* behind it. A stub built from a nameless
    entry carries nothing. Only the latter is unsearchable."""
    return not _is_placeholder(artist_name) and not _is_placeholder(track_name)


def wishlist_guesses_enabled() -> bool:
    """Whether unverified Wing It guesses may be auto-added to the wishlist.

    Off by default: a stub is a guess, and the Wing It Pool
    (``MusicDatabase.get_wing_it_pool``) is the surface for resolving guesses by
    hand. Turning it on suits libraries where the pool is too large to work
    through — the guesses are then searched like any other wishlist track.
    Isolated so tests can monkeypatch without a config manager."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("wishlist.wing_it_guesses", False))
    except Exception:
        return False


def should_wishlist_stub(artist_name: Any, track_name: Any) -> bool:
    """The single gate every "skip wing-it tracks" site shares, so they agree."""
    return wishlist_guesses_enabled() and stub_is_searchable(artist_name, track_name)


__all__ = [
    "STUB_ID_PREFIX",
    "is_stub_id",
    "should_wishlist_stub",
    "stub_is_searchable",
    "stub_track_id",
    "wishlist_guesses_enabled",
]
