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
    """The album row's musicbrainz release id, "" when unknown.

    The album comes from the catalogue lookups above, which are Library v2 on
    this branch, so the id is a ``lib2_albums`` id and the release id lives in
    its ``musicbrainz_id`` (the *release*, not the group)."""
    conn = None
    try:
        conn = db._get_connection()
        row = conn.execute(
            "SELECT musicbrainz_id FROM lib2_albums WHERE id = ?", (int(album_id),),
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
) -> Optional[str]:
    """Return the on-disk folder an existing album lives in (so a new track joins
    it) or None to fall back to the templated path. See module docstring."""
    if not transfer_dir or not os.path.isdir(transfer_dir):
        return None
    if not db:
        return None

    album = _find_album(db, spotify_album_id, album_name, album_artist,
                        active_server, expected_track_count)
    if not album:
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
