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


__all__ = [
    "STUB_ID_PREFIX",
    "is_stub_id",
    "stub_track_id",
]
