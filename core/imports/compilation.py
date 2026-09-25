"""Deciding that an album belongs to Various Artists rather than to one contributor.

A compilation or soundtrack is imported one track at a time, and the import
context's artist is whoever the DOWNLOAD was for. So one Don Felder track from
the Heavy Metal soundtrack filed the WHOLE album under Don Felder, and doing
that 45 times stacked 45 soundtracks — Shrek, Fifty Shades, Bomb Rush
Cyberfunk — onto a guitarist who appears on one track of each
(sassmastawillis, Sept 22 2026).

The signal has to come from the ALBUM. At this point we hold exactly one track
and cannot count distinct artists across the release, which is why
``is_multi_artist_compilation`` (core/library_reorganize.py) cannot be reused
here — it needs the whole tracklist and runs at reorganize time, where it
decides the folder layout and has no say over who the album is attributed to.

Nothing here touches the TRACK's artist. The library already stores a
per-track ``track_artist`` for exactly the case where it differs from the
album's, so correcting the album to Various Artists is what makes Don Felder
show up on his own track and nowhere else.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

VARIOUS_ARTISTS = "Various Artists"

# The spellings a source actually ships. Same vocabulary as
# ``is_multi_artist_compilation`` so the two agree about what "various" means.
VARIOUS_ARTIST_NAMES = frozenset({
    "various artists",
    "various",
    "va",
    "v.a.",
    "various artist",
    "diverse interpreten",
})

# `album_type` (Spotify), `record_type` (Deezer), `primary_type`/secondary
# types (MusicBrainz). Deezer spells it "compile".
_COMPILATION_TYPES = frozenset({"compilation", "compilations", "compile"})

# MusicBrainz marks a soundtrack as a secondary type on an album, not as a
# primary "compilation" — a film score credited to one composer is NOT a
# various-artists release, so a soundtrack only counts when its own artist
# credit already says various.
_SOUNDTRACK_TYPES = frozenset({"soundtrack", "soundtracks"})


def is_various_artists_name(name: Any) -> bool:
    """True for the spellings sources use for a various-artists credit."""
    return str(name or "").strip().lower() in VARIOUS_ARTIST_NAMES


def _album_credit_names(album_ctx: Dict[str, Any]) -> list:
    """Every artist name the ALBUM itself is credited to."""
    names = []
    artists = album_ctx.get("artists")
    if isinstance(artists, list):
        for entry in artists:
            if isinstance(entry, dict):
                value = entry.get("name", "")
            else:
                value = entry
            if str(value or "").strip():
                names.append(str(value).strip())
    for key in ("album_artist", "albumartist", "artist_name", "artist"):
        value = album_ctx.get(key)
        if isinstance(value, dict):
            value = value.get("name", "")
        if str(value or "").strip():
            names.append(str(value).strip())
    return names


def _album_types(album_ctx: Dict[str, Any]) -> set:
    """The release-type strings the source gave us, lowercased."""
    types = set()
    for key in ("album_type", "record_type", "primary_type", "type"):
        value = album_ctx.get(key)
        if str(value or "").strip():
            types.add(str(value).strip().lower())
    secondary = album_ctx.get("secondary_types")
    if isinstance(secondary, list):
        for entry in secondary:
            if str(entry or "").strip():
                types.add(str(entry).strip().lower())
    return types


def is_compilation_album_context(album_ctx: Optional[Dict[str, Any]]) -> bool:
    """Whether the source says this release is a various-artists compilation.

    Two independent signals, either is enough:

    * the album is credited to Various Artists in its own right, or
    * the source typed it a compilation.

    A soundtrack alone is deliberately NOT enough: a score credited to one
    composer is a normal album, and treating every soundtrack as various
    artists would move those off their composer.
    """
    if not isinstance(album_ctx, dict) or not album_ctx:
        return False

    credits = _album_credit_names(album_ctx)
    if credits and all(is_various_artists_name(name) for name in credits):
        return True

    types = _album_types(album_ctx)
    if types & _COMPILATION_TYPES:
        return True
    if types & _SOUNDTRACK_TYPES and any(is_various_artists_name(n) for n in credits):
        return True
    return False


def _detect_enabled() -> bool:
    """The same switch the reorganize planner reads, so one setting governs
    both halves of "this is a compilation"."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get(
            "file_organization.detect_multi_artist_compilations", True))
    except Exception:
        return True


def compilation_album_artist(
    album_ctx: Optional[Dict[str, Any]],
    current_artist: str,
    *,
    enabled: Optional[bool] = None,
) -> Optional[str]:
    """``"Various Artists"`` when this album should be attributed there.

    Returns ``None`` to leave the import's artist alone — which is the answer
    for every ordinary release, and also when the import already resolved to a
    various-artists credit, so a caller can treat a return value as "this
    changed".
    """
    if enabled is None:
        enabled = _detect_enabled()
    if not enabled:
        return None
    if is_various_artists_name(current_artist):
        return None
    if not is_compilation_album_context(album_ctx):
        return None
    return VARIOUS_ARTISTS
