"""§56.2 provider match provenance across automatic/manual/clear writes."""

from __future__ import annotations

from core.enrichment.match_provenance import record_manual_match
from database.music_database import MusicDatabase


def _row(conn):
    return conn.execute(
        """SELECT origin, external_id, actor
             FROM metadata_match_provenance
            WHERE entity_type='artist' AND entity_id=1 AND service='spotify'"""
    ).fetchone()


def test_worker_contract_records_automatic_and_manual_choice_is_sticky(tmp_path):
    db = MusicDatabase(str(tmp_path / "matches.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists(id, name) VALUES(1, 'Drake')")

    # This is the exact shared write contract used by every enrichment worker.
    conn.execute(
        """UPDATE artists
              SET spotify_artist_id='sp-auto', spotify_match_status='matched',
                  spotify_last_attempted=CURRENT_TIMESTAMP
            WHERE id=1"""
    )
    assert dict(_row(conn)) == {
        "origin": "automatic",
        "external_id": "sp-auto",
        "actor": "system",
    }

    # The route performs its legacy UPDATE first (trigger says automatic), then
    # records the user decision in the same transaction.
    conn.execute(
        """UPDATE artists
              SET spotify_artist_id='sp-manual', spotify_match_status='matched',
                  spotify_last_attempted=CURRENT_TIMESTAMP
            WHERE id=1"""
    )
    record_manual_match(
        conn,
        entity_type="artist",
        entity_id=1,
        service="spotify",
        external_id="sp-manual",
        actor="profile:1",
    )
    assert dict(_row(conn)) == {
        "origin": "manual",
        "external_id": "sp-manual",
        "actor": "profile:1",
    }

    # A worker re-confirming the same stored id must not erase user intent.
    conn.execute(
        """UPDATE artists
              SET spotify_match_status='matched',
                  spotify_last_attempted=CURRENT_TIMESTAMP
            WHERE id=1"""
    )
    assert _row(conn)["origin"] == "manual"
    conn.close()


def test_clear_match_removes_provenance(tmp_path):
    db = MusicDatabase(str(tmp_path / "matches.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists(id, name) VALUES(1, 'Drake')")
    conn.execute(
        """UPDATE artists
              SET spotify_artist_id='sp1', spotify_match_status='matched'
            WHERE id=1"""
    )
    assert _row(conn) is not None

    conn.execute(
        """UPDATE artists
              SET spotify_artist_id=NULL, spotify_match_status='not_found'
            WHERE id=1"""
    )
    assert _row(conn) is None
    conn.close()


def test_existing_match_is_seeded_as_legacy_when_provenance_is_missing(tmp_path):
    db = MusicDatabase(str(tmp_path / "matches.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists(id, name) VALUES(1, 'Drake')")
    conn.execute(
        """UPDATE artists
              SET spotify_artist_id='sp-existing', spotify_match_status='matched'
            WHERE id=1"""
    )
    conn.execute(
        """DELETE FROM metadata_match_provenance
            WHERE entity_type='artist' AND entity_id=1 AND service='spotify'"""
    )

    # Re-running the additive migration represents opening an existing database
    # after upgrading. Historical matches must not be mislabeled automatic.
    db._add_metadata_match_provenance(conn.cursor())

    assert dict(_row(conn)) == {
        "origin": "legacy",
        "external_id": "sp-existing",
        "actor": "migration",
    }
    conn.close()


def test_shared_trigger_contract_covers_album_and_track_provider_columns(tmp_path):
    db = MusicDatabase(str(tmp_path / "matches.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists(id, name) VALUES(1, 'Drake')")
    conn.execute("INSERT INTO albums(id, artist_id, title) VALUES(2, 1, 'Take Care')")
    conn.execute(
        "INSERT INTO tracks(id, album_id, artist_id, title) VALUES(3, 2, 1, 'Headlines')"
    )

    conn.execute(
        """UPDATE albums
              SET bandcamp_url='https://artist.bandcamp.com/album/take-care',
                  bandcamp_match_status='matched'
            WHERE id=2"""
    )
    conn.execute(
        """UPDATE tracks
              SET qobuz_id='q-track-3', qobuz_match_status='matched'
            WHERE id=3"""
    )

    rows = conn.execute(
        """SELECT entity_type, entity_id, service, origin, external_id
             FROM metadata_match_provenance
            WHERE (entity_type='album' AND entity_id=2)
               OR (entity_type='track' AND entity_id=3)
            ORDER BY entity_type"""
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "entity_type": "album",
            "entity_id": 2,
            "service": "bandcamp",
            "origin": "automatic",
            "external_id": "https://artist.bandcamp.com/album/take-care",
        },
        {
            "entity_type": "track",
            "entity_id": 3,
            "service": "qobuz",
            "origin": "automatic",
            "external_id": "q-track-3",
        },
    ]
    conn.close()


def test_manual_match_provenance_accepts_text_entity_ids(tmp_path):
    """Media-server entity ids may be opaque TEXT, including Spotify base62."""
    entity_id = "01MoTj8w4VkVtgdPOijUUE"
    db = MusicDatabase(str(tmp_path / "text-matches.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists(id, name) VALUES(?, 'Text Artist')", (entity_id,))
    conn.execute(
        """UPDATE artists
              SET spotify_artist_id='spotify-artist',
                  spotify_match_status='matched'
            WHERE id=?""",
        (entity_id,),
    )

    record_manual_match(
        conn,
        entity_type="artist",
        entity_id=entity_id,
        service="spotify",
        external_id="spotify-artist",
        actor="profile:1",
    )

    row = conn.execute(
        """SELECT entity_id, origin, actor
             FROM metadata_match_provenance
            WHERE entity_type='artist' AND entity_id=? AND service='spotify'""",
        (entity_id,),
    ).fetchone()
    conn.close()
    assert dict(row) == {
        "entity_id": entity_id,
        "origin": "manual",
        "actor": "profile:1",
    }
