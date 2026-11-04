"""``skip_deleted_quarantine`` — transfer-walking repair jobs must not re-scan
the ``<transfer>/deleted`` quarantine, or a file that a repair just moved there
reappears as an orphan/finding on the very next pass.

These two cases are what remains of the old ``_fix_duplicates`` regression file:
the ``duplicate_detector`` job itself was retired into the native Library-v2
delete engine (its "a failed physical delete must stay visible" contract now
lives in ``tests/library2/test_file_delete.py``), but the quarantine folder it
wrote into is still the shared destination every transfer-walking job skips.
"""

from __future__ import annotations

from core.repair_jobs.base import skip_deleted_quarantine


def test_skip_deleted_quarantine_prunes_top_level_deleted(tmp_path):
    transfer = str(tmp_path / "Transfer")
    dirs = ["Artist", "deleted", "Other"]
    skip_deleted_quarantine(transfer, dirs, transfer)   # root == transfer
    assert dirs == ["Artist", "Other"]                  # top-level /deleted pruned


def test_skip_deleted_quarantine_leaves_nested_deleted_folder(tmp_path):
    """Anchored to the TOP-LEVEL <transfer>/deleted — a legitimately-named
    'deleted' folder deeper in the library must NOT be pruned."""
    transfer = str(tmp_path / "Transfer")
    nested_root = str(tmp_path / "Transfer" / "Artist" / "Album")
    dirs = ["deleted", "CD1"]
    skip_deleted_quarantine(nested_root, dirs, transfer)
    assert dirs == ["deleted", "CD1"]
