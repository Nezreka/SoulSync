"""Per-provider match-status for Library v2 entities.

The legacy Enhanced View shows colored provider chips (Spotify/MusicBrainz/…)
per artist/album/track. lib2 keeps a back-reference to the legacy row
(``legacy_artist_id`` / ``legacy_album_id`` / ``legacy_track_id``), and the
legacy tables carry the ``{service}_match_status`` / ``{service}_id`` columns —
so we can surface the exact same match data with no migration.
"""

from __future__ import annotations

import pytest

from core.library2 import match_status as MS


def _drake_lib2_id(conn) -> int:
    return conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]


def test_artist_match_derives_matched_from_existing_provider_id(imported_conn):
    # The seed gives Drake a legacy spotify_artist_id ('sp1') but no explicit
    # *_match_status column — presence of the id means "matched".
    rows = MS.entity_match_status(imported_conn, "artist", _drake_lib2_id(imported_conn))
    by_service = {r["service"]: r for r in rows}

    assert by_service["spotify"]["status"] == "matched"
    assert by_service["spotify"]["external_id"] == "sp1"
    assert by_service["musicbrainz"]["status"] == "pending"
    assert by_service["musicbrainz"]["external_id"] is None


def test_explicit_match_status_column_wins_when_present(imported_conn):
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_id TEXT")
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_match_status TEXT")
    imported_conn.execute(
        "UPDATE artists SET deezer_id='dz9', deezer_match_status='matched' WHERE id=1"
    )
    imported_conn.commit()

    rows = MS.entity_match_status(imported_conn, "artist", _drake_lib2_id(imported_conn))
    deezer = next(r for r in rows if r["service"] == "deezer")

    assert deezer["status"] == "matched"
    assert deezer["external_id"] == "dz9"


def test_entity_without_legacy_backref_returns_synthetic_pending_chips(imported_conn):
    # A row without legacy source row returns synthetic chips matching its own columns.
    new_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, quality_profile_id, spotify_id) "
        "VALUES('Ghost', 'Ghost', 1, 'ghost_sp')"
    ).lastrowid
    imported_conn.commit()

    rows = MS.entity_match_status(imported_conn, "artist", new_id)
    assert len(rows) > 0

    by_service = {r["service"]: r for r in rows}
    assert by_service["spotify"]["status"] == "matched"
    assert by_service["spotify"]["external_id"] == "ghost_sp"
    assert by_service["spotify"]["legacy_entity_id"] is None

    assert by_service["musicbrainz"]["status"] == "pending"
    assert by_service["musicbrainz"]["legacy_entity_id"] is None


def test_track_match_reads_legacy_track_row(imported_conn):
    imported_conn.execute("ALTER TABLE tracks ADD COLUMN spotify_track_id TEXT")
    imported_conn.execute("UPDATE tracks SET spotify_track_id='spt' WHERE id=100")
    imported_conn.commit()
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]

    rows = MS.entity_match_status(imported_conn, "track", track_id)
    spotify = next(r for r in rows if r["service"] == "spotify")

    assert spotify["status"] == "matched"
    assert spotify["external_id"] == "spt"
    assert spotify["legacy_entity_id"] == 100


def test_only_services_applicable_to_entity_type_are_returned(imported_conn):
    # 'discogs' has no track-level id column; 'bandcamp' has no artist column.
    artist_services = {
        r["service"]
        for r in MS.entity_match_status(imported_conn, "artist", _drake_lib2_id(imported_conn))
    }
    assert "discogs" in artist_services
    assert "bandcamp" not in artist_services


def test_unknown_entity_type_raises(imported_conn):
    with pytest.raises(ValueError):
        MS.entity_match_status(imported_conn, "playlist", 1)


def test_album_match_bundle_returns_album_and_track_chips(imported_conn):
    imported_conn.execute("ALTER TABLE tracks ADD COLUMN spotify_track_id TEXT")
    imported_conn.execute("UPDATE tracks SET spotify_track_id='spt' WHERE id=100")
    imported_conn.commit()
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    one_dance = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]

    bundle = MS.album_match_bundle(imported_conn, views_id)

    assert isinstance(bundle["album"], list)
    spotify = next(r for r in bundle["tracks"][one_dance] if r["service"] == "spotify")
    assert spotify["status"] == "matched"
    assert spotify["external_id"] == "spt"
