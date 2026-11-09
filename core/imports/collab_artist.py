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


# a credit line joins artists with these. ";" and a spaced "/" are never part
# of an act's name, so they alone prove the string is a list (classical
# "Johannes Brahms; Joshua Bell"). the rest can sit inside one act's name
# ("Earth, Wind & Fire", "Simon & Garfunkel") and need a second word for it.
# "Λ" is not one: "Axwell Λ Ingrosso" is a duo's name (#2028).
_HARD_SEPARATOR_RE = re.compile(r"\s*;\s*|\s+/\s+")
_SOFT_SEPARATOR_RE = re.compile(
    r"\s*,\s+|\s+&\s+|\s+(?:(?-i:x)|×|vs\.?|feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)


def credit_segments(name: str) -> list:
    """each artist a credit string names, in order. one entry for a plain name."""
    parts = []
    for piece in _HARD_SEPARATOR_RE.split(str(name or "").strip()):
        parts.extend(p.strip() for p in _SOFT_SEPARATOR_RE.split(piece) if p.strip())
    return parts


def split_credit_primary(album_artist: str, known_names=(), library_has=None) -> str:
    """the first artist of a credit the source gave as one string, '' when
    it can't tell the string is a list.

    "Creed, 3 Doors Down & Finger Eleven" came with no artist list at all,
    so the list rules above had nothing to split and the release got its
    own combined folder (#2028). it counts as a list when it has a ";" or
    " / ", when one of its parts is an artist this download names (the
    track's artist, the watched artist), or when the library already holds
    two of its parts as artists. a known name that starts the credit wins
    whole, so "Axwell & Sebastian Ingrosso, X" keeps the duo.
    ``library_has(name)`` says whether the library has an artist by that name.
    """
    combined = str(album_artist or "").strip()
    segments = credit_segments(combined)
    if len(segments) < 2:
        return ""
    whole = combined.casefold()
    known = {}
    for entry in known_names or ():
        name = _name(entry)
        if name and name.casefold() != whole:
            known.setdefault(name.casefold(), name)

    def in_library(name):
        if library_has is None:
            return False
        try:
            return bool(library_has(name))
        except Exception:  # noqa: BLE001 - naming never fails over a lookup
            return False

    # the longest known name the credit starts with ("Axwell Λ Ingrosso")
    best = ""
    for name in known.values():
        if len(name) > len(best) and listed_primary_of(combined, name):
            best = combined[:len(name)]
    if best:
        return best

    is_list = bool(_HARD_SEPARATOR_RE.search(combined))
    if not is_list:
        # a known name standing on its own later in the credit, a duo
        # ("Axwell & Sebastian Ingrosso") as much as a single part
        is_list = any(_names_later(combined, name) for name in known.values())
    if not is_list and library_has is not None:
        is_list = sum(1 for seg in segments if in_library(seg)) >= 2
    if not is_list:
        return ""
    # a lead the library knows by its full name ("Earth, Wind & Fire; ...")
    # keeps it, otherwise the first part is the first listed artist
    if library_has is not None:
        for end in range(len(segments) - 1, 1, -1):
            prefix = _prefix_of(combined, segments[:end])
            if prefix and in_library(prefix):
                return prefix
    return segments[0]


_BOUNDARY_BEFORE_RE = re.compile(
    r"(?:[;,/&×]|\b(?:(?-i:x)|vs\.?|feat\.?|ft\.?|featuring))\s*$", re.IGNORECASE)


def _names_later(combined: str, name: str) -> bool:
    """does ``name`` stand as its own credit somewhere after the start."""
    folded, target = combined.casefold(), name.casefold()
    start = folded.find(target, 1)
    while start > 0:
        end = start + len(target)
        after = combined[end:]
        if (_BOUNDARY_BEFORE_RE.search(combined[:start])
                and (not after.strip() or _SEPARATOR_RE.match(after))):
            return True
        start = folded.find(target, start + 1)
    return False


def _prefix_of(combined: str, segments) -> str:
    """the part of ``combined`` that spells ``segments`` with their separators."""
    pos = 0
    for seg in segments:
        found = combined.find(seg, pos)
        if found < 0:
            return ""
        pos = found + len(seg)
    return combined[:pos]


def library_has_artist(name: str) -> bool:
    """does the library hold an artist by this name. False on any doubt."""
    name = str(name or "").strip()
    if not name:
        return False
    conn = None
    try:
        from database.music_database import get_database
        conn = get_database()._get_connection()
        # LOWER(name) has its own index; the lowering goes on the value side
        row = conn.execute(
            "SELECT 1 FROM lib2_artists WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,)).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 - naming never fails over a lookup
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 - cleanup only
                pass


def collab_primary(album_artist: str, album_artists=None, track_artists=None,
                   known_names=(), library_has=None) -> str:
    """the album artist under 'first listed', '' when it stays as it is.

    one rule for the folder ($albumartist, both template builders) and the
    album artist tag, so a release never files under one name and tags
    under another."""
    primary = first_listed_artist(album_artist, album_artists)
    if not primary and track_artists:
        primary = listed_primary_of(album_artist, track_artists[0])
    if not primary:
        names = list(known_names or ())
        names.extend(album_artists or ())
        names.extend(track_artists or ())
        primary = split_credit_primary(album_artist, names, library_has)
    return primary


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
