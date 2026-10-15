"""the 'first listed artist' rule for collab album artists.

a source can name a collab as one string ("grandson & K.Flay") while its
artist list holds only the primary ("grandson"). the old rule only split
when the list had 2+ entries, so that single landed in a combined folder
(discord, SeadogsBooty). the list's one name counts when the combined
string starts with it and a separator follows, so "Simon & Garfunkel"
listed as itself stays whole.
"""

from __future__ import annotations

import re

_SEPARATOR_RE = re.compile(
    r"^\s*(?:,|&|\+|/|;|×|x\s|and\s|with\s|vs\.?\s|feat\.?\s|ft\.?\s|featuring\s)",
    re.IGNORECASE,
)


def _name(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("name") or "").strip()
    return str(entry or "").strip()


def listed_primary_of(combined: str, listed) -> str:
    """the listed name when ``combined`` is that name plus more artists, else ''."""
    combined = (combined or "").strip()
    name = _name(listed)
    if not combined or not name or len(combined) <= len(name):
        return ""
    if not combined.casefold().startswith(name.casefold()):
        return ""
    rest = combined[len(name):]
    if not _SEPARATOR_RE.match(rest):
        return ""
    return name


def first_listed_artist(album_artist: str, artists_list) -> str:
    """the album artist under 'first listed': the list's first entry when it
    names several, or its one entry when the album artist is that entry plus
    collaborators. '' when the list can't tell (the caller keeps what it had)."""
    if not artists_list:
        return ""
    if len(artists_list) > 1:
        return _name(artists_list[0])
    return listed_primary_of(album_artist, artists_list[0])
