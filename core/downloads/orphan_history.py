"""Identify dead review-queue history rows whose file is gone (#934 follow-up).

The Unverified/Quarantine review queue is fed from ``library_history`` — an
append-only log that is never pruned. When a file is deleted, replaced, or
re-downloaded elsewhere, its old ``unverified`` row lingers forever and can
never be healed (there's no file left to confirm). Those are *orphans*.

This decides which rows are orphans, given a ``resolve(row) -> path | None``
the caller wires to the real filesystem lookup. Pure (no DB, no filesystem) so
the rules — including the safety gate — are unit-testable.

Safety gate: a filesystem check mass-false-positives when the library mount is
down (every file looks missing). So if EVERY reviewed file is unreachable and
there are enough rows to judge, we flag it ``suspicious`` and the caller refuses
to delete — better to clean nothing than to wipe a healthy log during an outage.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence


def find_orphan_history_ids(
    rows: Sequence[dict],
    resolve: Callable[[dict], Any],
    *,
    min_for_safety: int = 5,
    deletable: Callable[[dict], bool] | None = None,
) -> dict:
    """Return ``{'orphan_ids', 'checked', 'suspicious'}``.

    A row is an orphan when it has a non-empty ``file_path`` but ``resolve`` can
    find no file for it. ``suspicious`` is True when every checked row is
    missing and there are at least ``min_for_safety`` of them — the mount-down
    signature; the caller should refuse to delete in that case.

    ``deletable`` (optional) protects rows from removal WITHOUT weakening the
    safety gate: a protected row still counts toward ``checked`` and the
    all-missing signal (so e.g. a few unverified orphans can't be swept during a
    mount outage just because protected rows were filtered out first), but it
    never appears in ``orphan_ids``. Default: every missing row is deletable.
    """
    orphan_ids = []
    checked = 0
    missing = 0
    for row in rows:
        if not str((row.get('file_path') or '')).strip():
            continue
        checked += 1
        if resolve(row) is None:
            missing += 1
            if deletable is None or deletable(row):
                orphan_ids.append(row.get('id'))
    suspicious = checked >= min_for_safety and missing == checked
    return {'orphan_ids': orphan_ids, 'checked': checked, 'suspicious': suspicious}
