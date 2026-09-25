"""Roadmap-4 external/old identifier history contract."""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.identity_history import (
    ensure_external_id_history_schema,
    list_external_id_history,
)


def _events(conn, entity_type, entity_id):
    return list_external_id_history(
        conn, entity_type=entity_type, entity_id=entity_id, limit=100
    )


def test_existing_install_gets_idempotent_schema_baseline():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE lib2_artists(
               id INTEGER PRIMARY KEY,
               spotify_id TEXT,
               musicbrainz_id TEXT,
               legacy_artist_id INTEGER,
               external_ids TEXT NOT NULL DEFAULT '{}')"""
    )
    conn.execute(
        """INSERT INTO lib2_artists(
               id, spotify_id, musicbrainz_id, legacy_artist_id, external_ids)
           VALUES(7, 'spotify-existing', 'mb-existing', 70,
                  '{"deezer":"dz-existing"}')"""
    )

    assert ensure_external_id_history_schema(conn.cursor()) == 4
    assert ensure_external_id_history_schema(conn.cursor()) == 0
    events = _events(conn, "artist", 7)
    assert {event.namespace for event in events} == {
        "spotify", "musicbrainz", "legacy_artist", "external_ids_json"
    }
    assert {event.change_source for event in events} == {"schema_backfill"}


def test_schema_backfills_current_provider_and_legacy_ids(imported_conn):
    artist = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    track = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='Hotline Bling'"
    ).fetchone()[0]

    assert {
        (event.namespace, event.new_value, event.change_source)
        for event in _events(imported_conn, "artist", artist)
    } >= {
        ("spotify", "sp1", "database_write"),
        ("legacy_artist", "1", "database_write"),
    }
    assert {
        (event.namespace, event.new_value)
        for event in _events(imported_conn, "release_group", album)
    } >= {("legacy_album", "10")}
    assert {
        (event.namespace, event.new_value)
        for event in _events(imported_conn, "track", track)
    } >= {("legacy_track", "101")}


def test_replace_remove_and_reassign_preserve_old_values(imported_conn):
    track = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='Hotline Bling'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_tracks SET spotify_id='spotify-old' WHERE id=?", (track,)
    )
    imported_conn.execute(
        "UPDATE lib2_tracks SET spotify_id='spotify-new' WHERE id=?", (track,)
    )
    imported_conn.execute(
        "UPDATE lib2_tracks SET spotify_id=NULL WHERE id=?", (track,)
    )
    imported_conn.execute(
        "UPDATE lib2_tracks SET spotify_id='spotify-restored' WHERE id=?", (track,)
    )

    spotify = [
        event for event in reversed(_events(imported_conn, "track", track))
        if event.namespace == "spotify"
    ]
    assert [event.event_type for event in spotify] == [
        "assigned", "replaced", "removed", "assigned"
    ]
    assert (spotify[1].old_value, spotify[1].new_value) == (
        "spotify-old", "spotify-new"
    )
    assert (spotify[2].old_value, spotify[2].new_value) == (
        "spotify-new", None
    )


def test_noop_update_and_schema_rerun_do_not_duplicate_history(imported_conn):
    artist = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    before = len(_events(imported_conn, "artist", artist))
    imported_conn.execute(
        "UPDATE lib2_artists SET spotify_id=spotify_id WHERE id=?", (artist,)
    )
    ensure_external_id_history_schema(imported_conn.cursor())
    ensure_external_id_history_schema(imported_conn.cursor())
    assert len(_events(imported_conn, "artist", artist)) == before


def test_long_tail_json_and_entity_delete_are_retained(imported_conn):
    album = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET external_ids=? WHERE id=?",
        ('{"deezer":"123"}', album),
    )
    imported_conn.execute("DELETE FROM lib2_albums WHERE id=?", (album,))

    events = _events(imported_conn, "release_group", album)
    json_events = [
        event for event in events if event.namespace == "external_ids_json"
    ]
    assert {event.event_type for event in json_events} == {"assigned", "removed"}
    assert {event.change_source for event in json_events} == {
        "database_write", "entity_delete"
    }
    assert {event.old_value or event.new_value for event in json_events} == {
        '{"deezer":"123"}'
    }


def test_history_rows_are_immutable(imported_conn):
    row_id = imported_conn.execute(
        "SELECT id FROM lib2_external_id_history ORDER BY id LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        imported_conn.execute(
            "UPDATE lib2_external_id_history SET namespace='changed' WHERE id=?",
            (row_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        imported_conn.execute(
            "DELETE FROM lib2_external_id_history WHERE id=?", (row_id,)
        )


@pytest.mark.parametrize("entity_type", ["", "album", "bogus"])
def test_history_reader_rejects_unknown_entity_types(imported_conn, entity_type):
    with pytest.raises(ValueError, match="unsupported identity entity_type"):
        list_external_id_history(
            imported_conn, entity_type=entity_type, entity_id=1
        )
