"""Tracklist completeness helpers for Library v2."""

from __future__ import annotations

import json

from core.library2.completeness import (
    _persist_tracklist_tracks,
    precache_tracklists,
    resolve_tracklist,
)


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
        snapshot = conn.execute(
            """SELECT provider, is_complete, parser_version, payload_json
                 FROM library_provider_snapshots
                WHERE entity_type='album' AND entity_id=? AND scope='tracklist'""",
            (views_id,),
        ).fetchone()
        assert snapshot["provider"] == "legacy-cache"
        assert snapshot["is_complete"] == 1
        assert snapshot["parser_version"] == "library2-tracklist/1"
        assert json.loads(snapshot["payload_json"])["reference"][
            "release_edition_id"] is not None
    finally:
        conn.close()


def test_resolve_tracklist_snapshots_spotify_and_reuses_durable_cache(
        imported_conn, monkeypatch):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = imported_conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-edition-1' WHERE id=?",
        (edition_id,),
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET tracklist_json=NULL WHERE id=?", (views_id,))

    class Spotify:
        calls = []

        def get_album_tracks(self, album_id):
            self.calls.append(album_id)
            return {"items": [
                {"id": "sp-t1", "name": "One Dance", "track_number": 1,
                 "disc_number": 1, "duration_ms": 180000},
                {"id": "sp-t2", "name": "Hotline Bling", "track_number": 2,
                 "disc_number": 1, "duration_ms": 200000},
            ]}

    spotify = Spotify()
    monkeypatch.setattr("core.metadata.registry.get_spotify_client", lambda: spotify)
    monkeypatch.setattr("core.metadata.registry.get_deezer_client", lambda: None)

    first = resolve_tracklist(None, imported_conn, views_id)
    second = resolve_tracklist(None, imported_conn, views_id)

    assert first == second
    assert spotify.calls == ["sp-edition-1"]
    snapshot = imported_conn.execute(
        """SELECT provider, provider_entity_id, is_complete, parser_version,
                  payload_json
             FROM library_provider_snapshots
            WHERE entity_type='album' AND entity_id=? AND scope='tracklist'""",
        (views_id,),
    ).fetchone()
    payload = json.loads(snapshot["payload_json"])
    assert snapshot["provider"] == "spotify"
    assert snapshot["provider_entity_id"] == "sp-edition-1"
    assert snapshot["is_complete"] == 1
    assert snapshot["parser_version"] == "library2-tracklist/1"
    assert payload["reference"]["release_edition_id"] == edition_id
    assert payload["tracks"][0]["spotify_id"] == "sp-t1"


def test_default_edition_change_invalidates_tracklist_cache(
        imported_conn, monkeypatch):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = imported_conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-standard' WHERE id=?",
        (edition_id,),
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET tracklist_json=NULL WHERE id=?", (views_id,))

    class Spotify:
        calls = []

        def get_album_tracks(self, album_id):
            self.calls.append(album_id)
            return {"items": [{
                "id": f"{album_id}-track",
                "name": f"Track from {album_id}",
                "track_number": 1,
            }]}

    spotify = Spotify()
    monkeypatch.setattr("core.metadata.registry.get_spotify_client", lambda: spotify)
    monkeypatch.setattr("core.metadata.registry.get_deezer_client", lambda: None)

    first = resolve_tracklist(None, imported_conn, views_id)
    imported_conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-deluxe' WHERE id=?",
        (edition_id,),
    )
    second = resolve_tracklist(None, imported_conn, views_id)

    assert first[0]["title"] == "Track from sp-standard"
    assert second[0]["title"] == "Track from sp-deluxe"
    assert spotify.calls == ["sp-standard", "sp-deluxe"]
    latest = imported_conn.execute(
        """SELECT provider_entity_id, payload_json
             FROM library_provider_snapshots
            WHERE entity_type='album' AND entity_id=? AND scope='tracklist'
            ORDER BY fetched_at DESC, id DESC LIMIT 1""",
        (views_id,),
    ).fetchone()
    assert latest["provider_entity_id"] == "sp-deluxe"
    assert json.loads(latest["payload_json"])["reference"]["spotify_id"] == "sp-deluxe"


def test_edition_change_persists_invalidation_when_provider_is_unavailable(
        legacy_db, monkeypatch):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    views_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-old' WHERE id=?",
        (edition_id,),
    )
    conn.execute(
        "UPDATE lib2_albums SET tracklist_json=? WHERE id=?",
        (json.dumps([{"track_number": 1, "title": "Old Edition"}]), views_id),
    )
    conn.commit()
    assert resolve_tracklist(None, conn, views_id)[0]["title"] == "Old Edition"

    conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-new' WHERE id=?",
        (edition_id,),
    )
    conn.commit()
    monkeypatch.setattr("core.metadata.registry.get_spotify_client", lambda: None)
    monkeypatch.setattr("core.metadata.registry.get_deezer_client", lambda: None)
    assert resolve_tracklist(None, conn, views_id) is None
    conn.close()

    reopened = legacy_db._get_connection()
    try:
        row = reopened.execute(
            "SELECT tracklist_json, tracklist_status FROM lib2_albums WHERE id=?",
            (views_id,),
        ).fetchone()
        assert row["tracklist_json"] is None
        assert row["tracklist_status"] == "idle"
    finally:
        reopened.close()


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
