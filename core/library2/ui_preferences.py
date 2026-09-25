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

# Library track-table defaults captured from the intentionally configured
# reference layout. The fixed checkbox, monitor, number, title and actions
# columns are rendered independently and therefore do not appear here.
DEFAULT_PREFERENCES: Dict[str, Any] = {
    "track_table": {
        "columns": {
            # Title participates in ordering but intentionally cannot be hidden.
            "title": True,
            "disc": False,
            "artists": False,
            "duration": True,
            "bpm": False,
            "match": False,
            # Independent recognition by one or more connected media servers.
            # Keep this separate from the title so the table remains scannable.
            "media_server": False,
            "quality": True,
            # The assigned quality profile is a separate concern from the
            # physical file quality and therefore gets its own table column.
            "profile": False,
            "features": False,
            "metadata": True,
            # iss28-01: everyday Check summary backed by AcoustID plus the
            # human/force verification provenance.
            "acoustid": True,
            # UI-03: physical size of the selected primary file.
            "file_size": True,
            "file_path": False,
            # H1: row play button (reuses the Legacy player via the shell
            # bridge) — opt-in like file_path, not everyone wants it visible.
            "play": False,
        },
        "column_order": [
            "title",
            "disc",
            "artists",
            "duration",
            "bpm",
            "match",
            "profile",
            "file_size",
            "quality",
            "acoustid",
            "metadata",
            "features",
            "play",
            "file_path",
            "media_server",
        ],
        # UI-03/iss28-02: relative weights, normalized by the client to the
        # current table/browser width. Historical pixel values remain valid as
        # unnormalised weights and are therefore migrated without a DB write.
        "column_widths": {
            "number": 2.532,
            "title": 13.62,
            "disc": 5.93,
            "artists": 6.488,
            "duration": 5.154,
            "bpm": 3.357,
            "match": 28.79,
            "profile": 50.496,
            "file_size": 56.792,
            "quality": 12.283,
            "acoustid": 7.624,
            "metadata": 7.149,
            "features": 7.089,
            "media_server": 6.495,
        },
        "show_all_match_providers": False,
        "visible_match_providers": {
            "spotify": True,
            "musicbrainz": True,
            "deezer": True,
            "itunes": True,
            "audiodb": True,
            "discogs": True,
            "lastfm": True,
            "genius": True,
            "tidal": True,
            "qobuz": True,
            "amazon": True,
            "jiosaavn": True,
            "bandcamp": True,
        },
        "quality_show_format": True,
        "quality_show_resolution": True,
        "quality_show_bitrate": True,
    },
    # Round 5 (deep-dive D6): mirrors track_table's shape for the artist
    # overview's table view. All default off — the table view's whole point
    # is a denser row than the card grid, so extra columns stay opt-in.
    "artist_table": {
        "columns": {
            "quality_profile": False,
            "genres": False,
            "added": False,
            # I8: disk-space roll-up, opt-in like the rest of this table.
            "size": False,
        },
        "column_order": [
            "quality_profile",
            "genres",
            "added",
            "size",
        ],
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
