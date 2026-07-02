"""Schema/migration tests for `core/quality/schema.py`.

Uses a real `MusicDatabase` (sqlite-only, no Flask app) so the full
startup schema-init sequence runs exactly as it does in the app.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase
from core.quality.schema import ensure_quality_profiles_schema


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _columns(db):
    conn = db._get_connection()
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(quality_profiles)").fetchall()}
    finally:
        conn.close()


def test_fresh_install_has_no_quality_filter_enabled_column(db):
    assert "quality_filter_enabled" not in _columns(db)


def test_fresh_install_seeds_exactly_two_builtin_profiles(db):
    profiles = db.list_quality_profiles()
    names = {p["name"] for p in profiles}
    assert names == {"Balanced", "Upgrade until top quality"}


def test_ensure_schema_is_idempotent(db):
    conn = db._get_connection()
    try:
        # Calling it again (as every app boot does) must not raise or
        # duplicate the seeded rows.
        ensure_quality_profiles_schema(conn)
        conn.commit()
    finally:
        conn.close()
    assert len(db.list_quality_profiles()) == 2


def test_deleted_builtin_is_not_resurrected_by_reseeding(db):
    """`_seed_quality_profiles` used to `INSERT OR IGNORE` by hardcoded id —
    if a user deletes a starter profile, re-running schema init on the next
    boot must NOT bring it back. The guard is "table is empty", not
    "these specific ids are missing"."""
    db.delete_quality_profile(2)  # "Upgrade until top quality" (not default)

    conn = db._get_connection()
    try:
        ensure_quality_profiles_schema(conn)
        conn.commit()
    finally:
        conn.close()

    names = [p["name"] for p in db.list_quality_profiles()]
    assert "Upgrade until top quality" not in names
    assert len(names) == 1


def test_drops_leftover_quality_filter_enabled_column(db):
    """An intermediate version of this work added a `quality_filter_enabled`
    master toggle before it was noticed to be redundant with an empty
    ranked-target list / fallback_enabled=True. Anyone who ran that version
    has the column sitting in their real DB; schema init must clean it up on
    the next boot."""
    conn = db._get_connection()
    try:
        conn.execute(
            "ALTER TABLE quality_profiles ADD COLUMN quality_filter_enabled INTEGER NOT NULL DEFAULT 1"
        )
        conn.commit()
        assert "quality_filter_enabled" in _columns(db)

        ensure_quality_profiles_schema(conn)
        conn.commit()
    finally:
        conn.close()

    assert "quality_filter_enabled" not in _columns(db)
