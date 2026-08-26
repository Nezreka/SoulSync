"""Multi-file model for Library v2 track files (audit P1-07 / ADR-03).

``lib2_track_files`` has always allowed several files per track (FLAC + MP3 of
the same recording, or old + new file mid-upgrade), but until ADR-03 every
reading path picked ``ORDER BY id LIMIT 1`` — the OLDEST row, regardless of
quality or state. This module gives the multi-file schema an actual model:

- ``is_primary``: exactly one file per track is the one the app acts on
  (wishlist mirror, quality eval, retag, artwork, duplicate view, move).
- ``primary_manual``: a deliberate user selection survives later imports;
  without it the best active file is elected again whenever quality changes.
- ``file_role``: ``master`` / ``derivative`` / ``alternate`` distinguishes a
  retained acquisition from a generated output and an independent version.
- ``file_state``: ``active`` / ``missing_suspected`` / ``missing_confirmed`` /
  ``quarantined`` / ``deleted`` — the lifecycle from ADR-03/P2-02. Non-active
  files stay visible but never win primary selection over an active one.

Primary selection strategy (the ADR requires ONE documented strategy, not
implicit code):

1. ``active`` files before any other state;
2. lossless formats (flac/alac/wav/dsf, plus the aiff/aif/aifc/dff raw-
   extension spellings of wav/dsf — see ``_LOSSLESS_FORMATS`` below) before
   lossy;
3. higher bit depth, then higher sample rate, then higher bitrate;
4. the NEWER row wins ties (highest id) — the exact opposite of the old
   accidental "oldest row" behaviour, because a newer import of equal quality
   is the fresher, more trustworthy copy.

Maintenance is automatic: triggers keep the invariant on insert, re-home
(track_id update) and delete, so every write path — importer, scan, autolink,
manual import — participates without changes. ``backfill_primary_flags`` runs
from the schema-ensure step and repairs installs that predate the columns.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional

from core.quality.lossless import LOSSLESS_FORMATS as _CANONICAL_LOSSLESS
from core.quality.lossless import (
    LOSSLESS_CANDIDATE_EXTENSIONS as _CANONICAL_LOSSLESS_EXTS,
)
from utils.logging_config import get_logger

logger = get_logger("library2.track_files")

FILE_STATES = ("active", "missing_suspected", "missing_confirmed",
               "quarantined", "deleted")
FILE_ROLES = ("master", "derivative", "alternate")

# SQL IN-list of lossless formats, derived from the one canonical set the
# quality engine ranks (flac/alac/wav/dsf) so file election here and the UI
# badge (core.library2.status) can't disagree about what counts as lossless
# (review Teil B, reuse). The DB's ``format`` column sometimes holds the raw
# file extension verbatim (importer seed path) rather than the unified format
# name a probe produces — aiff/aif/aifc and dff are unambiguously lossless
# (they normalize to wav/dsf) but aren't spelled that way when unprobed, so
# their raw extensions are included too. Ambiguous containers (m4a/mp4 —
# ALAC-or-AAC) are deliberately excluded: without a codec probe they must not
# be assumed lossless, exactly like ``core.quality.lossless`` itself.
_LOSSLESS_TOKENS = set(_CANONICAL_LOSSLESS) | {
    ext.lstrip('.') for ext in _CANONICAL_LOSSLESS_EXTS
    if ext.lstrip('.') not in ('m4a', 'mp4')
}
_LOSSLESS_FORMATS = "(" + ",".join(
    f"'{fmt}'" for fmt in sorted(_LOSSLESS_TOKENS)) + ")"


def quality_order(alias: str = "") -> str:
    """The documented "best file" ordering WITHOUT the primary flag.

    Used to elect a primary (backfill, promotion after delete). Legacy rows
    with NULL ``file_state`` count as active.
    """
    p = f"{alias}." if alias else ""
    return (
        f"CASE WHEN COALESCE({p}file_state,'active')='active' THEN 0 ELSE 1 END, "
        f"CASE WHEN lower(COALESCE({p}format,'')) IN {_LOSSLESS_FORMATS} THEN 0 ELSE 1 END, "
        f"COALESCE({p}bit_depth,0) DESC, "
        f"COALESCE({p}sample_rate,0) DESC, "
        f"COALESCE({p}bitrate,0) DESC, "
        f"{p}id DESC"
    )


def primary_order(alias: str = "") -> str:
    """Read-path ordering: the primary flag first, quality as the defensive
    fallback for rows written before the flag existed (pre-backfill)."""
    p = f"{alias}." if alias else ""
    return f"{p}is_primary DESC, {quality_order(alias)}"


def primary_file_row(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """The track's primary file row (dict), or None when it has no file."""
    row = conn.execute(
        f"SELECT * FROM lib2_track_files WHERE track_id=? "
        f"AND COALESCE(file_state,'active')<>'deleted' "
        f"ORDER BY {primary_order()} LIMIT 1",
        (int(track_id),),
    ).fetchone()
    return dict(row) if row else None


#: Ids per `IN (...)` list. SQLite's default SQLITE_MAX_VARIABLE_NUMBER has been
#: 32,766 since 3.32, and a stock `python:3.x` Docker image ships that default --
#: so one bind variable per track hard-errored (`too many SQL variables`, HTTP
#: 500, permanent) above roughly 33,000 rows. The same 500-row chunking is
#: already applied in core/library2/scan.py.
_IN_CHUNK = 500


def primary_file_rows(conn, track_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """Load each track's ADR-03 primary file, chunked so it cannot exceed the
    SQLite bind-variable ceiling (perf-audit PERF-02)."""
    ids = sorted({int(track_id) for track_id in track_ids})
    if not ids:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start:start + _IN_CHUNK]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"""SELECT * FROM (
                    SELECT tf.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY tf.track_id
                               ORDER BY {primary_order('tf')}
                           ) AS lib2_primary_rank
                      FROM lib2_track_files tf
                     WHERE tf.track_id IN ({marks})
                       AND COALESCE(tf.file_state,'active')<>'deleted'
                ) ranked
                WHERE lib2_primary_rank=1""",
            chunk,
        ):
            out[int(row["track_id"])] = dict(row)
    return out


def writable_file_rows(conn, track_id: int) -> list:
    """Every file of a track a content write should reach, primary first.

    dd28-38: tags, ReplayGain and lyrics were written only into the primary
    file (``primary_order(...) LIMIT 1``) while the operation reported plain
    success — so a deliberate FLAC+MP3 pair silently diverged, and "Write Tags"
    was not true of the library.  ADR-03 allows several files per track; a
    metadata write is about the *recording*, so it belongs on all of them.

    Excludes states whose file must not be touched (``deleted``,
    ``missing_confirmed``, ``quarantined``).
    """
    return conn.execute(
        f"""SELECT id, path, file_state, is_primary, format
              FROM lib2_track_files
             WHERE track_id=? AND path IS NOT NULL AND path <> ''
               AND COALESCE(file_state,'active')
                   NOT IN ('missing_confirmed','deleted','quarantined')
             ORDER BY {primary_order()}""",
        (int(track_id),),
    ).fetchall()


def set_primary_file(conn, track_id: int, file_id: int) -> bool:
    """Explicitly make ``file_id`` the track's primary file.

    Returns False when the file doesn't belong to the track. Does not commit.
    """
    owner = conn.execute(
        """SELECT track_id FROM lib2_track_files
            WHERE id=? AND COALESCE(file_state,'active')='active'""",
        (int(file_id),)
    ).fetchone()
    if not owner or owner[0] != int(track_id):
        return False
    conn.execute(
        """UPDATE lib2_track_files
              SET is_primary=0, primary_manual=0,
                  updated_at=CURRENT_TIMESTAMP
            WHERE track_id=? AND id<>?""",
        (int(track_id), int(file_id)))
    conn.execute(
        """UPDATE lib2_track_files
              SET is_primary=1, primary_manual=1,
                  updated_at=CURRENT_TIMESTAMP
            WHERE id=?""", (int(file_id),))
    return True


def elect_primary_file(conn, track_id: int, *, force: bool = False) -> Optional[int]:
    """Elect and return one primary file for ``track_id``.

    A live manual selection wins unless ``force`` is requested.  Automatic
    election otherwise follows :func:`quality_order`.  Does not commit.
    """
    manual = "" if force else (
        "CASE WHEN COALESCE(primary_manual,0)=1 "
        "AND COALESCE(file_state,'active')='active' THEN 0 ELSE 1 END, "
    )
    row = conn.execute(
        f"""SELECT id FROM lib2_track_files
             WHERE track_id=?
             ORDER BY {manual}{quality_order()} LIMIT 1""",
        (int(track_id),),
    ).fetchone()
    conn.execute(
        "UPDATE lib2_track_files SET is_primary=0 WHERE track_id=?",
        (int(track_id),),
    )
    if not row:
        return None
    file_id = int(row[0])
    if force:
        conn.execute(
            "UPDATE lib2_track_files SET primary_manual=0 WHERE track_id=?",
            (int(track_id),),
        )
    conn.execute(
        "UPDATE lib2_track_files SET is_primary=1 WHERE id=?", (file_id,)
    )
    return file_id


def set_file_state(conn, file_id: int, state: str) -> bool:
    """Move a file through its ADR-03 lifecycle state.

    A primary file leaving ``active`` hands the flag to the best remaining
    active sibling (if any) so read paths keep acting on a live file.
    Returns False for unknown files/states. Does not commit.
    """
    if state not in FILE_STATES:
        return False
    row = conn.execute(
        "SELECT track_id, is_primary FROM lib2_track_files WHERE id=?",
        (int(file_id),)).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE lib2_track_files SET file_state=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?", (state, int(file_id)))
    track_id = row[0]
    if track_id is not None and row[1] and state != "active":
        conn.execute(
            "UPDATE lib2_track_files SET primary_manual=0 WHERE id=?",
            (int(file_id),),
        )
        elect_primary_file(conn, int(track_id))
    return True


def track_id_for_path(conn, file_path: Any) -> Optional[int]:
    """The catalogue track that owns a file path, or ``None``.

    Exact path first, then the basename — a media server and SoulSync often
    disagree about the prefix of the same file (server mount vs. container
    mount), and the filename is what survives that. The basename step is a
    guess by nature, so it only accepts an unambiguous one: two files sharing
    a filename in different albums return ``None`` rather than a coin flip.

    Live files win over deleted ones: a deleted row is history (ADR-03) and
    must not out-vote the file that actually sits at that path today.
    """
    file_path = str(file_path or "")
    if not file_path:
        return None
    row = conn.execute(
        f"SELECT track_id FROM lib2_track_files"
        f" WHERE path = ? AND track_id IS NOT NULL"
        f" AND COALESCE(file_state,'active') <> 'deleted'"
        f" ORDER BY {primary_order()} LIMIT 1", (file_path,)).fetchone()
    if row:
        return int(row[0])
    name = os.path.basename(file_path.replace('\\', '/'))
    if not name:
        return None
    escaped = name.replace('^', '^^').replace('%', '^%').replace('_', '^_')
    rows = conn.execute(
        "SELECT DISTINCT track_id FROM lib2_track_files"
        " WHERE track_id IS NOT NULL"
        "   AND COALESCE(file_state,'active') <> 'deleted'"
        "   AND (path LIKE ? ESCAPE '^' OR path LIKE ? ESCAPE '^')"
        " LIMIT 2", (f"%/{escaped}", f"%\\{escaped}")).fetchall()
    return int(rows[0][0]) if len(rows) == 1 else None


def repoint_file_path(conn, old_path: str, new_path: str,
                      track_id: Optional[int] = None) -> int:
    """Follow a file that moved on disk, matched by the path we stored for it.

    The by-path handle is the only one some callers have: the atomic album
    publish knows the pair it just moved, not a track. Returns the number of
    rows repointed, so a caller can tell "the catalogue did not know this file"
    from "done" — the two used to be indistinguishable, and the first is how a
    row ends up naming a path inside a staging tree that is about to be deleted.
    Does not commit.

    ``deleted`` rows are excluded and ``track_id`` narrows further when the
    caller has one. Without either, a path that was deleted (row retired to
    ``file_state='deleted'`` with the path kept as the audit record) and then
    re-imported had BOTH rows rewritten: the tombstone ended up naming a file it
    never pointed at, and the returned rowcount of 2 lied to the caller whose
    only reason for reading it is to distinguish those two cases.
    """
    old_path = str(old_path or "")
    new_path = str(new_path or "")
    if not old_path or not new_path or old_path == new_path:
        return 0
    params: tuple = (new_path, old_path)
    scope = ""
    if track_id is not None:
        scope = " AND track_id=?"
        params = (*params, int(track_id))
    cursor = conn.execute(
        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE path=? AND COALESCE(file_state,'active')<>'deleted'" + scope,
        params)
    return int(cursor.rowcount or 0)


def _absence_is_credible(stored: str, keep_path: str, config_manager: Any) -> bool:
    """Whether a stored path being absent really means the file is gone.

    Deliberately narrow.  ``resolve_lib2_path`` returns ``None`` both for "this
    path cannot be mapped into this container" and for "mapped fine, but the
    file is not there" — so absence alone proves nothing, and a media-server
    path on a correctly configured install would otherwise look exactly like a
    deleted file (the same trap as dd28-19).

    Two conditions must hold:

    1. the row lives in the SAME directory as the file we are keeping — which
       is the entire shape of the replacement this function exists for
       (``<same stem>.<new extension>``); and
    2. that directory genuinely resolves and exists, so its contents are
       observable right now.

    Anything else is left alone for the scanners, which have the whole-library
    context needed to judge it.
    """
    from core.library2.paths import resolve_lib2_directory, resolve_lib2_path

    stored_text = str(stored or "").strip()
    keep_text = str(keep_path or "").strip()
    if not stored_text or not keep_text:
        return False
    stored_dir = os.path.dirname(stored_text.replace("\\", "/"))
    keep_dir = os.path.dirname(keep_text.replace("\\", "/"))
    if not stored_dir or os.path.normcase(stored_dir) != os.path.normcase(keep_dir):
        return False
    try:
        resolved_dir = resolve_lib2_directory(stored_text, config_manager)
        if not resolved_dir:
            return False
        resolved = resolve_lib2_path(stored_text, config_manager=config_manager)
    except Exception:  # noqa: BLE001
        return False
    if resolved and os.path.exists(resolved):
        return False
    return not os.path.exists(
        os.path.join(resolved_dir, os.path.basename(stored_text))
    )


def retire_replaced_files(
    conn,
    track_id: int,
    *,
    keep_path: str,
    removed_paths: Iterable[str] = (),
    config_manager: Any = None,
) -> int:
    """Retire this track's file rows whose file the pipeline just replaced.

    dd28-08: a quality upgrade / enhance writes to ``<same stem>.<new ext>``
    and deletes the original.  Autolink keys on ``(track_id, path)`` and simply
    INSERTs the new row; the insert trigger only promotes it when the track has
    no active primary, so the *stale* row kept ``is_primary=1``/``active`` while
    pointing at a file that no longer exists.  Every lib2 read path — retag,
    ReplayGain, lyrics, the wishlist mirror, quality evaluation, queue status —
    then acted on a deleted file, and the track showed two files, until the
    user happened to run "Refresh & Scan".

    ``removed_paths`` are the paths the pipeline knows it deleted — those are
    retired unconditionally, because the caller just deleted them.  A row whose
    file is merely *absent* is retired only under the narrow conditions in
    :func:`_absence_is_credible` below; guide §5 forbids concluding a miss from
    an unhealthy root.  Returns the number of rows retired.  Does not commit.
    """

    def _norm(value: Any) -> str:
        text = str(value or "").strip()
        return os.path.normcase(os.path.normpath(text)) if text else ""

    keep = _norm(keep_path)
    removed = {_norm(p) for p in removed_paths if str(p or "").strip()}
    removed.discard(keep)

    rows = conn.execute(
        """SELECT id, path FROM lib2_track_files
            WHERE track_id=? AND COALESCE(file_state,'active')<>'deleted'""",
        (int(track_id),),
    ).fetchall()

    retired = 0
    for row in rows:
        stored = row["path"] if isinstance(row, dict) or hasattr(row, "keys") else row[1]
        file_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        normalized = _norm(stored)
        if not normalized or normalized == keep:
            continue
        if normalized not in removed and not _absence_is_credible(
            stored, keep_path, config_manager,
        ):
            continue
        if set_file_state(conn, file_id, "deleted"):
            retired += 1
            logger.info(
                "Library v2: retired replaced file row %s (%s) for track %s",
                file_id, stored, track_id,
            )
    return retired


def backfill_primary_flags(cursor) -> int:
    """Repair/seed the one-primary-per-track invariant. Idempotent.

    Promotes the best file of every track that has files but no primary and
    demotes accidental extra primaries (keeping the best). Orphaned rows
    (``track_id IS NULL``) never carry the flag. Returns rows changed.
    """
    changed = 0
    cursor.execute(
        "UPDATE lib2_track_files SET is_primary=0, primary_manual=0 "
        "WHERE track_id IS NULL AND (is_primary=1 OR primary_manual=1)")
    changed += cursor.rowcount
    # A manual choice is meaningful only while that file is usable.  Keep at
    # most one historical manual flag per track (the newest explicit row wins
    # if an older buggy version managed to create several).
    cursor.execute("""
        UPDATE lib2_track_files SET primary_manual=0
         WHERE primary_manual=1
           AND (COALESCE(file_state,'active')<>'active'
                OR id NOT IN (
                    SELECT MAX(m.id) FROM lib2_track_files m
                     WHERE m.primary_manual=1
                       AND COALESCE(m.file_state,'active')='active'
                     GROUP BY m.track_id))
    """)
    changed += cursor.rowcount
    # Older triggers could leave a deleted/missing row primary after a fresh
    # active import arrived.  Active files always own the operative flag.
    cursor.execute("""
        UPDATE lib2_track_files AS stale SET is_primary=0
         WHERE stale.is_primary=1
           AND COALESCE(stale.file_state,'active')<>'active'
           AND EXISTS (
               SELECT 1 FROM lib2_track_files active
                WHERE active.track_id=stale.track_id
                  AND COALESCE(active.file_state,'active')='active'
           )
    """)
    changed += cursor.rowcount
    # Keep only the desired primary: a valid manual selection first, otherwise
    # the documented quality ordering.  This also replaces stale but singular
    # automatic primaries when a better retained file arrived later.
    cursor.execute(f"""
        UPDATE lib2_track_files SET is_primary=0
         WHERE is_primary=1 AND track_id IS NOT NULL
           AND id <> (SELECT f.id FROM lib2_track_files f
                       WHERE f.track_id=lib2_track_files.track_id
                       ORDER BY CASE
                           WHEN COALESCE(f.primary_manual,0)=1
                            AND COALESCE(f.file_state,'active')='active'
                           THEN 0 ELSE 1 END,
                           {quality_order('f')} LIMIT 1)
    """)
    changed += cursor.rowcount
    # Elect a primary where none exists.
    cursor.execute(f"""
        UPDATE lib2_track_files SET is_primary=1
         WHERE id IN (
               SELECT (SELECT f.id FROM lib2_track_files f
                        WHERE f.track_id = t.track_id
                        ORDER BY CASE
                            WHEN COALESCE(f.primary_manual,0)=1
                             AND COALESCE(f.file_state,'active')='active'
                            THEN 0 ELSE 1 END,
                            {quality_order('f')} LIMIT 1)
                 FROM (SELECT DISTINCT track_id FROM lib2_track_files
                        WHERE track_id IS NOT NULL) t
                WHERE NOT EXISTS (
                      SELECT 1 FROM lib2_track_files p
                       WHERE p.track_id = t.track_id AND p.is_primary=1))
    """)
    changed += cursor.rowcount
    return changed


def backfill_file_roles(cursor) -> int:
    """Classify companions created before role/provenance columns existed."""
    changed = 0
    cursor.execute("""
        UPDATE lib2_track_files SET file_role='derivative'
         WHERE source='companion' AND COALESCE(file_role,'master')<>'derivative'
    """)
    changed += cursor.rowcount
    cursor.execute("""
        UPDATE lib2_track_files AS child
           SET derived_from_file_id=(
               SELECT parent.id FROM lib2_track_files parent
                WHERE parent.track_id=child.track_id
                  AND parent.id<>child.id AND parent.is_primary=1
                LIMIT 1)
         WHERE child.file_role='derivative'
           AND child.derived_from_file_id IS NULL
           AND EXISTS (
               SELECT 1 FROM lib2_track_files parent
                WHERE parent.track_id=child.track_id
                  AND parent.id<>child.id AND parent.is_primary=1)
    """)
    changed += cursor.rowcount
    return changed


def install_primary_triggers(cursor) -> None:
    """(Re)install the invariant-keeping triggers. Idempotent.

    Every mutation that can change the winner re-elects it.  A live
    ``primary_manual`` row wins; otherwise the documented quality order does.
    """
    for name in ("insert", "move", "delete", "quality_state"):
        cursor.execute(
            f"DROP TRIGGER IF EXISTS trg_lib2_track_files_primary_{name}")
    cursor.execute(f"""
        CREATE TRIGGER trg_lib2_track_files_primary_insert
        AFTER INSERT ON lib2_track_files
        FOR EACH ROW
        WHEN NEW.track_id IS NOT NULL
        BEGIN
            UPDATE lib2_track_files SET primary_manual=0
             WHERE track_id=NEW.track_id AND id<>NEW.id
               AND NEW.primary_manual=1;
            UPDATE lib2_track_files SET is_primary=0
             WHERE track_id=NEW.track_id;
            UPDATE lib2_track_files SET is_primary=1
             WHERE id=COALESCE(
                 (SELECT id FROM lib2_track_files
                   WHERE track_id=NEW.track_id AND primary_manual=1
                     AND COALESCE(file_state,'active')='active'
                   ORDER BY id DESC LIMIT 1),
                 (SELECT f.id FROM lib2_track_files f
                   WHERE f.track_id=NEW.track_id
                   ORDER BY {quality_order('f')} LIMIT 1));
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER trg_lib2_track_files_primary_move
        AFTER UPDATE OF track_id ON lib2_track_files
        FOR EACH ROW
        WHEN OLD.track_id IS NOT NEW.track_id
        BEGIN
            UPDATE lib2_track_files SET primary_manual=0 WHERE id=NEW.id;
            UPDATE lib2_track_files SET is_primary=0
             WHERE track_id=OLD.track_id OR track_id=NEW.track_id;
            UPDATE lib2_track_files SET is_primary=1
             WHERE id=COALESCE(
                 (SELECT id FROM lib2_track_files
                   WHERE track_id=OLD.track_id AND primary_manual=1
                     AND COALESCE(file_state,'active')='active'
                   ORDER BY id DESC LIMIT 1),
                 (SELECT f.id FROM lib2_track_files f
                   WHERE f.track_id=OLD.track_id
                   ORDER BY {quality_order('f')} LIMIT 1));
            UPDATE lib2_track_files SET is_primary=1
             WHERE id=COALESCE(
                 (SELECT id FROM lib2_track_files
                   WHERE track_id=NEW.track_id AND primary_manual=1
                     AND COALESCE(file_state,'active')='active'
                   ORDER BY id DESC LIMIT 1),
                 (SELECT f.id FROM lib2_track_files f
                   WHERE f.track_id=NEW.track_id
                   ORDER BY {quality_order('f')} LIMIT 1));
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER trg_lib2_track_files_primary_delete
        AFTER DELETE ON lib2_track_files
        FOR EACH ROW
        WHEN OLD.is_primary=1 AND OLD.track_id IS NOT NULL
        BEGIN
            UPDATE lib2_track_files SET is_primary=1
             WHERE id=COALESCE(
                 (SELECT id FROM lib2_track_files
                   WHERE track_id=OLD.track_id AND primary_manual=1
                     AND COALESCE(file_state,'active')='active'
                   ORDER BY id DESC LIMIT 1),
                 (SELECT f.id FROM lib2_track_files f
                   WHERE f.track_id=OLD.track_id
                   ORDER BY {quality_order('f')} LIMIT 1));
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER trg_lib2_track_files_primary_quality_state
        AFTER UPDATE OF file_state, format, bit_depth, sample_rate, bitrate
        ON lib2_track_files
        FOR EACH ROW
        WHEN NEW.track_id IS NOT NULL
        BEGIN
            UPDATE lib2_track_files SET primary_manual=0
             WHERE id=NEW.id AND COALESCE(NEW.file_state,'active')<>'active';
            UPDATE lib2_track_files SET is_primary=0
             WHERE track_id=NEW.track_id;
            UPDATE lib2_track_files SET is_primary=1
             WHERE id=COALESCE(
                 (SELECT id FROM lib2_track_files
                   WHERE track_id=NEW.track_id AND primary_manual=1
                     AND COALESCE(file_state,'active')='active'
                   ORDER BY id DESC LIMIT 1),
                 (SELECT f.id FROM lib2_track_files f
                   WHERE f.track_id=NEW.track_id
                   ORDER BY {quality_order('f')} LIMIT 1));
        END
    """)


__all__ = [
    "FILE_STATES",
    "FILE_ROLES",
    "backfill_file_roles",
    "backfill_primary_flags",
    "elect_primary_file",
    "install_primary_triggers",
    "primary_file_row",
    "primary_order",
    "quality_order",
    "repoint_file_path",
    "set_file_state",
    "set_primary_file",
]
