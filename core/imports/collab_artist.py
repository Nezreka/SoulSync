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


def album_lead_artist(album_ctx, download_artist: str) -> str:
    """the album's first credited artist, when the download was for one of
    its other credited artists. '' otherwise (the caller keeps its artist).

    a split single "3OH!3 versus Innerpartysystem" has side B by
    Innerpartysystem. downloading it for them filed the album under them
    while the folder went under 3OH!3, so one release became two albums
    (#2003). the library row follows the folder: the release's lead.
    """
    if not isinstance(album_ctx, dict):
        return ""
    artists = album_ctx.get("artists")
    if not isinstance(artists, list) or len(artists) < 2:
        return ""
    names = [_name(entry) for entry in artists]
    lead = names[0]
    wanted = (download_artist or "").strip().casefold()
    if not lead or not wanted or lead.casefold() == wanted:
        return ""
    if wanted not in {name.casefold() for name in names[1:]}:
        return ""
    return lead
