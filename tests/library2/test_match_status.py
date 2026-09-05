"""Per-provider match-status for Library v2 entities.

The legacy Enhanced View shows colored provider chips (Spotify/MusicBrainz/…)
per artist/album/track. lib2 keeps a back-reference to the legacy row
(``legacy_artist_id`` / ``legacy_album_id`` / ``legacy_track_id``), and the
legacy tables carry the ``{service}_match_status`` / ``{service}_id`` columns —
so we can surface the exact same match data with no migration.
"""

from __future__ import annotations

import sqlite3

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


def test_legacy_match_status_does_not_override_native_row(imported_conn):
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_id TEXT")
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_match_status TEXT")
    imported_conn.execute(
        "UPDATE artists SET deezer_id='dz9', deezer_match_status='matched' WHERE id=1"
    )
    imported_conn.commit()

    rows = MS.entity_match_status(imported_conn, "artist", _drake_lib2_id(imported_conn))
    deezer = next(r for r in rows if r["service"] == "deezer")

    assert deezer["status"] == "pending"
    assert deezer["external_id"] is None


def test_legacy_match_provenance_does_not_leak_into_native_chips(imported_conn):
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_id TEXT")
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_match_status TEXT")
    imported_conn.execute("ALTER TABLE artists ADD COLUMN deezer_last_attempted TEXT")
    imported_conn.execute(
        """CREATE TABLE metadata_match_provenance(
               entity_type TEXT, entity_id INTEGER, service TEXT, origin TEXT,
               external_id TEXT, matched_at TEXT, actor TEXT)"""
    )
    imported_conn.execute(
        "UPDATE artists SET deezer_id='dz9', deezer_match_status='matched' WHERE id=1"
    )
    imported_conn.execute(
        """INSERT INTO metadata_match_provenance
               VALUES('artist', 1, 'deezer', 'manual', 'dz9', '2026-07-17 12:00:00', 'profile:1')"""
    )
    imported_conn.commit()

    rows = MS.entity_match_status(imported_conn, "artist", _drake_lib2_id(imported_conn))
    deezer = next(r for r in rows if r["service"] == "deezer")

    assert deezer["match_origin"] is None
    assert deezer["matched_at"] is None


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
    assert by_service["spotify"]["library_v2_entity_id"] == new_id

    assert by_service["musicbrainz"]["status"] == "pending"
    assert by_service["musicbrainz"]["legacy_entity_id"] is None


def test_lib2_native_entity_can_be_manually_matched_and_cleared(imported_conn):
    artist_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Native', 'Native')"
    ).lastrowid

    MS.set_library_v2_match(
        imported_conn, "artist", artist_id, "deezer", "dz-native"
    )
    rows = MS.entity_match_status(imported_conn, "artist", artist_id)
    deezer = next(row for row in rows if row["service"] == "deezer")
    assert deezer["status"] == "matched"
    assert deezer["external_id"] == "dz-native"
    assert deezer["library_v2_entity_id"] == artist_id

    MS.set_library_v2_match(imported_conn, "artist", artist_id, "deezer", None)
    rows = MS.entity_match_status(imported_conn, "artist", artist_id)
    deezer = next(row for row in rows if row["service"] == "deezer")
    assert deezer["status"] == "pending"
    assert deezer["external_id"] is None


def test_track_match_ignores_legacy_track_row(imported_conn):
    imported_conn.execute("ALTER TABLE tracks ADD COLUMN spotify_track_id TEXT")
    imported_conn.execute("UPDATE tracks SET spotify_track_id='spt' WHERE id=100")
    imported_conn.commit()
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]

    rows = MS.entity_match_status(imported_conn, "track", track_id)
    spotify = next(r for r in rows if r["service"] == "spotify")

    assert spotify["status"] == "pending"
    assert spotify["external_id"] is None
    assert spotify["legacy_entity_id"] is None


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


def test_available_services_flags_chips_from_a_legacy_row(imported_conn):
    rows = MS.entity_match_status(
        imported_conn, "artist", _drake_lib2_id(imported_conn),
        available_services={"spotify", "deezer"},
    )
    by_service = {r["service"]: r["available"] for r in rows}

    assert by_service["spotify"] is True
    assert by_service["musicbrainz"] is False
    assert by_service["deezer"] is True
    assert by_service["discogs"] is False


def test_available_services_flags_synthetic_chips(imported_conn):
    new_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, quality_profile_id, spotify_id) "
        "VALUES('Ghost', 'Ghost', 1, 'ghost_sp')"
    ).lastrowid
    imported_conn.commit()

    rows = MS.entity_match_status(
        imported_conn, "artist", new_id, available_services={"spotify"},
    )
    by_service = {r["service"]: r["available"] for r in rows}

    assert by_service["spotify"] is True
    assert by_service["musicbrainz"] is False


def test_omitted_available_services_defaults_every_chip_available(imported_conn):
    rows = MS.entity_match_status(imported_conn, "artist", _drake_lib2_id(imported_conn))
    assert all(r["available"] is True for r in rows)


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
    imported_conn.execute(
        "UPDATE lib2_tracks SET spotify_id='spt' WHERE id=?", (one_dance,)
    )
    imported_conn.commit()

    bundle = MS.album_match_bundle(imported_conn, views_id)

    assert isinstance(bundle["album"], list)
    spotify = next(r for r in bundle["tracks"][one_dance] if r["service"] == "spotify")
    assert spotify["status"] == "matched"
    assert spotify["external_id"] == "spt"


def test_album_match_bundle_propagates_available_services_to_album_and_tracks(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    one_dance = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]

    bundle = MS.album_match_bundle(imported_conn, views_id, available_services={"spotify"})

    album_spotify = next(r for r in bundle["album"] if r["service"] == "spotify")
    album_deezer = next(r for r in bundle["album"] if r["service"] == "deezer")
    track_spotify = next(r for r in bundle["tracks"][one_dance] if r["service"] == "spotify")
    track_deezer = next(r for r in bundle["tracks"][one_dance] if r["service"] == "deezer")
    assert album_spotify["available"] is True
    assert album_deezer["available"] is False
    assert track_spotify["available"] is True
    assert track_deezer["available"] is False


def test_match_status_ignores_text_legacy_ids_and_provenance(tmp_path):
    """P3 never resolves provider state through opaque legacy identities."""
    album_legacy_id = "01MoTj8w4VkVtgdPOijUUE"
    track_legacy_id = "base62-track-key"
    conn = sqlite3.connect(str(tmp_path / "text-match-ids.db"))
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE albums(
            id TEXT PRIMARY KEY, title TEXT, spotify_album_id TEXT,
            spotify_match_status TEXT, spotify_last_attempted TEXT);
        CREATE TABLE tracks(
            id TEXT PRIMARY KEY, title TEXT, spotify_track_id TEXT,
            spotify_match_status TEXT, spotify_last_attempted TEXT);
        CREATE TABLE metadata_match_provenance(
            entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
            service TEXT NOT NULL, origin TEXT NOT NULL, external_id TEXT,
            matched_at TEXT, actor TEXT,
            PRIMARY KEY(entity_type, entity_id, service));
    """)
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Text Artist', ?)",
        ("artist-text-key",),
    ).lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Text Album', ?)",
        (artist_id, album_legacy_id),
    ).lastrowid
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, legacy_track_id) "
        "VALUES(?, 'Text Track', ?)",
        (album_id, track_legacy_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO albums VALUES(?, 'Text Album', 'spotify-album', 'matched', '2026-07-17')",
        (album_legacy_id,),
    )
    conn.execute(
        "INSERT INTO tracks VALUES(?, 'Text Track', 'spotify-track', 'matched', '2026-07-17')",
        (track_legacy_id,),
    )
    conn.execute(
        "INSERT INTO metadata_match_provenance VALUES"
        "('album', ?, 'spotify', 'manual', 'spotify-album', '2026-07-17', 'admin')",
        (album_legacy_id,),
    )
    conn.execute(
        "INSERT INTO metadata_match_provenance VALUES"
        "('track', ?, 'spotify', 'automatic', 'spotify-track', '2026-07-17', 'system')",
        (track_legacy_id,),
    )
    conn.commit()

    album_services = MS.entity_match_status(conn, "album", album_id)
    album_spotify = next(row for row in album_services if row["service"] == "spotify")
    assert album_spotify["legacy_entity_id"] is None
    assert album_spotify["external_id"] is None
    assert album_spotify["match_origin"] is None

    bundle = MS.album_match_bundle(conn, album_id)
    track_spotify = next(
        row for row in bundle["tracks"][track_id] if row["service"] == "spotify"
    )
    assert track_spotify["legacy_entity_id"] is None
    assert track_spotify["external_id"] is None
    assert track_spotify["match_origin"] is None
    conn.close()


def test_artist_enrichment_coverage_counts_tracks_per_provider(imported_conn):
    """ldp-05: the rich header's rings answer "how many of this artist's
    TRACKS does each provider actually know?" — a different question from the
    artist-level chips, and the one legacy showed. Detection reuses the chips'
    own id resolution, so a track cannot count as matched in one place and
    unmatched in the other."""
    conn = imported_conn
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Portishead','Portishead')"
    ).lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Dummy')",
        (artist_id,),
    ).lastrowid
    for spotify_id, external in (("sp-1", "{}"), ("sp-2", '{"deezer":"dz-2"}'), (None, "{}")):
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, spotify_id, external_ids) "
            "VALUES(?, 'T', ?, ?)", (album_id, spotify_id, external))

    from core.library2.match_status import artist_enrichment_coverage

    coverage = artist_enrichment_coverage(conn, artist_id)

    assert coverage["total_tracks"] == 3
    assert coverage["spotify"] == pytest.approx(66.7, abs=0.1)
    assert coverage["deezer"] == pytest.approx(33.3, abs=0.1)
    assert "musicbrainz" not in coverage


def test_artist_enrichment_coverage_is_empty_without_tracks(imported_conn):
    conn = imported_conn
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Nobody','Nobody')"
    ).lastrowid

    from core.library2.match_status import artist_enrichment_coverage

    assert artist_enrichment_coverage(conn, artist_id) == {"total_tracks": 0}


class TestManualMatchKeepsTheAttemptLedgerInStep:
    """L2-004 — the chip reads the entity's id, the enrichment queue reads
    ``lib2_provider_attempts``. A manual match wrote only the first, so the two
    disagreed in both directions."""

    @staticmethod
    def _owned_artist(conn, name="Rone"):
        from core.library2.provider_attempts import (
            ensure_provider_attempt_schema, record_attempt,
        )
        ensure_provider_attempt_schema(conn.cursor())
        # Settle everything the seed already holds so the queue's answer is
        # about the artist under test and nothing else.
        for row in conn.execute("SELECT id FROM lib2_artists").fetchall():
            record_attempt(conn, entity_type="artist", entity_id=int(row[0]),
                           service="spotify", status="matched")
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name),
        ).lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Bora')", (album,)).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
            "VALUES(?,?,1,'active')", (track, f"/music/{track}.flac"))
        return artist

    def test_setting_an_id_settles_the_queue_entry(self, imported_conn):
        from core.library2.worker_queue import next_pending

        conn = imported_conn
        artist = self._owned_artist(conn)
        assert next_pending(conn, "spotify", entity_types=("artist",))["id"] == artist

        MS.set_library_v2_match(conn, "artist", artist, "spotify", "sp-manual")

        row = conn.execute(
            "SELECT status, detail FROM lib2_provider_attempts "
            "WHERE entity_type='artist' AND entity_id=? AND service='spotify'",
            (artist,)).fetchone()
        assert row["status"] == "matched"
        # …and the worker no longer picks the entity it was just told about.
        picked = next_pending(conn, "spotify", entity_types=("artist",))
        assert picked is None or picked["id"] != artist

    def test_clearing_an_id_makes_the_entity_selectable_again(self, imported_conn):
        from core.library2.worker_queue import next_pending

        conn = imported_conn
        artist = self._owned_artist(conn)
        MS.set_library_v2_match(conn, "artist", artist, "spotify", "sp-manual")

        MS.set_library_v2_match(conn, "artist", artist, "spotify", None)

        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts "
            "WHERE entity_type='artist' AND entity_id=? AND service='spotify'",
            (artist,)).fetchone()[0] == 0
        # No retry window has to expire first: it is unanswered, not failed.
        assert next_pending(conn, "spotify", entity_types=("artist",))["id"] == artist

    def test_a_missing_ledger_table_does_not_break_the_match(self, imported_conn):
        conn = imported_conn
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES('Old','Old')",
        ).lastrowid
        conn.execute("DROP TABLE IF EXISTS lib2_provider_attempts")

        MS.set_library_v2_match(conn, "artist", artist, "spotify", "sp-1")

        assert conn.execute(
            "SELECT spotify_id FROM lib2_artists WHERE id=?", (artist,),
        ).fetchone()[0] == "sp-1"
