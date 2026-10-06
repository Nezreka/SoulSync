"""Reuse an album's existing on-disk folder for new downloads (#829).

When tracks are added to an album across multiple batches (a wishlist run, the
Album Completeness job, a missed track re-downloaded later), the destination
folder is normally rebuilt from API metadata each time. If ``$albumtype`` or
``$year`` come back blank/different on a later batch, the folder *name* changes
and the album splits across folders — forcing a Reorganize afterwards.

This resolves the folder the album *already* lives in so the new track joins its
existing files instead. Matching is deliberately conservative: the exact stored
Spotify album id first (definitive), then a STRICT (>= 0.85) name+artist match —
higher than the 0.7 used elsewhere, because a wrong match here misplaces a file.

Safety rails:
  * Only ever returns a folder UNDER the transfer dir (the managed download
    tree) — never a read-only library/NAS mount the resolver happens to find.
  * Only reuses when the album lives in EXACTLY ONE folder on disk. Multiple
    folders means disc subfolders (DatabaseTrack carries no disc number, so we
    can't safely pick the right one) — those defer to the template path.
  * Never reuses another release's folder. Two releases can share a title and
    differ only by musicbrainz's disambiguation (#1299); the album's release id
    (db row, else the files' own tags) has to agree before its folder is reused.
  * Never lets a smaller release join a bigger edition's folder: when the
    incoming release's own track total is known and the existing roster is
    larger, it is a different edition (a 12-track standard vs a 16-track
    deluxe) and the template path builds its own folder instead. A total of
    1 doesn't count as known — it is album.py's unknown-fallback — so lone
    thin-metadata tracks keep today's reuse.
  * Never merges different release kinds: a single/EP named after its lead
    track ("Ocean Avenue") must not join the album of the same name's folder
    — the file would collide with the album's title track and the single
    would never exist as its own release. When both the incoming type and
    the stored album row's type are known and differ, reuse is refused and
    the template builds the single/EP its own folder. Unknown on either side
    stays lenient (today's reuse) so thin metadata never splits a real album.
  * Any failure returns None — the caller falls back to the normal template.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Any, Optional

from core.library.path_resolver import resolve_library_file_path
from core.library.release_identity import read_release_identity
from utils.logging_config import get_logger

logger = get_logger("library.existing_album_folder")

# Strict — a wrong album match drops the file in the wrong folder.
_STRICT_ALBUM_CONFIDENCE = 0.85


def _normalize_album_name(name: Optional[str]) -> str:
    """Casefold + strip diacritics + collapse whitespace. Deliberately does NOT
    strip edition qualifiers (Deluxe/Remastered/"Plus"/…) — folder identity must
    treat those as *different* albums (#1001)."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def _same_album_name(a: Optional[str], b: Optional[str]) -> bool:
    """True only when two album names are the SAME album, not merely an
    edition-collapsed neighbour. Exact after normalization — a wrong merge here
    loses files/art, so we bias toward 'different' when unsure."""
    na, nb = _normalize_album_name(a), _normalize_album_name(b)
    return bool(na) and na == nb


def _is_under(child: str, parent: str) -> bool:
    """True if ``child`` is the same as or inside ``parent`` (normalized)."""
    try:
        child_n = os.path.normcase(os.path.normpath(os.path.abspath(child)))
        parent_n = os.path.normcase(os.path.normpath(os.path.abspath(parent)))
        return child_n == parent_n or child_n.startswith(parent_n + os.sep)
    except Exception:
        return False


# Canonical release kinds for the folder-reuse type check. Anything not on
# this map (including the empty string) is "unknown" and stays lenient.
_RELEASE_KIND_MAP = {
    "single": "single",
    "ep": "ep",
    "album": "album",
    "compilation": "compilation",
    "compile": "compilation",
    "compilations": "compilation",
}


def _normalize_release_kind(value: Any) -> str:
    """Map a raw album_type to a canonical release kind, or "" when unknown."""
    if value is None:
        return ""
    return _RELEASE_KIND_MAP.get(str(value).strip().lower(), "")


def _release_kinds_compatible(incoming: Any, stored: Any) -> bool:
    """True when folder reuse is safe across these release types.

    A known mismatch (single vs album) is never safe — the single would land
    in the album's folder and collide with its title track. Unknown on either
    side is lenient (True) so thin metadata keeps today's #829 reuse instead
    of splitting a real album across folders.
    """
    incoming_kind = _normalize_release_kind(incoming)
    stored_kind = _normalize_release_kind(stored)
    if not incoming_kind or not stored_kind:
        return True
    return incoming_kind == stored_kind


def _find_album(db: Any, spotify_album_id: Optional[str], album_name: Optional[str],
                album_artist: Optional[str], active_server: Optional[str],
                expected_track_count: Optional[int]):
    """Stored Spotify id first, then a strict name+artist match. None on no match."""
    if spotify_album_id:
        try:
            album = db.get_album_by_spotify_album_id(spotify_album_id)
            if album:
                return album
        except Exception as e:
            logger.debug("album-by-spotify-id lookup failed: %s", e)
    if album_name and album_artist:
        try:
            match, confidence = db.check_album_exists_with_editions(
                title=album_name, artist=album_artist,
                confidence_threshold=_STRICT_ALBUM_CONFIDENCE,
                expected_track_count=expected_track_count,
                server_source=active_server,
            )
            if match and confidence >= _STRICT_ALBUM_CONFIDENCE:
                # #1001: check_album_exists_with_editions is edition-AWARE — it
                # deliberately treats "Grassy Fields" and "Grassy Fields Plus"
                # (or Original vs Remastered) as the same album. That is right
                # for "does this album exist" questions, but WRONG for folder
                # identity: different editions are different releases and must
                # keep their own folders (that's what the user's $albumtype/$year
                # template encodes). So accept the name match only when it is the
                # SAME album name; otherwise fall through to the template.
                if _same_album_name(album_name, getattr(match, "title", None)):
                    return match
                logger.debug(
                    "[Existing Album Folder] rejecting edition-variant match "
                    "%r != %r — folder identity requires the same album name",
                    album_name, getattr(match, "title", None),
                )
        except Exception as e:
            logger.debug("strict album name+artist match failed: %s", e)
    return None


def _row_release_id(db: Any, album_id: Any) -> str:
    """The album row's musicbrainz release id, "" when unknown."""
    conn = None
    try:
        conn = db._get_connection()
        row = conn.execute(
            "SELECT musicbrainz_release_id FROM albums WHERE id = ?", (str(album_id),),
        ).fetchone()
        return str((row[0] if row else "") or "").strip()
    except Exception as e:
        logger.debug("release id lookup for album %s failed: %s", album_id, e)
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 - cleanup only
                pass


def _row_album_type(db: Any, album_id: Any) -> str:
    """The album row's stored release type, "" when unknown/unreadable.

    ``record_type`` is the populated column (the Deezer/Spotify/iTunes/
    JioSaavn enrichment workers backfill it: album / single / ep /
    compilation); ``album_type`` is legacy and unwritten by current code but
    still honored when a DB carries it. Each column is queried separately so
    a missing column never hides the other.
    """
    for col in ("record_type", "album_type"):
        conn = None
        try:
            conn = db._get_connection()
            row = conn.execute(
                f"SELECT {col} FROM albums WHERE id = ?", (str(album_id),),
            ).fetchone()
            # Strip before the truthiness check: a whitespace-only value is
            # unknown and must fall through to the next column, not return "".
            val = str(row[0]).strip() if row and row[0] else ""
            if val:
                return val.lower()
        except Exception as e:
            # Fail-open by design (pinned by
            # test_db_without_the_column_falls_back_to_file_tags): any
            # unreadable kind — missing column on legacy DBs, dead
            # connection, locked DB — keeps today's reuse instead of
            # splitting folders or breaking an import.
            logger.debug("%s lookup for album %s failed: %s", col, album_id, e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: S110 - cleanup only
                    pass
    return ""


def _same_release(db: Any, album: Any, sample_file: Optional[str],
                  release_id: str, disambiguation: str,
                  read_identity=read_release_identity) -> bool:
    """Is the found album the release being imported? ids decide when both sides
    have one. an import that knows nothing about its release keeps today's reuse."""
    known_id = _row_release_id(db, getattr(album, "id", None))
    file_comment = ""
    if sample_file:
        file_id, file_comment = read_identity(sample_file)
        known_id = known_id or file_id
    if release_id and known_id:
        return known_id.casefold() == release_id.casefold()
    if disambiguation:
        # an edition we can't match by id joins only a folder tagged as that edition
        return file_comment.casefold() == disambiguation.casefold()
    return True


def resolve_existing_album_folder(
    *,
    db: Any,
    transfer_dir: Optional[str],
    album_name: Optional[str] = None,
    album_artist: Optional[str] = None,
    spotify_album_id: Optional[str] = None,
    active_server: Optional[str] = None,
    expected_track_count: Optional[int] = None,
    config_manager: Any = None,
    resolver=resolve_library_file_path,
    musicbrainz_release_id: Optional[str] = None,
    disambiguation: Optional[str] = None,
    read_identity=read_release_identity,
    incoming_album_type: Optional[str] = None,
) -> Optional[str]:
    """Return the on-disk folder an existing album lives in (so a new track joins
    it) or None to fall back to the templated path. See module docstring.

    ``incoming_album_type`` is the release kind of the track being imported
    ("single" / "ep" / "album" / "compilation", "" when unknown). When it and
    the stored album row's type are both known and differ, the folder is NOT
    reused — a single must never be filed into its same-named album's folder.
    """
    if not transfer_dir or not os.path.isdir(transfer_dir):
        return None
    if not db:
        return None

    album = _find_album(db, spotify_album_id, album_name, album_artist,
                        active_server, expected_track_count)
    if not album:
        return None

    # Release-kind gate: a single/EP sharing the album's title is a different
    # release, not a later batch of the same one. Refuse before touching the
    # folder so the template builds it its own Singles/EPs folder instead.
    _stored_kind = _normalize_release_kind(
        _row_album_type(db, getattr(album, "id", None)))
    if not _release_kinds_compatible(incoming_album_type, _stored_kind):
        logger.info(
            "[Existing Album Folder] '%s' is a %s but the incoming release is "
            "a %s — different release kinds, not reusing its folder",
            getattr(album, "title", album_name),
            _stored_kind or "unknown",
            _normalize_release_kind(incoming_album_type) or "unknown",
        )
        return None

    try:
        tracks = db.get_tracks_by_album(album.id)
    except Exception as e:
        logger.debug("get_tracks_by_album(%s) failed: %s", getattr(album, 'id', '?'), e)
        return None

    folders = set()
    sample_file = None
    for t in tracks:
        file_path = getattr(t, 'file_path', None)
        if not file_path:
            continue
        try:
            resolved = resolver(file_path, transfer_folder=transfer_dir,
                                config_manager=config_manager)
        except Exception:
            resolved = None
        if not resolved:
            continue
        folder = os.path.dirname(resolved)
        if _is_under(folder, transfer_dir):
            folders.add(os.path.normpath(folder))
            sample_file = sample_file or resolved

    # Single folder under the transfer dir → reuse it. Zero (nothing on disk yet)
    # or many (disc subfolders) → let the template decide.
    if len(folders) == 1:
        reuse = next(iter(folders))
        # A smaller release never completes a bigger edition's folder. The
        # incoming release's own track total is authoritative: when the
        # existing album roster is LARGER, it is a different edition — a
        # 12-track standard must not join a 16-track deluxe folder. (The
        # edition-upgrade bonus in _calculate_album_confidence deliberately
        # matches standard→deluxe for "already own" checks; right for
        # existence, wrong for folder identity — and the DB row title is
        # edition-blind, so _same_album_name can't see it either.) The
        # reverse — fewer existing tracks than expected — is the normal
        # multi-batch completion #829 exists for, so it still reuses. A total
        # of 1 is not "known": album.py falls back to 1 when the source said
        # nothing (a Soulseek single, say), so 1 means "unknown" as often as
        # "single" — too ambiguous to judge an edition by, and refusing it
        # would split thin-metadata tracks off their album under template
        # drift. Totals of 2+ are never the fallback, so they judge.
        if (expected_track_count and expected_track_count > 1
                and len(tracks) > expected_track_count):
            logger.info(
                "[Existing Album Folder] '%s' holds %d tracks but the incoming "
                "release has %d — a different edition, not reusing it",
                reuse, len(tracks), expected_track_count)
            return None
        if not _same_release(db, album, sample_file,
                             (musicbrainz_release_id or "").strip(),
                             (disambiguation or "").strip(), read_identity):
            logger.info("[Existing Album Folder] '%s' holds a different release of '%s', "
                        "not reusing it", reuse, getattr(album, 'title', album_name))
            return None
        logger.info("[Existing Album Folder] Reusing '%s' for album '%s'",
                    reuse, getattr(album, 'title', album_name))
        return reuse
    return None


__all__ = ["resolve_existing_album_folder"]
