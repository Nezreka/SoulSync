"""Fold the duplicate catalog row a redownload import creates (#1810).

A library redownload runs through the normal import pipeline. When the
replacement lands at a DIFFERENT ``file_path`` than the original (even a
capitalization-only difference), the import's library-entry write finds no
row at the new path and INSERTs a fresh track row for it. The redownload
post-processing hook then repoints the ORIGINAL row at that same path —
leaving two catalog rows pointing at one file.

This module is the merge half of that hook: pure DB mechanics over an
injected ``cursor`` (sqlite3-style, ``?`` params), caller owns commit — the
same seam as ``core.imports.rematch_hints``. Comparisons happen in Python,
mirroring ``side_effects._fill_empty_columns``, because SQLite's type
coercion makes SQL-only empty checks unreliable across TEXT/INTEGER
columns. The keeper is the original row: its id (and therefore its
playlist references) survives.

**Which rows may fold.** Only rows the duplicate could actually be: same
``server_source`` as the keeper (the import writes ``'soulsync'`` rows; a
plex/navidrome row at the same path is a different library's entry for the
file and is never touched) and, when ``owner_profile_id`` exists, the same
owner as the keeper (a profile's copy is not the shared library's copy).
Matching is by exact ``file_path`` plus a normalized form (separator
style, ``./`` prefix, duplicate separators; case folding follows the OS —
``os.path.normcase`` — so on POSIX a case-different path is a different
file and is NOT folded, while on Windows it is the same file and is).
Paths under a different root do not match: a different string for a path
this process cannot prove is the same file. Duplicates fold oldest first
(by ``created_at``, then rowid — track ids are hashes with no temporal
meaning), so the freshest import's values win where values compete.

**What travels from a duplicate to the keeper:**

* file facts (duration in ms, bitrate, file_size — the units the import
  writer stores, where 0 means "unknown" and never overwrites);
* the retention-provenance pair and ``server_path`` (the file_path
  companion readers prefer) — taken unconditionally, NULL included,
  mirroring the import's existing-row refresh: a clean re-import must not
  leave the old file's claims or old path on the keeper;
* the recording identity pair (``musicbrainz_recording_id`` +
  ``recording_disambiguation``) — as a unit only: both fill together when
  the keeper has no recording id, or disambiguation fills alone when both
  rows name the SAME recording. The pair only belongs together
  (``side_effects._recording_identity``);
* every other column only fills a keeper value that is empty (NULL/'',
  plus 0 for the numeric columns the codebase treats as unknown —
  ``year``, ``track_number``, ``disc_number``), so user state and
  enrichment on the original row are never overwritten — except what
  never travels at all: ``owner_profile_id`` and ``server_source``
  (identity/scope — stamping a profile owner onto a shared row would hide
  it from shared-scope lookups, the #1504 lesson), ``quality_profile_id``
  (NULL means "follow the app-wide default", a setting, not a gap), the
  ``*_norm`` columns (derived from the keeper's own raw text by trigger +
  backfill — carrying the duplicate's norm for text the keeper doesn't
  have would strand an inconsistent norm no backfill repairs), and the
  per-provider ``*_match_status`` / ``*_last_attempted`` columns (they
  describe attempts against the duplicate's provider ids, not the
  keeper's);
* ``listening_history`` and the soulsync playlist-sync pairings
  (``sync_match_cache``, ``manual_library_track_matches``) repoint to the
  keeper; a ``sample_analysis`` row the duplicate already earned moves to
  the keeper (it describes the replacement file), replacing the keeper's
  stale one; the duplicate's ``track_credits`` move when the keeper's
  track artist IS the duplicate's (the credits belong to that name) and
  are otherwise removed with the row.

Optional-table failures are swallowed ONLY when the table is absent
(minimal schemas); any other error aborts the fold so the caller can roll
back to the pre-fold state rather than delete a row whose references
failed to move. If the original row no longer exists, the imported row IS
the library entry now, so nothing is folded or deleted.

This is a hook-side fold: it repairs the redownload this hook completes.
It is not a library-wide guarantee — duplicates already sitting at other
paths are untouched, artist/album rows a duplicate leaves empty are not
swept, and append-only logs that mention a duplicate id (e.g.
``personalized_track_history``) keep their historical entry.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# File facts: the duplicate's value wins when it is a real value — the
# import writer stores 0 for "unknown", so 0 (like NULL) never overwrites.
_FILE_FACT_COLUMNS = ("duration", "bitrate", "file_size")

# Taken from the duplicate unconditionally, NULL included: the retention
# provenance pair (a clean import clears a stale destructive-policy claim)
# and server_path, file_path's companion.
_UNCONDITIONAL_COLUMNS = ("acquired_quality_json", "retention_json", "server_path")

# Recording identity: filled as a unit, never independently.
_RECORDING_ID_COLUMN = "musicbrainz_recording_id"
_RECORDING_DISAMBIG_COLUMN = "recording_disambiguation"

# Numeric columns where 0 means "unknown" (the _fill_empty_columns
# convention), so a keeper 0 counts as empty and a duplicate 0 as no value.
_ZERO_EMPTY_COLUMNS = {"year", "track_number", "disc_number"}

# Never carried onto the keeper (see module docstring).
_NEVER_CARRY = {
    "id", "file_path", "created_at", "updated_at",
    "owner_profile_id", "server_source", "quality_profile_id",
}

# Fallback column set if the PRAGMA ever fails on a real connection.
_FALLBACK_COLUMNS = {
    "duration", "bitrate", "file_size", "title",
    "acquired_quality_json", "retention_json", "server_path",
    "musicbrainz_recording_id", "recording_disambiguation",
}


def _is_missing_table(exc: Exception) -> bool:
    return "no such table" in str(exc).lower()


def _row_dict(row: Any, columns: list) -> dict:
    try:
        return dict(row)
    except Exception:
        return dict(zip(columns, row, strict=False))


def _normalized(path: Any) -> str:
    """Path form for same-file comparison: separators unified, normcase'd."""
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(str(path).replace("\\", "/")))


def _empty(value: Any, column: str = "") -> bool:
    if value is None or value == "":
        return True
    return column in _ZERO_EMPTY_COLUMNS and value == 0


def _candidate_ids(cursor: Any, keeper: dict, new_file_path: str) -> list:
    """Ids of in-scope rows (other than the keeper) for the same file,
    oldest first. Scope: same server_source, same owner (when the column
    exists) — a different server's or profile's row at this path is a
    distinct catalog entry, never a duplicate of the keeper."""
    keeper_id = keeper.get("id")
    scope_cols = [c for c in ("server_source", "owner_profile_id") if c in keeper]

    def in_scope(row: dict) -> bool:
        return all(row.get(c) == keeper.get(c) for c in scope_cols)

    cursor.execute(
        "SELECT rowid AS _rowid, * FROM tracks WHERE file_path = ? AND id != ?",
        (new_file_path, keeper_id),
    )
    cols = [d[0] for d in cursor.description]
    found: dict = {}
    for r in cursor.fetchall():
        row = _row_dict(r, cols)
        if str(row.get("id")) != str(keeper_id) and in_scope(row):
            found[row.get("id")] = row

    # Normalized-form fallback, prefiltered to the same basename so the
    # scan touches only plausible rows: another writer may have stored
    # the same file under a differently-normalized string.
    target = _normalized(new_file_path)
    basename = target.replace("\\", "/").rsplit("/", 1)[-1]
    if target and basename:
        # Suffix match on the basename (either separator style stores it
        # last); the normalized-equality check below does the real deciding.
        like = "%" + basename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor.execute(
            "SELECT rowid AS _rowid, * FROM tracks "
            "WHERE id != ? AND file_path LIKE ? ESCAPE '\\'",
            (keeper_id, like),
        )
        cols = [d[0] for d in cursor.description]
        for r in cursor.fetchall():
            row = _row_dict(r, cols)
            if (
                str(row.get("id")) != str(keeper_id)
                and in_scope(row)
                and _normalized(row.get("file_path")) == target
            ):
                found[row.get("id")] = row

    rows = list(found.values())
    rows.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("_rowid") or 0))
    return rows


def _fold_one(cursor: Any, keeper: dict, donor: dict, columns: set) -> None:
    """Merge donor's values into the keeper dict + row (no commit)."""
    keeper_id = keeper.get("id")
    donor_id = donor.get("id")
    updates: dict = {}

    for col in _FILE_FACT_COLUMNS:
        if col in columns:
            value = donor.get(col)
            if value is not None and value != 0:
                updates[col] = value
    for col in _UNCONDITIONAL_COLUMNS:
        if col in columns:
            updates[col] = donor.get(col)

    if _RECORDING_ID_COLUMN in columns:
        keeper_mbid = keeper.get(_RECORDING_ID_COLUMN)
        donor_mbid = donor.get(_RECORDING_ID_COLUMN)
        if _empty(keeper_mbid) and not _empty(donor_mbid):
            updates[_RECORDING_ID_COLUMN] = donor_mbid
            if _RECORDING_DISAMBIG_COLUMN in columns:
                updates[_RECORDING_DISAMBIG_COLUMN] = donor.get(_RECORDING_DISAMBIG_COLUMN)
        elif (
            _RECORDING_DISAMBIG_COLUMN in columns
            and keeper_mbid
            and keeper_mbid == donor_mbid
            and _empty(keeper.get(_RECORDING_DISAMBIG_COLUMN))
            and not _empty(donor.get(_RECORDING_DISAMBIG_COLUMN))
        ):
            updates[_RECORDING_DISAMBIG_COLUMN] = donor.get(_RECORDING_DISAMBIG_COLUMN)

    skip = (
        set(_FILE_FACT_COLUMNS)
        | set(_UNCONDITIONAL_COLUMNS)
        | {_RECORDING_ID_COLUMN, _RECORDING_DISAMBIG_COLUMN}
        | _NEVER_CARRY
    )
    for col in sorted(columns):
        if col in skip or col.endswith("_norm"):
            continue
        if col.endswith("_match_status") or col.endswith("_last_attempted"):
            continue
        if _empty(keeper.get(col), col) and not _empty(donor.get(col), col):
            updates[col] = donor.get(col)

    if updates:
        sets = ", ".join(f'"{col}" = ?' for col in updates)
        cursor.execute(
            f"UPDATE tracks SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [*updates.values(), keeper_id],
        )
        keeper.update(updates)

    try:
        cursor.execute(
            "UPDATE listening_history SET db_track_id = ? WHERE db_track_id = ?",
            (keeper_id, donor_id),
        )
    except Exception as exc:
        if not _is_missing_table(exc):
            raise
        logger.debug("listening_history repoint skipped: %s", exc)
    for pair_table, pair_col in (
        ("sync_match_cache", "server_track_id"),
        ("manual_library_track_matches", "library_track_id"),
    ):
        try:
            cursor.execute(
                f"UPDATE {pair_table} SET {pair_col} = ? "
                f"WHERE {pair_col} = ? AND server_source = 'soulsync'",
                (keeper_id, donor_id),
            )
        except Exception as exc:
            if _is_missing_table(exc):
                logger.debug("%s repoint skipped: %s", pair_table, exc)
                continue
            # A unique conflict (keeper already has the equivalent
            # pairing) — drop the duplicate's copy instead of leaving it
            # dangling. Anything else aborts the fold.
            if not isinstance(exc, sqlite3.IntegrityError):
                raise
            logger.debug("%s repoint conflict, dropping duplicate copy: %s", pair_table, exc)
            cursor.execute(
                f"DELETE FROM {pair_table} "
                f"WHERE {pair_col} = ? AND server_source = 'soulsync'",
                (donor_id,),
            )
    try:
        # The duplicate's analysis describes the replacement file; the
        # keeper's describes the old one. Move it over (as TEXT ids —
        # sample_analysis.track_id is a TEXT primary key). An analysis
        # that lands only after this fold stays keyed by the dead id and
        # is simply never read; it is a recomputable cache.
        cursor.execute(
            "SELECT 1 FROM sample_analysis WHERE track_id = ?", (str(donor_id),))
        if cursor.fetchone() is not None:
            cursor.execute(
                "DELETE FROM sample_analysis WHERE track_id = ?",
                (str(keeper_id),))
            cursor.execute(
                "UPDATE sample_analysis SET track_id = ? WHERE track_id = ?",
                (str(keeper_id), str(donor_id)))
    except Exception as exc:
        if not _is_missing_table(exc):
            raise
        logger.debug("sample_analysis move skipped: %s", exc)
    try:
        # Credits belong to the credited name: when the keeper's track
        # artist IS the duplicate's (its own, or just filled from it),
        # the duplicate's credits are the keeper's credits — move them.
        # Otherwise they describe a different credit and go with the row
        # (the credits trigger covers full schemas either way).
        if keeper.get("track_artist") and keeper.get("track_artist") == donor.get("track_artist"):
            cursor.execute(
                "INSERT OR IGNORE INTO track_credits (track_id, name_norm) "
                "SELECT ?, name_norm FROM track_credits WHERE track_id = ?",
                (str(keeper_id), str(donor_id)),
            )
        cursor.execute("DELETE FROM track_credits WHERE track_id = ?", (str(donor_id),))
    except Exception as exc:
        if not _is_missing_table(exc):
            raise
        logger.debug("track_credits move skipped: %s", exc)


def fold_imported_duplicate_tracks(
    cursor: Any,
    original_track_id: Any,
    new_file_path: str,
) -> list:
    """Fold every in-scope duplicate row at ``new_file_path`` into the
    original row and delete the duplicates.

    Returns the ids of the duplicate rows that were folded and deleted
    (empty when there was nothing to merge). Raises on a real DB error so
    the caller can roll back instead of half-merging. Caller owns commit.
    """
    if original_track_id is None or original_track_id == "" or not new_file_path:
        return []

    cursor.execute("SELECT * FROM tracks WHERE id = ?", (original_track_id,))
    row = cursor.fetchone()
    if row is None:
        # Original is gone (deleted mid-download): the imported row is the
        # only catalog entry for the replacement — leave it alone.
        return []
    keeper_cols = [d[0] for d in cursor.description]
    keeper = _row_dict(row, keeper_cols)

    try:
        columns = {c[1] for c in cursor.execute("PRAGMA table_info(tracks)").fetchall()}
    except Exception:  # pragma is best-effort for non-SQLite test doubles
        columns = set()
    if not columns:
        columns = set(_FALLBACK_COLUMNS)

    folded = []
    for donor in _candidate_ids(cursor, keeper, new_file_path):
        _fold_one(cursor, keeper, donor, columns)
        cursor.execute("DELETE FROM tracks WHERE id = ?", (donor.get("id"),))
        if cursor.rowcount:
            folded.append(donor.get("id"))
    return folded


__all__ = ["fold_imported_duplicate_tracks"]
