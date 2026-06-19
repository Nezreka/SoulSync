"""Re-identify hints (#889) — a single-use, user-designated answer to "which
release does this already-imported track belong to".

Flow: the user clicks *Re-identify* on a library track, searches a source, and
picks the exact release (single / EP / album) it should live under. We write a
**hint** here and stage the file for auto-import. The import flow then reads the
hint at the very TOP of matching — before any fuzzy tier — builds the match from
these exact IDs, and consumes the row. So the original ambiguity that mis-filed
the track (which release?) is gone: the user already answered it.

Two safety properties live in the hint, not the import code:

- ``replace_track_id`` — the library row to delete AFTER the re-import lands (so a
  re-identify *replaces* rather than *duplicates*). Cleanup is deferred to success
  so a failed import can never lose the file.
- ``exempt_dedup`` — always set: a re-identify is an explicit user action and must
  not be silently dropped by the quality dedup-skip (which would otherwise see the
  incoming file as a duplicate of the very row we're replacing).

This module is pure DB mechanics over an injected ``cursor`` (sqlite3-style,
``?`` params) — no connection management, no app state — so the create / find /
consume seam is unit-tested against an in-memory DB with no live metadata client.
The binding is keyed on the staged path, with ``content_hash`` as a rename-proof
fallback in case the staging watcher normalizes the filename on ingest.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

# Columns in INSERT/SELECT order — single source of truth so the dataclass, the
# write, and the read can't drift apart.
_FIELDS = (
    "staged_path",
    "content_hash",
    "source",
    "isrc",
    "track_id",
    "album_id",
    "artist_id",
    "track_title",
    "album_name",
    "artist_name",
    "album_type",
    "track_number",
    "disc_number",
    "replace_track_id",
    "exempt_dedup",
)


@dataclass
class RematchHint:
    """One user-designated re-identify answer. ``id``/``status`` are set by the DB."""
    staged_path: str
    source: str
    content_hash: Optional[str] = None
    isrc: Optional[str] = None
    track_id: Optional[str] = None
    album_id: Optional[str] = None
    artist_id: Optional[str] = None
    track_title: Optional[str] = None
    album_name: Optional[str] = None
    artist_name: Optional[str] = None
    album_type: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    replace_track_id: Optional[int] = None
    exempt_dedup: bool = True
    id: Optional[int] = None
    status: str = "pending"

    def _values(self) -> tuple:
        return (
            self.staged_path,
            self.content_hash,
            self.source,
            self.isrc,
            self.track_id,
            self.album_id,
            self.artist_id,
            self.track_title,
            self.album_name,
            self.artist_name,
            self.album_type,
            self.track_number,
            self.disc_number,
            self.replace_track_id,
            1 if self.exempt_dedup else 0,
        )


def _row_to_hint(row: Any) -> RematchHint:
    """Map a sqlite3.Row (or any mapping/sequence-by-name) to a RematchHint."""
    def g(key, default=None):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default
    return RematchHint(
        id=g("id"),
        staged_path=g("staged_path") or "",
        content_hash=g("content_hash"),
        source=g("source") or "",
        isrc=g("isrc"),
        track_id=g("track_id"),
        album_id=g("album_id"),
        artist_id=g("artist_id"),
        track_title=g("track_title"),
        album_name=g("album_name"),
        artist_name=g("artist_name"),
        album_type=g("album_type"),
        track_number=g("track_number"),
        disc_number=g("disc_number"),
        replace_track_id=g("replace_track_id"),
        exempt_dedup=bool(g("exempt_dedup", 1)),
        status=g("status") or "pending",
    )


def create_hint(cursor: Any, hint: RematchHint) -> int:
    """Insert a pending hint; return its new id. Caller owns commit."""
    placeholders = ", ".join("?" for _ in _FIELDS)
    cursor.execute(
        f"INSERT INTO rematch_hints ({', '.join(_FIELDS)}) VALUES ({placeholders})",
        hint._values(),
    )
    new_id = cursor.lastrowid
    hint.id = new_id
    return new_id


def find_hint_for_file(
    cursor: Any,
    staged_path: str,
    content_hash: Optional[str] = None,
) -> Optional[RematchHint]:
    """Return the newest PENDING hint for a staged file, or ``None``.

    Matched by exact ``staged_path`` first; if that misses and a ``content_hash``
    is given, fall back to it (covers a staging watcher that renamed the file on
    ingest). Only ``status='pending'`` rows are returned, so a consumed hint is
    never reused."""
    if staged_path:
        cursor.execute(
            "SELECT * FROM rematch_hints WHERE staged_path = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (staged_path,),
        )
        row = cursor.fetchone()
        if row is not None:
            return _row_to_hint(row)
        # Try by basename too — the watcher may move the file into a different dir.
        base = os.path.basename(staged_path)
        if base and base != staged_path:
            cursor.execute(
                "SELECT * FROM rematch_hints WHERE staged_path LIKE ? AND status = 'pending' "
                "ORDER BY id DESC LIMIT 1",
                ("%/" + base,),
            )
            row = cursor.fetchone()
            if row is not None:
                return _row_to_hint(row)

    if content_hash:
        cursor.execute(
            "SELECT * FROM rematch_hints WHERE content_hash = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (content_hash,),
        )
        row = cursor.fetchone()
        if row is not None:
            return _row_to_hint(row)

    return None


def consume_hint(cursor: Any, hint_id: int) -> None:
    """Mark a hint consumed (single-use). Caller owns commit."""
    cursor.execute(
        "UPDATE rematch_hints SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (hint_id,),
    )


def list_pending_hints(cursor: Any) -> list:
    """All pending hints (newest first) — for a 'pending re-identify' view and
    orphan recovery when a staged file never imports."""
    cursor.execute("SELECT * FROM rematch_hints WHERE status = 'pending' ORDER BY id DESC")
    return [_row_to_hint(r) for r in cursor.fetchall()]


def build_identification_from_hint(hint: RematchHint) -> dict:
    """Turn a hint into the ``identification`` dict the auto-import matcher expects,
    so a re-identify SKIPS the guessing tiers entirely and matches straight against
    the user-chosen release. Mirrors the shape `_identify_folder` returns (album_id
    / source / track_number drive the album fetch + file→track match)."""
    return {
        "album_id": hint.album_id or None,
        "album_name": hint.album_name or hint.track_title or "",
        "artist_name": hint.artist_name or "",
        "artist_id": hint.artist_id or "",
        "track_name": hint.track_title or "",
        "track_id": hint.track_id or "",
        "image_url": "",
        "release_date": "",
        "track_number": hint.track_number or 1,
        "total_tracks": 1,
        "source": hint.source,
        "method": "rematch_hint",
        "identification_confidence": 1.0,
        # is_single reflects the CHOSEN release, but force_album_match makes the
        # matcher FETCH that release (even for a lone staged file) instead of taking
        # the singles fast-path — so the re-imported track gets the real album
        # metadata: year, the correct in-album track number, and the album art.
        "is_single": (str(hint.album_type or "").lower() == "single"),
        "force_album_match": True,
        "album_type": hint.album_type,
    }


def _canonical(path: Optional[str]) -> str:
    """Canonical form of a path for same-file comparison (symlinks + case + sep)."""
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(os.path.normpath(path))


def delete_replaced_track(
    cursor: Any,
    replace_track_id: Any,
    *,
    unlink=os.remove,
    resolve_fn: Optional[Callable[[str], Optional[str]]] = None,
    new_paths: Optional[list] = None,
) -> Optional[str]:
    """Remove the OLD library row a re-identify replaces, and its file.

    Called only AFTER the re-import has landed the track at its new home, so the
    original is never lost on failure. Safe by construction:

    * **Same-home guard (CRITICAL):** if the re-import landed at the SAME file as the
      old one (``new_paths`` — the paths the import actually wrote), this is a no-op:
      we DON'T delete the row or the file, because that file IS the re-imported track.
      This is what stops "re-identify to the release it's already in" from deleting
      the file (the import reuses the same row, so deleting it would orphan the file).
    * the file is unlinked only if it still exists and **no other track row references
      it** (guards against yanking a file a different row legitimately points to).

    Returns the path it removed, or ``None`` if there was nothing to do. ``unlink`` is
    injectable for tests. ``resolve_fn`` maps the STORED DB path to the file's actual
    on-disk location (the stored path may be a Docker/media-server view this process
    can't read literally — without it we'd delete the row but orphan the file)."""
    if not replace_track_id:
        return None
    cursor.execute("SELECT file_path FROM tracks WHERE id = ?", (replace_track_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    old_path = (row["file_path"] if not isinstance(row, (tuple, list)) else row[0]) or ""
    if not old_path:
        cursor.execute("DELETE FROM tracks WHERE id = ?", (replace_track_id,))
        return None

    # Resolve the old stored path to its real on-disk location up front.
    real_path = old_path
    if resolve_fn is not None:
        try:
            real_path = resolve_fn(old_path) or old_path
        except Exception:
            real_path = old_path

    # Same-home guard: if the re-import wrote to this very file, do NOTHING — the row
    # is the re-imported track's row and the file is its file. Deleting either would
    # be data loss (the "picked the same release" bug).
    if new_paths:
        landed = {_canonical(p) for p in new_paths if p}
        if _canonical(real_path) in landed or _canonical(old_path) in landed:
            return None

    cursor.execute("DELETE FROM tracks WHERE id = ?", (replace_track_id,))
    # Only unlink if no surviving row still points at this file (rows store the
    # stored path, so compare against the stored path, not the resolved one).
    cursor.execute("SELECT 1 FROM tracks WHERE file_path = ? LIMIT 1", (old_path,))
    if cursor.fetchone() is not None:
        return None
    try:
        if os.path.exists(real_path):   # real_path resolved above
            unlink(real_path)
            return real_path
    except OSError:
        pass
    return None


def quick_file_signature(path: str, *, chunk: int = 65536) -> Optional[str]:
    """A cheap, rename-proof content fingerprint: size + first/last chunk, hashed.

    Audio files are large, so a full hash is wasteful when we only need to re-bind
    a hint to *this* file after a possible rename. Size + head + tail is plenty to
    distinguish staged files in practice. Returns ``None`` if the file can't be
    read (caller falls back to path-only binding)."""
    import hashlib

    try:
        size = os.path.getsize(path)
        h = hashlib.sha256()
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(chunk))
            if size > chunk:
                f.seek(max(0, size - chunk))
                h.update(f.read(chunk))
        return h.hexdigest()
    except OSError:
        return None


__all__ = [
    "RematchHint",
    "create_hint",
    "find_hint_for_file",
    "consume_hint",
    "list_pending_hints",
    "build_identification_from_hint",
    "delete_replaced_track",
    "quick_file_signature",
]
