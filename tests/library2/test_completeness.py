"""Tracklist completeness helpers for Library v2."""

from __future__ import annotations

import json

from core.library2.completeness import _persist_tracklist_tracks, precache_tracklists


def test_persist_tracklist_tracks_creates_monitorable_missing_rows(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET monitored=1, quality_profile_id=2 WHERE id=?",
        (views_id,),
    )

    created = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "One Dance"},
        {"track_number": 3, "title": "Missing Provider Title", "spotify_id": "sp-missing"},
    ])

    assert created == 1
    row = imported_conn.execute(
        "SELECT id, monitored, quality_profile_id, spotify_id FROM lib2_tracks "
        "WHERE album_id=? AND track_number=3",
        (views_id,),
    ).fetchone()
    assert row["monitored"] == 1
    assert row["quality_profile_id"] == 2
    assert row["spotify_id"] == "sp-missing"

    linked_artist = imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_track_artists WHERE track_id=?",
        (row["id"],),
    ).fetchone()[0]
    assert linked_artist == 1

    assert _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 3, "title": "Missing Provider Title", "spotify_id": "sp-missing"},
    ]) == 0


def test_precache_materializes_cached_tracklists_before_provider_lookup(legacy_db):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    views_id = conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    conn.execute(
        "UPDATE lib2_albums SET expected_track_count=3, monitored=1, quality_profile_id=2, "
        "tracklist_json=? WHERE id=?",
        (
            json.dumps([
                {"track_number": 1, "title": "One Dance"},
                {"track_number": 2, "title": "Hotline Bling"},
                {"track_number": 3, "title": "Provider Only", "spotify_id": "sp-provider-only"},
            ]),
            views_id,
        ),
    )
    conn.commit()
    conn.close()

    assert precache_tracklists(legacy_db, config_manager=None) >= 1

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT title, monitored, quality_profile_id, spotify_id FROM lib2_tracks "
            "WHERE album_id=? AND track_number=3",
            (views_id,),
        ).fetchone()
        assert dict(row) == {
            "title": "Provider Only",
            "monitored": 1,
            "quality_profile_id": 2,
            "spotify_id": "sp-provider-only",
        }
    finally:
        conn.close()


def test_persist_tracklist_tracks_infers_discs_when_numbers_reset(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=4, monitored=1 WHERE id=?",
        (views_id,),
    )

    created = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "Disc 1 A"},
        {"track_number": 2, "title": "Disc 1 B"},
        {"track_number": 1, "title": "Disc 2 A"},
        {"track_number": 2, "title": "Disc 2 B"},
    ])

    assert created == 2
    rows = imported_conn.execute(
        "SELECT title, track_number, disc_number FROM lib2_tracks "
        "WHERE album_id=? ORDER BY disc_number, track_number",
        (views_id,),
    ).fetchall()
    assert [dict(r) for r in rows][-2:] == [
        {"title": "Disc 2 A", "track_number": 1, "disc_number": 2},
        {"title": "Disc 2 B", "track_number": 2, "disc_number": 2},
    ]


def test_persist_tracklist_tracks_trims_surplus_fileless_rows(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=2 WHERE id=?",
        (views_id,),
    )
    imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?, 'stale provider row', 3, 1, 0)",
        (views_id,),
    )
    stale_id = imported_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "SELECT ?, primary_artist_id, 'primary', 0 FROM lib2_albums WHERE id=?",
        (stale_id, views_id),
    )

    changed = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "One Dance"},
        {"track_number": 2, "title": "Hotline Bling"},
    ])

    assert changed == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE id=?",
        (stale_id,),
    ).fetchone()[0] == 0
