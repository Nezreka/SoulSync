"""Reconcile ``lib2_track_files.path`` rows that drifted away from disk.

pathdrift25-01 / LV2-017: ``resolve_lib2_path`` only compensates root and
mount differences.  A *filename* that changed underneath the catalogue — the
classic case being a disc-prefix template change, ``01-01 - Title.flac`` on
the row versus ``01 - Title.flac`` on disk — resolves to ``None``, and every
consumer then treats a physically present file as absent:

* ``rescan_files`` never reaches ``persist_tag_cache``, so the track's
  metadata scan stays ``pending`` forever, no matter how often the user runs
  "Refresh & Scan";
* the parent directory *does* exist, so ``missing_path_root_is_healthy`` calls
  the miss credible and two scans later the row is ``missing_confirmed`` —
  wanted/redownload logic can then go looking for a song that is right there.

LV2-017's correction contract promised a read-only backfill for installations
that drifted *before* the forward fix (H-13) landed.  This module is that
tool. Its repair job is deliberately review-only. During an exclusive legacy
upgrade, however, the same conservative matcher automatically applies its one
unambiguous, reverified proposal so existing users need no cleanup click.
Ambiguous matches are never guessed — pointing a catalogue row at the wrong
file is far worse than leaving it unresolved.

Nothing here mutates the filesystem.  The only write is
:func:`apply_path_drift_fix`, which re-verifies every precondition and then
updates the stored native index path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Imported as a MODULE, not as two names: `from ... import resolve_lib2_path`
# captures whatever the attribute happens to be at first import. This module is
# imported lazily from the scan, so in a test suite the first import can land
# inside a test that has the resolver patched — and that stand-in then stays
# bound here for the rest of the session, making unrelated tests fail depending
# on their order. Going through the module re-reads the attribute every call.
from core.library2 import paths as _paths
from core.quality.source_map import AUDIO_EXTENSIONS
from utils.logging_config import get_logger

logger = get_logger("library2.path_drift")

# Bounded by default: this walks directories for rows the resolver already
# failed on, and an operator wants a reviewable list, not a full-library dump.
DEFAULT_LIMIT = 500


@dataclass(frozen=True)
class DriftMatch:
    """One directory's answer for a single drifted filename."""

    status: str  # 'proposed' | 'ambiguous' | 'no_candidate'
    basename: Optional[str] = None
    reason: str = ""
    alternatives: Tuple[str, ...] = field(default_factory=tuple)


def split_track_numbering(stem: str) -> Tuple[Optional[int], str]:
    """Peel a leading ``disc-track``/``track`` prefix off a filename stem.

    Returns ``(track_number, remainder)``; the track number is the *last*
    number of the prefix, which is what both ``01-01 - Title`` (disc 1,
    track 1) and ``01 - Title`` mean.
    """
    rest = stem.lstrip()
    numbers: List[int] = []
    while True:
        digits = ""
        idx = 0
        while idx < len(rest) and rest[idx].isdigit() and len(digits) < 3:
            digits += rest[idx]
            idx += 1
        if not digits:
            break
        after = rest[idx:]
        stripped = after.lstrip()
        # A number only counts as numbering when a separator or space follows
        # it, so "1nonly - Song" keeps its whole stem.  "99 Problems" is
        # genuinely ambiguous and does get peeled; the track-number check in
        # the matcher is what keeps that from pairing two different songs.
        if stripped[:1] in ("-", ".", "_") or (after and after[:1] == " "):
            separator = stripped[:1] if stripped[:1] in ("-", ".", "_") else ""
            remainder = stripped[1:] if separator else stripped
            if not remainder.strip():
                break
            numbers.append(int(digits))
            rest = remainder.lstrip()
            continue
        break
    if not numbers:
        return None, stem.strip()
    return numbers[-1], rest.strip()


def _title_key(text: str) -> str:
    """Case-folded, punctuation-free, Unicode-preserving comparison key."""
    return "".join(ch for ch in str(text or "").casefold() if ch.isalnum())


def _describe(name: str) -> Tuple[str, Optional[int], str]:
    """``(extension, track_number, title_key)`` for one filename."""
    stem, ext = os.path.splitext(os.path.basename(str(name or "")))
    number, remainder = split_track_numbering(stem)
    return ext.lower(), number, _title_key(remainder)


def match_drifted_filename(
    stored_name: str,
    listing: Sequence[str],
    *,
    expected_size: Optional[int] = None,
    size_of: Optional[Callable[[str], Optional[int]]] = None,
) -> DriftMatch:
    """Pick the one file in ``listing`` that is plausibly ``stored_name``.

    Precision over recall by design.  A candidate qualifies only when it is
    the same audio container and its title key matches after the leading
    track numbering is peeled off; a *different* track number disqualifies it
    unless the file size confirms the pairing (same title on two real tracks
    is common, and stealing one for the other is exactly the failure this tool
    must not create).  Several qualifying candidates are reported as
    ``ambiguous`` unless the expected size singles one out.
    """
    want_ext, want_number, want_title = _describe(stored_name)
    if not want_title or want_ext not in AUDIO_EXTENSIONS:
        return DriftMatch(status="no_candidate", reason="unusable stored filename")

    qualified: List[str] = []
    size_matched: List[str] = []
    for name in listing:
        if os.path.basename(name) == os.path.basename(stored_name):
            continue  # the stored name itself is not a fix
        ext, number, title = _describe(name)
        if ext != want_ext or title != want_title:
            continue
        same_size = False
        if expected_size is not None and size_of is not None:
            try:
                same_size = int(expected_size) == int(size_of(name) or -1)
            except (TypeError, ValueError):
                same_size = False
        numbering_ok = want_number is None or number is None or number == want_number
        if not numbering_ok and not same_size:
            continue
        qualified.append(os.path.basename(name))
        if same_size:
            size_matched.append(os.path.basename(name))

    if not qualified:
        return DriftMatch(status="no_candidate", reason="no plausible file in directory")
    if len(qualified) == 1:
        return DriftMatch(status="proposed", basename=qualified[0], reason="title match")
    if len(size_matched) == 1:
        return DriftMatch(status="proposed", basename=size_matched[0],
                          reason="title and size match")
    return DriftMatch(status="ambiguous", reason="several plausible files",
                      alternatives=tuple(sorted(qualified)))


def has_drift_candidate(
    stored_path: Any,
    *,
    expected_size: Optional[int] = None,
    config_manager: Any = None,
) -> bool:
    """Whether an unresolvable path plausibly drifted instead of vanishing.

    Used by the scan to keep such a row out of the ``missing_confirmed``
    lifecycle: the directory is there and still holds a file that looks like
    this row's, so declaring the file gone (and letting wanted/redownload act
    on that) would be wrong while the reconcile proposal is still pending.
    Ambiguous directories count too — "probably one of these two" is still not
    "deleted".  Never raises.
    """
    try:
        stored = str(stored_path or "")
        if not stored:
            return False
        directory = _paths.resolve_lib2_directory(stored, config_manager)
        if not directory:
            return False
        match = match_drifted_filename(
            os.path.basename(stored.replace("\\", "/")),
            _listing(directory),
            expected_size=expected_size,
            size_of=lambda name: (
                os.path.getsize(os.path.join(directory, name))
                if os.path.exists(os.path.join(directory, name)) else None
            ),
        )
        return match.status in ("proposed", "ambiguous")
    except Exception as exc:  # noqa: BLE001
        logger.debug("drift candidate probe failed (%s): %s", stored_path, exc)
        return False


def _stored_sibling(stored_path: str, basename: str) -> str:
    """Rebuild a stored-namespace path with a different filename.

    The catalogue keeps paths in the *media server's* namespace; writing the
    locally resolved path back would silently re-namespace the row.  Only the
    filename drifted, so only the filename is replaced — separator included.
    """
    separator = "\\" if "\\" in stored_path and "/" not in stored_path else "/"
    head = stored_path.rsplit(separator, 1)[0] if separator in stored_path else ""
    return f"{head}{separator}{basename}" if head else basename


def _listing(directory: str) -> List[str]:
    try:
        with os.scandir(directory) as entries:
            return [entry.name for entry in entries if entry.is_file()]
    except OSError as exc:
        logger.debug("path drift listing failed (%s): %s", directory, exc)
        return []


def _claimed_by_other_row(conn, file_id: int, stored_candidate: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE path=? AND id<>? "
        "  AND COALESCE(file_state,'active')<>'deleted' LIMIT 1",
        (stored_candidate, int(file_id)),
    ).fetchone()
    return row is not None


def scan_path_drift(
    database,
    *,
    file_ids: Optional[List[int]] = None,
    limit: int = DEFAULT_LIMIT,
    config_manager: Any = None,
) -> Dict[str, Any]:
    """Read-only report of index rows whose stored path no longer resolves.

    Returns ``{"checked", "truncated", "proposals", "unresolved"}``.  Only
    unresolvable rows count towards ``checked``/``limit`` — a healthy library
    produces an empty report for the price of the resolve calls the scan does
    anyway.  Never writes, never touches the filesystem beyond reads.
    """
    conn = database._get_connection()
    try:
        if file_ids is not None:
            if not file_ids:
                return {"checked": 0, "truncated": False, "proposals": [],
                        "unresolved": []}
            marks = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"""SELECT id, track_id, path, size, file_state
                      FROM lib2_track_files
                     WHERE id IN ({marks}) AND path IS NOT NULL AND path <> ''
                       AND COALESCE(file_state,'active')<>'deleted'
                     ORDER BY id""",
                [int(value) for value in file_ids],
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, track_id, path, size, file_state
                     FROM lib2_track_files
                    WHERE path IS NOT NULL AND path <> ''
                      AND COALESCE(file_state,'active')<>'deleted'
                    ORDER BY id"""
            ).fetchall()

        proposals: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        listings: Dict[str, List[str]] = {}
        checked = 0
        truncated = False

        for row in rows:
            stored = str(row["path"])
            if _paths.resolve_lib2_path(stored, config_manager):
                continue
            if checked >= max(0, int(limit)):
                truncated = True
                break
            checked += 1

            entry: Dict[str, Any] = {
                "file_id": int(row["id"]),
                "track_id": row["track_id"],
                "stored_path": stored,
                "file_state": row["file_state"],
                "candidate_path": None,
                "proposed_stored_path": None,
                "status": "no_candidate",
                "reason": "",
                "alternatives": [],
            }

            directory = _paths.resolve_lib2_directory(stored, config_manager)
            if not directory:
                entry.update(status="directory_unresolved",
                             reason="containing directory not reachable")
                unresolved.append(entry)
                continue
            entry["directory"] = directory

            if directory not in listings:
                listings[directory] = _listing(directory)
            match = match_drifted_filename(
                os.path.basename(stored.replace("\\", "/")),
                listings[directory],
                expected_size=row["size"],
                size_of=lambda name, _dir=directory: (
                    os.path.getsize(os.path.join(_dir, name))
                    if os.path.exists(os.path.join(_dir, name)) else None
                ),
            )
            entry["reason"] = match.reason
            entry["alternatives"] = list(match.alternatives)
            if match.status != "proposed" or not match.basename:
                entry["status"] = match.status
                unresolved.append(entry)
                continue

            proposed_stored = _stored_sibling(stored, match.basename)
            if _claimed_by_other_row(conn, int(row["id"]), proposed_stored):
                entry.update(status="claimed",
                             reason="candidate already indexed by another file row")
                unresolved.append(entry)
                continue
            entry.update(status="proposed",
                         candidate_path=os.path.join(directory, match.basename),
                         proposed_stored_path=proposed_stored)
            proposals.append(entry)

        return {"checked": checked, "truncated": truncated,
                "proposals": proposals, "unresolved": unresolved}
    finally:
        conn.close()


def apply_path_drift_fix(
    database,
    file_id: int,
    candidate_path: str,
    *,
    config_manager: Any = None,
) -> Dict[str, Any]:
    """Repoint one index row at a proposed file after re-checking everything.

    The proposal may be minutes or days old, so every precondition is verified
    again here: the row must still exist, must still fail to resolve (someone
    else may have fixed it), the candidate must still be a real file, and no
    other row may have claimed it meanwhile.  No file is moved, created or
    deleted — this only repairs the index.
    """
    conn = database._get_connection()
    try:
        row = conn.execute(
            "SELECT id, track_id, path, file_state FROM lib2_track_files WHERE id=?",
            (int(file_id),),
        ).fetchone()
        if row is None:
            return {"success": False, "error": "File row no longer exists"}
        stored = str(row["path"] or "")
        if _paths.resolve_lib2_path(stored, config_manager):
            return {"success": False,
                    "error": "Stored path resolves again — rescan before applying"}

        target = str(candidate_path or "").strip()
        if not target or not os.path.isfile(target):
            return {"success": False, "error": "Proposed file no longer exists"}

        proposed_stored = _stored_sibling(stored, os.path.basename(target))
        if _claimed_by_other_row(conn, int(file_id), proposed_stored):
            return {"success": False,
                    "error": "Proposed file is already indexed by another row"}
        if not _paths.resolve_lib2_path(proposed_stored, config_manager):
            return {"success": False,
                    "error": "Proposed path does not resolve in this namespace"}

        conn.execute(
            """UPDATE lib2_track_files
                  SET path=?, missing_scan_count=0, missing_since=NULL,
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (proposed_stored, int(file_id)),
        )
        if str(row["file_state"] or "active") in ("missing_suspected",
                                                  "missing_confirmed"):
            from core.library2.track_files import set_file_state
            set_file_state(conn, int(file_id), "active")

        conn.commit()
        return {"success": True, "action": "path_repointed",
                "path": proposed_stored}
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        logger.error("path drift apply failed (%s): %s", file_id, exc, exc_info=True)
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def reconcile_imported_path_drift(
    database,
    *,
    batch_size: int = DEFAULT_LIMIT,
    config_manager: Any = None,
) -> Dict[str, int]:
    """Repair safe legacy filename drift before the migration gate opens.

    Only rows imported from Legacy participate. The existing conservative
    matcher and apply guards decide what is safe; anything ambiguous is merely
    observed so it becomes ``missing_suspected`` instead of false ownership or
    a redownload. Paging by id keeps large upgrades bounded without stalls.
    """
    stats = {"checked": 0, "repointed": 0, "unresolved": 0, "protected": 0}
    after = 0
    size = max(1, int(batch_size))
    while True:
        conn = database._get_connection()
        try:
            ids = [int(row[0]) for row in conn.execute(
                "SELECT id FROM lib2_track_files WHERE id>? "
                "AND legacy_import_run_id IS NOT NULL AND path IS NOT NULL "
                "AND TRIM(path)<>'' AND COALESCE(file_state,'active')<>'deleted' "
                "ORDER BY id LIMIT ?", (after, size),
            ).fetchall()]
        finally:
            conn.close()
        if not ids:
            break
        after = ids[-1]
        report = scan_path_drift(
            database, file_ids=ids, limit=len(ids), config_manager=config_manager,
        )
        stats["checked"] += int(report.get("checked") or 0)
        unresolved = {int(row["file_id"]) for row in report.get("unresolved", [])}
        for proposal in report.get("proposals", []):
            result = apply_path_drift_fix(
                database, int(proposal["file_id"]), proposal["candidate_path"],
                config_manager=config_manager,
            )
            if result.get("success"):
                stats["repointed"] += 1
            else:
                unresolved.add(int(proposal["file_id"]))
        if unresolved:
            from core.library2.scan import rescan_files
            observed = rescan_files(database, file_ids=sorted(unresolved))
            stats["protected"] += int(observed.get("missing") or 0)
            stats["unresolved"] += len(unresolved)
    return stats


__all__ = [
    "DEFAULT_LIMIT",
    "DriftMatch",
    "apply_path_drift_fix",
    "has_drift_candidate",
    "match_drifted_filename",
    "reconcile_imported_path_drift",
    "split_track_numbering",
    "scan_path_drift",
]
