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


def test_artist_history_includes_linked_alias_releases(imported_conn):
    drake = _drake_ids(imported_conn)
    alias = _second_artist(imported_conn)
    from core.library2.artist_aliases import link_artist_alias

    link_artist_alias(imported_conn, alias["artist_id"], drake["artist_id"])
    _acquisition_grab(
        imported_conn, scope="recording", entity_id=alias["recording_id"]
    )

    history = scoped_history(
        imported_conn, scope="artist", entity_id=drake["artist_id"]
    )

    assert any(e["event_type"] == "grab_submitted" for e in history)


def test_structured_pipeline_checks_surface_status_quality_and_reason(imported_conn):
    from core.acquisition.history import record_history_event

    drake = _drake_ids(imported_conn)
    request_id = _acquisition_grab(
        imported_conn, scope="recording", entity_id=drake["recording_id"]
    )
    record_history_event(
        imported_conn,
        "quality_checked",
        request_id=request_id,
        reason_code="quality_not_allowed",
        message="Below selected target",
        payload={
            "status": "failed",
            "actor": "system",
            "before_quality": "MP3 320kbps",
            "after_quality": "FLAC 16-bit/44.1kHz",
            "quality_profile_id": 2,
        },
    )
    record_history_event(
        imported_conn,
        "acoustic_id_checked",
        request_id=request_id,
        reason_code="user_override",
        message="AcoustID skipped by user approval",
        payload={"status": "skipped", "actor": "user"},
    )
    imported_conn.commit()

    history = scoped_history(
        imported_conn, scope="track", entity_id=drake["track_id"]
    )

    quality = next(e for e in history if e["event_type"] == "quality_checked")
    assert quality["title"] == "Quality checked"
    assert quality["category"] == "failed"
    assert quality["status"] == "failed"
    assert quality["payload"]["actor"] == "system"
    assert quality["detail"] == (
        "failed · Below selected target · "
        "MP3 320kbps → FLAC 16-bit/44.1kHz · profile 2"
    )
    acoustic = next(
        e for e in history if e["event_type"] == "acoustic_id_checked"
    )
    assert acoustic["title"] == "Acoustic ID checked"
    assert acoustic["category"] == "override"
    assert acoustic["status"] == "skipped"
    assert acoustic["detail"] == "skipped · AcoustID skipped by user approval"
    assert [
        event["event_type"]
        for event in history
        if event["event_type"] in {"quality_checked", "acoustic_id_checked"}
    ] == ["acoustic_id_checked", "quality_checked"]


def test_previous_file_replaced_surfaces_in_the_feed(imported_conn):
    """F-10 event vocabulary: an upgrade/replace step must show up in the
    track's history feed like every other correlated pipeline event."""
    from core.acquisition.history import record_history_event

    drake = _drake_ids(imported_conn)
    request_id = _acquisition_grab(
        imported_conn, scope="recording", entity_id=drake["recording_id"]
    )
    record_history_event(
        imported_conn,
        "previous_file_replaced",
        request_id=request_id,
        reason_code="quality_upgrade",
        payload={"reason": "quality_upgrade"},
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    replaced = next(e for e in history if e["event_type"] == "previous_file_replaced")
    assert replaced["title"] == "Previous file replaced"
    assert replaced["category"] == "imported"
    assert replaced["detail"] == "quality_upgrade"


def test_manual_grab_says_which_gate_it_overrode(imported_conn):
    """Reported: "Grabbed (manual)" showed only the source ("youtube") — no
    sign that this grab happened BECAUSE a human overrode gates the
    automatic matcher had rejected. That override reason is exactly the
    "wieso/weshalb" the history is supposed to answer.
    """
    from core.acquisition.history import record_history_event

    drake = _drake_ids(imported_conn)
    request_id = _acquisition_grab(
        imported_conn, scope="recording", entity_id=drake["recording_id"]
    )
    record_history_event(
        imported_conn,
        "manual_grab_correlated",
        request_id=request_id,
        reason_code="gate_rejections_overridden_by_manual_pick",
        payload={
            "source": "youtube",
            "accepted": False,
            "rejections": ["artist_mismatch", "release_mismatch"],
            "warnings": ["quality_fallback"],
        },
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    manual = next(e for e in history if e["event_type"] == "manual_grab_correlated")

    assert "youtube" in manual["detail"]
    assert "artist mismatch" in manual["detail"]
    assert "release mismatch" in manual["detail"]
    assert "quality fallback" in manual["detail"]


def test_the_legacy_download_row_does_not_duplicate_the_acquisition_completion(imported_conn):
    """Reported: "wieso gibt es Download und Download completed in der
    History?" — track_downloads (legacy) and acquisition_history (current)
    both journal the same real-world "the file arrived" moment
    independently. For a track the acquisition pipeline actually tracked,
    that must read as ONE event, not two.
    """
    from core.acquisition.history import record_history_event

    drake = _drake_ids(imported_conn)
    request_id = _acquisition_grab(
        imported_conn, scope="recording", entity_id=drake["recording_id"]
    )
    record_history_event(
        imported_conn, "grab_completed", request_id=request_id,
        payload={"source": "youtube", "has_output_path": True},
    )
    same_moment = imported_conn.execute(
        "SELECT created_at FROM acquisition_history WHERE request_id=? AND event_type='grab_completed'",
        (request_id,),
    ).fetchone()[0]
    imported_conn.execute(
        """CREATE TABLE IF NOT EXISTS track_downloads(
               id INTEGER PRIMARY KEY AUTOINCREMENT, track_id TEXT, file_path TEXT,
               source_service TEXT, track_title TEXT, track_album TEXT,
               status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    imported_conn.execute(
        "INSERT INTO track_downloads(track_id, source_service, track_title, status, created_at) "
        "VALUES(?, 'youtube', 'One Dance', 'completed', ?)",
        (str(drake["track_id"]), same_moment),
    )
    imported_conn.execute(
        "UPDATE lib2_tracks SET legacy_track_id=? WHERE id=?",
        (str(drake["track_id"]), drake["track_id"]),
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])

    assert any(e["event_type"] == "grab_completed" for e in history)
    assert not any(e["event_type"] == "downloaded" for e in history), (
        "the legacy row at the same timestamp is the same event, not a second one"
    )


def test_human_verification_decisions_surface_in_the_feed(imported_conn):
    """F-10's two human steps: an approve/reject that happens long after the
    download must land in the same correlated story, not a separate silo."""
    from core.acquisition.history import record_history_event

    drake = _drake_ids(imported_conn)
    request_id = _acquisition_grab(
        imported_conn, scope="recording", entity_id=drake["recording_id"]
    )
    record_history_event(
        imported_conn, "human_verified", request_id=request_id,
        payload={"actor": "profile:1", "library_history_id": 7},
    )
    record_history_event(
        imported_conn, "rejected", request_id=request_id,
        reason_code="human_rejected", payload={"actor": "profile:1"},
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    verified = next(e for e in history if e["event_type"] == "human_verified")
    rejected = next(e for e in history if e["event_type"] == "rejected")
    assert (verified["title"], verified["category"]) == ("Verified by you", "imported")
    assert (rejected["title"], rejected["category"]) == ("Rejected by you", "failed")


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


def test_a_maintenance_delete_shows_up_on_the_albums_timeline(imported_conn):
    """Lidarr parity, as reported: "da wird dann auch in der history angezeigt
    wenn ein song gelöscht wurde und neu heruntergeladen". The download half
    was already there; the delete half only existed for the dialog, because a
    job's delete was a bare os.remove with no operation row to render.
    """
    from core.library2.file_delete import delete_files_journaled

    drake = _drake_ids(imported_conn)

    class _KeepOpen:
        """The journal closes what it opens; this test's connection must live."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    class _DB:
        def _get_connection(self):
            return _KeepOpen(imported_conn)

    # A path that does not exist: the delete fails, but the operation is still
    # journalled — which is the point being tested here.
    delete_files_journaled(
        _DB(), targets=["/music/Drake/Views/01 - Gone.flac"],
        entity_type="albums", entity_id=drake["album_id"],
        actor="repair:corrupt_audio", require_library_root=False,
    )

    album_history = scoped_history(imported_conn, scope="album", entity_id=drake["album_id"])
    deleted = [e for e in album_history if e["event_type"] == "files_deleted"]

    assert deleted, "a job's delete has to appear on the album's timeline"
    assert "repair:corrupt_audio" in deleted[0]["detail"], (
        "the timeline must say WHO deleted it — a user or a job"
    )


def test_an_acoustid_scan_event_says_the_verdict_not_just_the_field_names(imported_conn):
    """Reported: "wenn in der history steht 'acoustid updated' dann soll da
    auch stehen ob unverified, verified etc mit begründung". The raw event
    only ever recorded WHICH columns a job touched, never what they became —
    so "Acoustic ID status updated" said nothing an unverified/verified
    reader could act on. The file's current row already carries the verdict
    the Check column shows; the history event should say the same thing.
    """
    from core.library2.maintenance_sync import ensure_maintenance_event_schema

    drake = _drake_ids(imported_conn)
    file_id = imported_conn.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (drake["track_id"],),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_track_files SET acoustid_status='fail', verification_status='unverified', "
        "pipeline_result_json=? WHERE id=?",
        ('{"acoustid_message": "Audio mismatch: file identified as \'Wrong Song\' by \'Someone Else\'"}',
         file_id),
    )
    ensure_maintenance_event_schema(imported_conn.cursor())
    imported_conn.execute(
        "INSERT INTO lib2_maintenance_events(job_id, action, lib2_track_id, lib2_file_id, "
        "changed_fields_json) VALUES('acoustid_scanner', 'verification_status_updated', ?, ?, "
        "'[\"file_snapshot\"]')",
        (drake["track_id"], file_id),
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    scan_event = next(e for e in history if e["event_type"] == "verification_status_updated")

    # Status carries the verdict word (matches the Check column's own badge
    # text exactly, so the UI can render one identical badge in both places);
    # Detail carries the reasoning — not the same field doing double duty.
    assert scan_event["status"] == "Mismatch"
    assert "Wrong Song" in scan_event["detail"]
    assert scan_event["track_id"] == drake["track_id"]
    assert scan_event["track_title"] == "One Dance"
    assert scan_event["album_title"] == "Views"
    assert scan_event["changed_fields"] == ["file_snapshot"]
    # Older journals have no verdict snapshot. Never present today's file
    # state as if it had been recorded at the time of the event.
    assert scan_event["status_basis"] == "current_file"
    assert scan_event["job_id"] == "acoustid_scanner"


def test_acoustid_scan_event_says_unverified_not_skipped_when_the_check_ran(imported_conn):
    """Reported: "Skipped" reads as "nothing happened" — wrong for a file
    AcoustID actually fingerprinted and simply could not confirm. Force/retry
    bypasses (never ran at all) keep "Skipped"; a genuine no-match gets
    "Unverified", same split as the Check column badge."""
    from core.library2.maintenance_sync import ensure_maintenance_event_schema

    drake = _drake_ids(imported_conn)
    file_id = imported_conn.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (drake["track_id"],),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_track_files SET acoustid_status='skip', verification_status=NULL "
        "WHERE id=?", (file_id,),
    )
    ensure_maintenance_event_schema(imported_conn.cursor())
    imported_conn.execute(
        "INSERT INTO lib2_maintenance_events(job_id, action, lib2_track_id, lib2_file_id, "
        "changed_fields_json) VALUES('acoustid_scanner', 'verification_status_updated', ?, ?, "
        "'[\"file_snapshot\"]')",
        (drake["track_id"], file_id),
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    scan_event = next(e for e in history if e["event_type"] == "verification_status_updated")

    assert scan_event["status"] == "Unverified"
    assert "no confident match" in scan_event["detail"]


def test_a_tracks_own_history_says_its_file_was_deleted(imported_conn):
    """Reported: "ich hab gelöscht und wenn ich auf den Stift klicke, dann auf
    Infos gehe, sehe ich nicht dass dieser Track von mir gelöscht wurde."

    The delete journal is keyed by artist/album, so the artist and album
    timelines showed it and the track's own — the one behind the pencil — did
    not. The journal's ITEMS name the file ids, and a file belongs to a track.
    """
    from core.library2.file_delete import delete_files_journaled

    track_id, album_id = imported_conn.execute(
        "SELECT t.id, t.album_id FROM lib2_tracks t "
        " JOIN lib2_track_files f ON f.track_id=t.id LIMIT 1"
    ).fetchone()
    path = imported_conn.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=?", (track_id,)
    ).fetchone()[0]

    class _KeepOpen:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    class _DB:
        def _get_connection(self):
            return _KeepOpen(imported_conn)

    delete_files_journaled(
        _DB(), targets=[{"path": path, "stored_path": path}],
        entity_type="albums", entity_id=album_id,
        actor="user", require_library_root=False,
    )

    history = scoped_history(imported_conn, scope="track", entity_id=track_id)
    deleted = [e for e in history if e["event_type"] == "files_deleted"]

    assert deleted, "the track the file belonged to must say so"
    assert "user" in deleted[0]["detail"]


def test_a_sibling_tracks_delete_stays_off_this_tracks_history(imported_conn):
    """Scoping, so the pencil does not turn into the album's timeline."""
    from core.library2.file_delete import delete_files_journaled

    rows = imported_conn.execute(
        "SELECT t.id, t.album_id, f.path FROM lib2_tracks t "
        " JOIN lib2_track_files f ON f.track_id=t.id LIMIT 1"
    ).fetchone()
    other_track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE id <> ? LIMIT 1", (rows[0],)
    ).fetchone()[0]

    class _KeepOpen:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    class _DB:
        def _get_connection(self):
            return _KeepOpen(imported_conn)

    delete_files_journaled(
        _DB(), targets=[{"path": rows[2], "stored_path": rows[2]}],
        entity_type="albums", entity_id=rows[1],
        actor="user", require_library_root=False,
    )

    history = scoped_history(imported_conn, scope="track", entity_id=other_track_id)

    assert not [e for e in history if e["event_type"] == "files_deleted"]


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


def test_track_download_history_accepts_text_legacy_id(imported_conn):
    drake = _drake_ids(imported_conn)
    legacy_track_id = "base62-track-key"
    imported_conn.execute(
        "UPDATE lib2_tracks SET legacy_track_id=? WHERE id=?",
        (legacy_track_id, drake["track_id"]),
    )
    imported_conn.execute(
        """CREATE TABLE IF NOT EXISTS track_downloads(
               id INTEGER PRIMARY KEY AUTOINCREMENT, track_id TEXT, file_path TEXT,
               source_service TEXT, track_title TEXT, track_album TEXT,
               status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    imported_conn.execute(
        "INSERT INTO track_downloads(track_id, source_service, track_title, status) "
        "VALUES(?, 'soulseek', 'One Dance', 'completed')",
        (legacy_track_id,),
    )
    imported_conn.commit()

    history = scoped_history(
        imported_conn, scope="track", entity_id=drake["track_id"]
    )

    assert any(e["event_type"] == "downloaded" for e in history)


def test_unsupported_scope_raises(imported_conn):
    with pytest.raises(ValueError):
        scoped_history(imported_conn, scope="playlist", entity_id=1)


@pytest.mark.parametrize("limit", [0, 501])
def test_limit_out_of_range_raises(imported_conn, limit):
    with pytest.raises(ValueError):
        scoped_history(imported_conn, scope="artist", entity_id=1, limit=limit)


@pytest.mark.parametrize("stored,expected", [
    ('{"track_number": 3}', []),
    ('7', []),
    ('"track_number"', []),
    ('["track_number"]', ["track_number"]),
])
def test_changed_fields_is_always_the_list_the_ui_is_promised(
        imported_conn, stored, expected):
    """`changed_fields_json` is free-form JSON written by whatever repair job
    produced the row, but the feed serves it as `changed_fields: string[]`. A
    stored object or scalar decoded straight through, reaching the UI as a
    shape its own types promise cannot occur — and the detail line's
    `', '.join` would silently render a dict's KEYS as if they were the fields
    that changed."""
    from core.library2.maintenance_sync import ensure_maintenance_event_schema

    drake = _drake_ids(imported_conn)
    ensure_maintenance_event_schema(imported_conn.cursor())
    imported_conn.execute(
        "INSERT INTO lib2_maintenance_events(job_id, action, lib2_track_id, "
        "changed_fields_json) VALUES('track_number_repair', 'track_updated', ?, ?)",
        (drake["track_id"], stored),
    )
    imported_conn.commit()

    history = scoped_history(imported_conn, scope="track", entity_id=drake["track_id"])
    event = next(e for e in history if e["event_type"] == "track_updated")

    assert event["changed_fields"] == expected
