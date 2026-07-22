"""B5: persisted Library-v2 UI display preferences."""

from __future__ import annotations

import sqlite3

from core.library2.schema import ensure_library_v2_schema
from core.library2.ui_preferences import (
    DEFAULT_PREFERENCES,
    get_ui_preferences,
    update_ui_preferences,
)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    return conn


def test_defaults_when_nothing_stored():
    conn = _conn()
    assert get_ui_preferences(conn) == DEFAULT_PREFERENCES


def test_update_merges_into_existing_section_without_clobbering_siblings():
    conn = _conn()
    update_ui_preferences(conn, {"track_table": {"columns": {"bpm": False}}})
    prefs = get_ui_preferences(conn)
    assert prefs["track_table"]["columns"]["bpm"] is False
    # Untouched sibling columns keep their default.
    assert prefs["track_table"]["columns"]["duration"] is True
    assert prefs["track_table"]["show_all_match_providers"] is False


def test_disc_column_defaults_off():
    conn = _conn()
    assert get_ui_preferences(conn)["track_table"]["columns"]["disc"] is False


def test_play_column_defaults_off():
    conn = _conn()
    assert get_ui_preferences(conn)["track_table"]["columns"]["play"] is False


def test_artist_table_columns_default_off_and_merge_independently():
    conn = _conn()
    prefs = get_ui_preferences(conn)
    assert prefs["artist_table"]["columns"] == {
        "quality_profile": False,
        "genres": False,
        "added": False,
        "size": False,
    }
    update_ui_preferences(conn, {"artist_table": {"columns": {"genres": True}}})
    prefs = get_ui_preferences(conn)
    assert prefs["artist_table"]["columns"]["genres"] is True
    assert prefs["artist_table"]["columns"]["added"] is False
    # Sibling section untouched.
    assert prefs["track_table"]["columns"]["bpm"] is True


def test_update_persists_across_connections(tmp_path):
    path = str(tmp_path / "lib2.db")

    def _file_conn():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        ensure_library_v2_schema(conn)
        conn.commit()
        return conn

    conn = _file_conn()
    update_ui_preferences(conn, {"track_table": {"show_all_match_providers": True}})
    conn.close()

    conn2 = _file_conn()
    assert get_ui_preferences(conn2)["track_table"]["show_all_match_providers"] is True
    conn2.close()


def test_second_update_merges_onto_first_not_onto_defaults():
    conn = _conn()
    update_ui_preferences(conn, {"track_table": {"columns": {"bpm": False}}})
    update_ui_preferences(conn, {"track_table": {"columns": {"file_path": True}}})
    prefs = get_ui_preferences(conn)
    assert prefs["track_table"]["columns"]["bpm"] is False
    assert prefs["track_table"]["columns"]["file_path"] is True


def test_unknown_stored_value_is_tolerated_not_fatal():
    conn = _conn()
    conn.execute(
        "INSERT INTO lib2_ui_preferences(id, preferences_json) VALUES (1, 'not json')"
    )
    conn.commit()
    # Falls back to defaults rather than raising on malformed JSON.
    assert get_ui_preferences(conn) == DEFAULT_PREFERENCES


def test_column_order_defaults_and_customization():
    conn = _conn()
    prefs = get_ui_preferences(conn)
    # Default order exists.
    assert prefs["track_table"]["column_order"] == [
        "play",
        "disc",
        "artists",
        "duration",
        "bpm",
        "match",
        "quality",
        "features",
        "metadata",
        "file_path",
    ]
    # Patch overrides it completely since lists are leaf values.
    custom_order = ["file_path", "play", "artists"]
    update_ui_preferences(conn, {"track_table": {"column_order": custom_order}})
    prefs = get_ui_preferences(conn)
    assert prefs["track_table"]["column_order"] == custom_order
    # Columns sibling mapping remains untouched.
    assert prefs["track_table"]["columns"]["bpm"] is True


def test_quality_and_match_provider_preferences():
    conn = _conn()
    prefs = get_ui_preferences(conn)
    # Default values exist.
    assert prefs["track_table"]["quality_show_format"] is True
    assert prefs["track_table"]["quality_show_resolution"] is True
    assert prefs["track_table"]["quality_show_bitrate"] is True
    assert prefs["track_table"]["visible_match_providers"]["spotify"] is True
    assert prefs["track_table"]["visible_match_providers"]["tidal"] is True

    # Patch values individually.
    patch = {
        "track_table": {
            "quality_show_resolution": False,
            "visible_match_providers": {"spotify": False},
        }
    }
    update_ui_preferences(conn, patch)
    prefs = get_ui_preferences(conn)
    assert prefs["track_table"]["quality_show_resolution"] is False
    assert prefs["track_table"]["quality_show_format"] is True
    assert prefs["track_table"]["visible_match_providers"]["spotify"] is False
    assert prefs["track_table"]["visible_match_providers"]["tidal"] is True
