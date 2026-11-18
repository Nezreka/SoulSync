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

from utils.logging_config import get_logger

logger = get_logger("imports.collab_artist")

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


def credit_segments(name: str) -> list:
    """each artist a credit string could name, in order: split at every
    separator except a slash inside a word ("AC/DC" stays one). For reading
    a credit, not for deciding it (split_credit_primary does that)."""
    from core.matching.artist_credit import tokens

    names, seps = tokens(name)
    out = names[:1]
    for (kind, raw), piece in zip(seps, names[1:], strict=True):
        if kind == "slash":
            out[-1] = out[-1] + raw + piece
        else:
            out.append(piece)
    return out


def split_credit_primary(album_artist: str, known_names=(), library_has=None) -> str:
    """the first artist of a credit the source gave as one string, '' when
    it can't tell the string is a list.

    "Creed, 3 Doors Down & Finger Eleven" came with no artist list at all,
    so the list rules above had nothing to split and the release got its
    own combined folder (#2028). The answer comes from the one credit kernel
    (core/matching/artist_credit.decide_credit): a ";" or " / " proves a
    list; a name this download vouches for (the track's artist, the watched
    artist) standing in the credit splits it around that name, so
    "Axwell & Sebastian Ingrosso, X" keeps the duo; an act the library
    holds whole keeps its name ("Above & Beyond" next to ghost "Above" and
    "Beyond" rows, #2131); two parts the library holds as artists make a
    list. ``library_has(name)`` says whether the library knows that act.
    """
    from core.matching.artist_credit import decide_credit, name_key

    combined = str(album_artist or "").strip()
    whole = name_key(combined)
    # a name equal to the whole credit says nothing about splitting it: an
    # album list naming "Creed, 3 Doors Down & Finger Eleven" as one entry is
    # the very case of #2028. A band stays whole because only a sure
    # separator cuts without evidence ("MAN WITH A MISSION", "Bear vs. Shark")
    known = [n for n in (_name(e) for e in known_names or ()) if n and name_key(n) != whole]

    def in_library(name):
        if library_has is None:
            return False
        try:
            return bool(library_has(name))
        except Exception:  # noqa: BLE001 - naming never fails over a lookup
            return False

    decision = decide_credit(combined, known=known,
                             is_act=in_library if library_has is not None else None,
                             is_artist=in_library if library_has is not None else None,
                             min_runs=2)
    return decision.primary if decision.split else ""


def _safe_lookup(lookup, name) -> bool:
    try:
        return bool(lookup(name))
    except Exception as exc:  # noqa: BLE001 - naming never fails over a lookup
        logger.debug("artist lookup failed for %r: %s", name, exc)
        return False


def library_has_artist(name: str) -> bool:
    """does the library know an act by this name: a canonical artist with a
    provider id, or one that is an album's artist. A credit an old split
    left behind ("Above" and "Beyond" out of "Above & Beyond") is neither,
    so it never votes a real name apart (#2131). False on any doubt."""
    name = str(name or "").strip()
    if not name:
        return False
    conn = None
    try:
        from database.music_database import get_database
        conn = get_database()._get_connection()
        # LOWER(name) has its own index; the lowering goes on the value side
        row = conn.execute(
            "SELECT 1 FROM lib2_artists a WHERE LOWER(a.name) = LOWER(?) "
            "AND (a.canonical_artist_id IS NULL OR a.canonical_artist_id = a.id) "
            "AND (COALESCE(a.spotify_id,'') <> '' OR COALESCE(a.musicbrainz_id,'') <> '' "
            "     OR COALESCE(a.external_ids,'{}') NOT IN ('','{}') "
            "     OR EXISTS (SELECT 1 FROM lib2_albums al WHERE al.primary_artist_id = a.id)) "
            "LIMIT 1", (name,)).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 - naming never fails over a lookup
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 - cleanup only
                pass


# the keep-whole names (#2150) live in the credit kernel; re-exported here
# for the callers and tests that import them from this module
from core.matching.artist_credit import (  # noqa: E402,F401
    KEPT_WHOLE_SETTING,
    KEPT_WHOLE_SETTINGS_KEY,
    kept_whole_artist_names,
    kept_whole_keys,
    parse_kept_whole_names,
    remember_kept_whole_names,
)


def library_has_identified_artist(name: str, conn=None) -> bool:
    """does the library hold an artist by exactly this name that a provider
    identified, or one the user said is one artist. False on any doubt."""
    name = str(name or "").strip()
    if not name:
        return False
    from core.matching.artist_credit import name_key
    if name_key(name) in kept_whole_keys():
        return True
    own = conn is None
    try:
        if own:
            from database.music_database import get_database
            conn = get_database()._get_connection()
        row = conn.execute(
            "SELECT 1 FROM lib2_artists WHERE LOWER(name) = LOWER(?) "
            "AND (COALESCE(spotify_id,'') <> '' OR COALESCE(musicbrainz_id,'') <> '' "
            "OR COALESCE(external_ids,'{}') NOT IN ('','{}')) LIMIT 1", (name,)).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 - naming never fails over a lookup
        return False
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001 - cleanup only
                logger.debug("library lookup close failed: %s", exc)


def _has_hard_separator(name) -> bool:
    """a ";" or " / " in the name: never part of an act's name"""
    from core.matching.artist_credit import tokens
    return any(kind == "hard" for kind, _raw in tokens(name)[1])


def collab_primary(album_artist: str, album_artists=None, track_artists=None,
                   known_names=(), library_has=None, library_knows=None) -> str:
    """the album artist under 'first listed', '' when it stays as it is.

    one rule for the folder ($albumartist, both template builders) and the
    album artist tag, so a release never files under one name and tags
    under another.

    ``library_knows(name)``: the library holds an identified artist by that
    exact name. such a name is one artist whatever its words are: a track
    credited to "Above" (or a library holding "Above" and "Beyond") never
    turns "Above & Beyond" into a list (#2131). only a ";" or " / " still
    reads as one, those are never part of an act's name."""
    primary = first_listed_artist(album_artist, album_artists)
    if primary:
        return primary
    if (library_knows is not None and not _has_hard_separator(album_artist)
            and _safe_lookup(library_knows, album_artist)):
        return ""
    if not primary and track_artists:
        primary = listed_primary_of(album_artist, track_artists[0])
    if not primary:
        names = list(known_names or ())
        names.extend(album_artists or ())
        names.extend(track_artists or ())
        has = library_has
        if library_knows is not None:
            # an identified artist is a library artist too, so a lead the
            # library knows ("Above & Beyond; Mat Zo") keeps its whole name
            def has(name):
                return ((library_has is not None and _safe_lookup(library_has, name))
                        or _safe_lookup(library_knows, name))
        primary = split_credit_primary(album_artist, names, has)
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
