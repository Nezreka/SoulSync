"""§A6/C3: the merged history feed must attribute events to the RIGHT
artist/album/track — acquisition_requests.scope/entity_id is not 1:1 with a
lib2 entity, so a naive join would silently cross-contaminate two artists'
history. These tests seed two independent artists and assert isolation.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.editions import backfill_editions
from core.library2.history_feed import scoped_history


def _second_artist(conn) -> dict:
    """A second, unrelated artist/album/track — Drake's own is seeded by the
    ``imported_conn`` fixture (legacy_db in conftest.py)."""
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, sort_name, monitored) VALUES('Rihanna','Rihanna',0)")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, monitored) "
        "VALUES(?, 'Anti', 'album', 0)", (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Work', 1, 0)", (album_id,))
    track_id = cur.lastrowid
    backfill_editions(cur)
    conn.commit()
    recording_id = cur.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?", (track_id,)
    ).fetchone()[0]
    return {
        "artist_id": artist_id, "album_id": album_id, "track_id": track_id,
        "recording_id": recording_id,
    }


def _drake_ids(conn) -> dict:
    row = conn.execute(
        """SELECT t.id AS track_id, t.album_id, al.primary_artist_id AS artist_id
             FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
            WHERE t.title='One Dance' AND al.album_type='album'"""
    ).fetchone()
    recording_id = conn.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?", (row["track_id"],)
    ).fetchone()[0]
    return {
        "artist_id": row["artist_id"], "album_id": row["album_id"],
        "track_id": row["track_id"], "recording_id": recording_id,
    }


def _acquisition_grab(conn, *, scope: str, entity_id: int, quality_profile_id: int = 1):
    from core.acquisition import ensure_acquisition_schema
    from core.acquisition.history import record_history_event
    from core.acquisition.requests import ADMIN_PROFILE_ID, create_request

    ensure_acquisition_schema(conn)
    request, _created = create_request(
        conn, profile_id=ADMIN_PROFILE_ID, scope=scope, entity_id=entity_id,
        quality_profile_id=quality_profile_id, trigger="manual",
        idempotency_key=f"test-{scope}-{entity_id}", search_options={},
    )
    record_history_event(
        conn, "grab_submitted", request_id=request.id, message="grabbed a candidate",
    )
    conn.commit()
    return request.id


def test_recording_scoped_grab_isolated_to_its_own_track(imported_conn):
    drake = _drake_ids(imported_conn)
    rihanna = _second_artist(imported_conn)
    _acquisition_grab(imported_conn, scope="recording", entity_id=drake["recording_id"])

    drake_history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    rihanna_history = scoped_history(imported_conn, scope="track", entity_id=rihanna["track_id"])

    assert any(e["event_type"] == "grab_submitted" for e in drake_history)
    assert not any(e["event_type"] == "grab_submitted" for e in rihanna_history)


def test_recording_grab_rolls_up_to_album_and_artist_scope(imported_conn):
    drake = _drake_ids(imported_conn)
    _acquisition_grab(imported_conn, scope="recording", entity_id=drake["recording_id"])

    album_history = scoped_history(imported_conn, scope="album", entity_id=drake["album_id"])
    artist_history = scoped_history(imported_conn, scope="artist", entity_id=drake["artist_id"])

    assert any(e["event_type"] == "grab_submitted" for e in album_history)
    assert any(e["event_type"] == "grab_submitted" for e in artist_history)


def test_artist_missing_scope_does_not_leak_into_a_different_artist(imported_conn):
    drake = _drake_ids(imported_conn)
    rihanna = _second_artist(imported_conn)
    _acquisition_grab(imported_conn, scope="artist_missing", entity_id=drake["artist_id"])

    drake_history = scoped_history(imported_conn, scope="artist", entity_id=drake["artist_id"])
    rihanna_history = scoped_history(imported_conn, scope="artist", entity_id=rihanna["artist_id"])

    assert any(e["event_type"] == "grab_submitted" for e in drake_history)
    assert not any(e["event_type"] == "grab_submitted" for e in rihanna_history)


def test_release_group_scope_does_not_leak_into_a_sibling_album(imported_conn):
    drake = _drake_ids(imported_conn)
    single_album_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='One Dance' AND album_type='single'"
    ).fetchone()[0]
    _acquisition_grab(imported_conn, scope="release_group", entity_id=drake["album_id"])

    views_history = scoped_history(imported_conn, scope="album", entity_id=drake["album_id"])
    single_history = scoped_history(imported_conn, scope="album", entity_id=single_album_id)

    assert any(e["event_type"] == "grab_submitted" for e in views_history)
    assert not any(e["event_type"] == "grab_submitted" for e in single_history)


def test_entity_history_canonical_link_surfaces_at_track_scope(imported_conn):
    # The importer's own dedup already canonical-links track 102 (single) to
    # track 100 (album) — the schema-ensure backfill journals it as a baseline
    # event (see test_entity_history.py). It should show up in that track's
    # merged history.
    drake = _drake_ids(imported_conn)
    single_track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='One Dance' AND album_id != ?",
        (drake["album_id"],),
    ).fetchone()[0]

    history = scoped_history(imported_conn, scope="track", entity_id=single_track_id)

    assert any(e["event_type"] == "canonical_linked" for e in history)


def test_file_delete_operation_surfaces_at_album_and_artist_not_sibling(imported_conn):
    from core.library2.file_delete import ensure_file_delete_schema

    drake = _drake_ids(imported_conn)
    rihanna = _second_artist(imported_conn)
    cur = imported_conn.cursor()
    ensure_file_delete_schema(cur)
    cur.execute(
        """INSERT INTO lib2_file_delete_operations(
               id, entity_type, entity_id, preview_token, status, file_count, total_size)
           VALUES('op1', 'release_group', ?, 'tok', 'completed', 2, 1000)""",
        (drake["album_id"],),
    )
    imported_conn.commit()

    album_history = scoped_history(imported_conn, scope="album", entity_id=drake["album_id"])
    artist_history = scoped_history(imported_conn, scope="artist", entity_id=drake["artist_id"])
    rihanna_history = scoped_history(imported_conn, scope="artist", entity_id=rihanna["artist_id"])

    assert any(e["event_type"] == "files_deleted" for e in album_history)
    assert any(e["event_type"] == "files_deleted" for e in artist_history)
    assert not any(e["event_type"] == "files_deleted" for e in rihanna_history)


def test_database_only_file_removal_has_distinct_history_label(imported_conn):
    from core.library2.file_delete import ensure_file_delete_schema

    drake = _drake_ids(imported_conn)
    ensure_file_delete_schema(imported_conn.cursor())
    imported_conn.execute(
        """INSERT INTO lib2_file_delete_operations(
               id, entity_type, entity_id, preview_token, status, file_count,
               total_size, mode, actor, completed_at)
           VALUES('op-db', 'albums', ?, 'tok', 'completed', 1, 123,
                  'database_only', 'user', CURRENT_TIMESTAMP)""",
        (drake["album_id"],),
    )
    imported_conn.commit()

    history = scoped_history(
        imported_conn, scope="album", entity_id=drake["album_id"]
    )

    event = next(e for e in history if e["event_type"] == "file_records_removed")
    assert event["title"] == "Removed from library database"
    assert event["source"] == "library"


def test_manual_skip_surfaces_at_track_scope_by_primary_file_path(imported_conn):
    drake = _drake_ids(imported_conn)
    path = imported_conn.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (drake["track_id"],),
    ).fetchone()[0]
    imported_conn.execute(
        """INSERT INTO lib2_manual_skips(file_path, skipped_checks, profile_id)
           VALUES(?, '["acoustid"]', 1)""",
        (path,),
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])

    assert any(e["event_type"] == "manual_skip" for e in history)


def test_track_download_surfaces_via_path_fallback_when_legacy_id_stale(imported_conn):
    """Real-DB finding: ``track_downloads.track_id`` is frequently never
    backfilled (NULL) even on a track whose ``lib2_tracks.legacy_track_id``
    IS set — a stale/never-populated legacy id, not a "no legacy id at all"
    case. ``source_info.py`` already falls through to the exact-path match
    when the legacy-id query returns nothing (see its docstring); this must
    do the same or the track-scoped Pipeline timeline silently drops every
    download whose ``track_downloads`` row predates/skipped that backfill."""
    drake = _drake_ids(imported_conn)
    path = imported_conn.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (drake["track_id"],),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_tracks SET legacy_track_id=999999 WHERE id=?", (drake["track_id"],)
    )
    imported_conn.execute(
        """CREATE TABLE IF NOT EXISTS track_downloads(
               id INTEGER PRIMARY KEY AUTOINCREMENT, track_id TEXT, file_path TEXT,
               source_service TEXT, track_title TEXT, track_album TEXT,
               status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    imported_conn.execute(
        "INSERT INTO track_downloads(track_id, file_path, source_service, track_title, status) "
        "VALUES(NULL, ?, 'soulseek', 'One Dance', 'completed')",
        (path,),
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])

    assert any(e["event_type"] == "downloaded" for e in history)


def test_unsupported_scope_raises(imported_conn):
    with pytest.raises(ValueError):
        scoped_history(imported_conn, scope="playlist", entity_id=1)


@pytest.mark.parametrize("limit", [0, 501])
def test_limit_out_of_range_raises(imported_conn, limit):
    with pytest.raises(ValueError):
        scoped_history(imported_conn, scope="artist", entity_id=1, limit=limit)
