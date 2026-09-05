"""Roadmap-4 merge/move/link history contract."""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.entity_history import (
    ensure_entity_history_schema,
    list_entity_history,
    record_entity_merge,
    record_entity_move,
)
from core.library2.track_file_move import move_track_file


class _NoWishlistDB:
    def remove_from_wishlist(self, *_args, **_kwargs):
        return False

    def add_to_wishlist(self, *_args, **_kwargs):
        return False


def _pair(conn):
    rows = conn.execute(
        """SELECT track.id, album.album_type
             FROM lib2_tracks track
             JOIN lib2_albums album ON album.id=track.album_id
            WHERE track.title='One Dance'
            ORDER BY album.album_type"""
    ).fetchall()
    album_track = next(row["id"] for row in rows if row["album_type"] == "album")
    single_track = next(row["id"] for row in rows if row["album_type"] == "single")
    return single_track, album_track


def test_existing_canonical_link_gets_idempotent_baseline():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE lib2_tracks(
               id INTEGER PRIMARY KEY, canonical_track_id INTEGER)"""
    )
    conn.execute("INSERT INTO lib2_tracks VALUES(1, NULL)")
    conn.execute("INSERT INTO lib2_tracks VALUES(2, 1)")

    assert ensure_entity_history_schema(conn.cursor()) == 1
    assert ensure_entity_history_schema(conn.cursor()) == 0
    event = list_entity_history(conn, entity_type="track", entity_id=2)[0]
    assert event.event_type == "canonical_linked"
    assert event.to_entity_id == 1
    assert event.change_source == "schema_backfill"


def test_canonical_link_relink_and_unlink_are_journaled(imported_conn):
    single, album = _pair(imported_conn)
    extra = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) "
        "SELECT album_id, 'Other Canonical' FROM lib2_tracks WHERE id=?",
        (album,),
    ).lastrowid
    imported_conn.execute(
        "UPDATE lib2_tracks SET canonical_track_id=? WHERE id=?", (extra, single)
    )
    imported_conn.execute(
        "UPDATE lib2_tracks SET canonical_track_id=NULL WHERE id=?", (single,)
    )

    own_events = [
        event for event in reversed(list_entity_history(
            imported_conn, entity_type="track", entity_id=single
        ))
        if event.subject_id == single
    ]
    assert [event.event_type for event in own_events] == [
        "canonical_linked", "canonical_relinked", "canonical_unlinked"
    ]
    assert own_events[1].from_entity_id == album
    assert own_events[1].to_entity_id == extra
    assert own_events[2].from_entity_id == extra
    assert own_events[2].to_entity_id is None


def test_track_file_move_records_ids_without_path(imported_conn):
    single, album = _pair(imported_conn)
    # The fixture's album side already has a file. Remove it so the public
    # move command can re-home the single file exactly as Manage Tracks does.
    imported_conn.execute(
        "DELETE FROM lib2_track_files WHERE track_id=?", (album,)
    )
    result = move_track_file(_NoWishlistDB(), imported_conn, single, album)

    events = list_entity_history(
        imported_conn, entity_type="track_file", entity_id=result["moved_file_id"]
    )
    move = next(event for event in events if event.event_type == "file_moved")
    assert (move.from_entity_id, move.to_entity_id) == (single, album)
    row = imported_conn.execute(
        "SELECT context_json FROM lib2_entity_history WHERE id=?", (move.id,)
    ).fetchone()
    assert row["context_json"] == "{}"
    assert "/m/" not in row["context_json"]


def test_release_track_recording_and_edition_moves_are_journaled(imported_conn):
    release_track = imported_conn.execute(
        "SELECT * FROM lib2_release_tracks ORDER BY id LIMIT 1"
    ).fetchone()
    new_recording = imported_conn.execute(
        "INSERT INTO lib2_recordings(title) VALUES('Replacement Recording')"
    ).lastrowid
    release_group = imported_conn.execute(
        "SELECT release_group_id FROM lib2_release_editions WHERE id=?",
        (release_track["release_edition_id"],),
    ).fetchone()[0]
    new_edition = imported_conn.execute(
        "INSERT INTO lib2_release_editions(release_group_id, is_default) VALUES(?, 0)",
        (release_group,),
    ).lastrowid
    imported_conn.execute(
        "UPDATE lib2_release_tracks SET recording_id=?, release_edition_id=? WHERE id=?",
        (new_recording, new_edition, release_track["id"]),
    )

    events = list_entity_history(
        imported_conn,
        entity_type="release_track",
        entity_id=release_track["id"],
    )
    by_type = {event.event_type: event for event in events}
    assert by_type["recording_moved"].from_entity_id == release_track["recording_id"]
    assert by_type["recording_moved"].to_entity_id == new_recording
    assert by_type["release_track_moved"].from_entity_id == (
        release_track["release_edition_id"]
    )
    assert by_type["release_track_moved"].to_entity_id == new_edition


def test_explicit_merge_and_move_helpers_are_transactional_and_redacted(imported_conn):
    merge_id = record_entity_merge(
        imported_conn,
        source_type="track",
        source_id=11,
        target_type="recording",
        target_id=22,
        context={"reason": "manual review", "path": "/secret/music.flac"},
    )
    move_id = record_entity_move(
        imported_conn,
        source_type="release_group",
        source_id=33,
        target_type="release_group",
        target_id=44,
        context={"command": "edition correction"},
    )
    rows = imported_conn.execute(
        "SELECT id, event_type, context_json FROM lib2_entity_history "
        "WHERE id IN (?,?) ORDER BY id",
        (merge_id, move_id),
    ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "entity_merged", "entity_moved"
    ]
    assert rows[0]["context_json"] == '{"reason":"manual review"}'
    assert "secret" not in rows[0]["context_json"]


def test_entity_history_is_immutable_and_rejects_self_merge(imported_conn):
    row_id = imported_conn.execute(
        "SELECT id FROM lib2_entity_history ORDER BY id LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        imported_conn.execute(
            "UPDATE lib2_entity_history SET change_source='changed' WHERE id=?",
            (row_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        imported_conn.execute("DELETE FROM lib2_entity_history WHERE id=?", (row_id,))
    with pytest.raises(ValueError, match="identical"):
        record_entity_merge(
            imported_conn,
            source_type="track",
            source_id=1,
            target_type="track",
            target_id=1,
        )
