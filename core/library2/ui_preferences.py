"""B5: persisted Library-v2 UI display preferences (columns, match-provider
visibility, feature badges).

lib2 is single-profile/admin-only (ADR-01), so this is one JSON blob row
rather than a per-user table — same shape ``app_config`` uses for the rest of
the app, just its own tiny table instead of the encrypted settings blob (that
blob's encryption/migration machinery has nothing to do with display
prefs). DB-backed rather than ``localStorage`` so the picks survive a
browser/profile switch.

The stored JSON only ever needs a shallow, one-level-deep merge: each
top-level section (``track_table``, …) is itself a flat dict of scalar
values, so partial updates (``{"track_table": {"bpm": False}}``) merge into
the existing section without clobbering its other keys.
"""

from __future__ import annotations

import json
from typing import Any, Dict

UI_PREFERENCES_DDL = """
CREATE TABLE IF NOT EXISTS lib2_ui_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    preferences_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Deep-dive B5/B6 defaults: everything visible except the opt-in file path
# column, matching the table's shape before this feature existed. ``disc``
# (round 5) defaults off too — most albums are single-disc, so it's noise
# until a user has a multi-disc release to look at.
DEFAULT_PREFERENCES: Dict[str, Any] = {
    "track_table": {
        "columns": {
            "disc": False,
            "artists": True,
            "duration": True,
            "bpm": True,
            "match": True,
            "quality": True,
            "features": True,
            "metadata": True,
            "file_path": False,
        },
        "show_all_match_providers": False,
    },
    # Round 5 (deep-dive D6): mirrors track_table's shape for the artist
    # overview's table view. All default off — the table view's whole point
    # is a denser row than the card grid, so extra columns stay opt-in.
    "artist_table": {
        "columns": {
            "quality_profile": False,
            "genres": False,
            "added": False,
        },
    },
}


def ensure_ui_preferences_schema(cursor) -> None:
    cursor.execute(UI_PREFERENCES_DDL)


def _merge_section(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_section(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_ui_preferences(conn) -> Dict[str, Any]:
    """Stored preferences overlaid on ``DEFAULT_PREFERENCES`` (missing/unknown
    keys fall back to the default so older stored blobs and new keys added
    later both resolve cleanly)."""
    row = conn.execute(
        "SELECT preferences_json FROM lib2_ui_preferences WHERE id=1"
    ).fetchone()
    stored: Dict[str, Any] = {}
    if row and row[0]:
        try:
            parsed = json.loads(row[0])
            if isinstance(parsed, dict):
                stored = parsed
        except (TypeError, ValueError):
            pass
    return _merge_section(DEFAULT_PREFERENCES, stored)


def update_ui_preferences(conn, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``patch`` into the stored preferences and persist the result."""
    merged = _merge_section(get_ui_preferences(conn), patch)
    conn.execute(
        """INSERT INTO lib2_ui_preferences(id, preferences_json, updated_at)
           VALUES (1, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
               preferences_json=excluded.preferences_json,
               updated_at=CURRENT_TIMESTAMP""",
        (json.dumps(merged),),
    )
    conn.commit()
    return merged


__all__ = [
    "DEFAULT_PREFERENCES",
    "ensure_ui_preferences_schema",
    "get_ui_preferences",
    "update_ui_preferences",
]
