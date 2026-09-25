"""What the one-shot legacy migration must carry over.

The importer runs exactly once per installation, on the update that switches a
user to Library v2. Anything it drops is not "missing until the next scan" —
it is gone, because the legacy tables go with it. These tests name each thing
the legacy row knows that the catalogue would otherwise have to guess or
re-fetch.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.importer import import_legacy_library


@pytest.fixture
def imported(migrated_legacy_db):
    """The synthetic legacy library after a full migration."""
    def _run():
        import_legacy_library(migrated_legacy_db)
        conn = sqlite3.connect(migrated_legacy_db.path)
        conn.row_factory = sqlite3.Row
        return conn
    return _run


def _set(db, sql, params=()):
    conn = sqlite3.connect(db.path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


class TestReleaseType:
    """Legacy calls it `record_type`; the catalogue calls it `album_type`. The
    normalizer only looked for the v2 spelling, so every EP, compilation and
    live album arrived as a plain 'album' — with the answer sitting in the row
    it was reading."""

    @pytest.mark.parametrize("legacy,expected", [
        ("ep", "ep"),
        ("compilation", "compilation"),
        ("live", "live"),
        ("album", "album"),
    ])
    def test_record_type_survives(self, migrated_legacy_db, imported, legacy, expected):
        _set(migrated_legacy_db, "UPDATE albums SET record_type=? WHERE id=10", (legacy,))

        conn = imported()
        row = conn.execute(
            "SELECT album_type FROM lib2_albums WHERE legacy_album_id=10").fetchone()
        assert row["album_type"] == expected

    def test_a_typeless_row_still_falls_back_to_the_heuristic(
            self, migrated_legacy_db, imported):
        """Album 11 has one track and no record_type — the old guess still
        applies to rows that genuinely never learned their type."""
        conn = imported()
        row = conn.execute(
            "SELECT album_type FROM lib2_albums WHERE legacy_album_id=11").fetchone()
        assert row["album_type"] == "single"


class TestDateAdded:
    """`created_at` is when the library got the thing. Not carrying it makes
    every row look added on migration day, which is what "recently added"
    sorts on."""

    def test_artist_album_and_track_keep_their_date(self, migrated_legacy_db, imported):
        _set(migrated_legacy_db, "UPDATE artists SET created_at='2019-03-01 10:00:00'")
        _set(migrated_legacy_db, "UPDATE albums SET created_at='2019-03-02 10:00:00'")
        _set(migrated_legacy_db, "UPDATE tracks SET created_at='2019-03-03 10:00:00'")

        conn = imported()
        assert conn.execute(
            "SELECT added_at FROM lib2_artists WHERE legacy_artist_id=1"
        ).fetchone()[0].startswith("2019-03-01")
        assert conn.execute(
            "SELECT added_at FROM lib2_albums WHERE legacy_album_id=10"
        ).fetchone()[0].startswith("2019-03-02")
        assert conn.execute(
            "SELECT added_at FROM lib2_tracks WHERE legacy_track_id=100"
        ).fetchone()[0].startswith("2019-03-03")

    def test_a_row_without_a_date_gets_the_default(self, migrated_legacy_db, imported):
        conn = imported()
        added_at = conn.execute(
            "SELECT added_at FROM lib2_artists WHERE legacy_artist_id=1").fetchone()[0]
        assert added_at  # CURRENT_TIMESTAMP, not NULL


class TestServerIdentity:
    """A legacy row's id IS the media server's own id (that is the whole #1069
    story). Dropping it leaves the catalogue unable to say which server
    reported a row — removal detection, per-server statistics and the M3U
    export filter all go silent, and the next scan has to re-match by name."""

    def test_server_source_and_id_come_across(self, migrated_legacy_db, imported):
        _set(migrated_legacy_db, "UPDATE artists SET server_source='plex'")
        _set(migrated_legacy_db, "UPDATE albums SET server_source='plex'")
        _set(migrated_legacy_db, "UPDATE tracks SET server_source='plex'")

        conn = imported()
        artist = conn.execute(
            "SELECT server_source, server_id FROM lib2_artists WHERE legacy_artist_id=1"
        ).fetchone()
        assert (artist["server_source"], artist["server_id"]) == ("plex", "1")
        album = conn.execute(
            "SELECT server_source, server_id FROM lib2_albums WHERE legacy_album_id=10"
        ).fetchone()
        assert (album["server_source"], album["server_id"]) == ("plex", "10")
        track = conn.execute(
            "SELECT server_source, server_id FROM lib2_tracks WHERE legacy_track_id=100"
        ).fetchone()
        assert (track["server_source"], track["server_id"]) == ("plex", "100")

    def test_a_row_no_server_reported_keeps_no_server_id(
            self, migrated_legacy_db, imported):
        """Downloads imported by SoulSync itself have no server_source in the
        legacy schema. Minting a server id for them would tell the scan that a
        server it never asked knows this row."""
        conn = imported()
        row = conn.execute(
            "SELECT server_source, server_id FROM lib2_artists WHERE legacy_artist_id=1"
        ).fetchone()
        assert (row["server_source"], row["server_id"]) == (None, None)


class TestCanonicalPin:
    """#758: the user picked which release of an album is the real one. That
    is a decision, not derived data — nothing can recompute it."""

    def test_the_pin_survives_the_migration(self, migrated_legacy_db, imported):
        _set(migrated_legacy_db,
             "UPDATE albums SET canonical_source='deezer', canonical_album_id='dz-9',"
             "                  canonical_score=1.0, canonical_locked=1,"
             "                  canonical_resolved_at='2026-01-02 03:04:05'"
             " WHERE id=10")

        conn = imported()
        row = conn.execute(
            "SELECT canonical_source, canonical_album_id, canonical_score,"
            "       canonical_locked, canonical_resolved_at"
            "  FROM lib2_albums WHERE legacy_album_id=10").fetchone()
        assert row["canonical_source"] == "deezer"
        assert row["canonical_album_id"] == "dz-9"
        assert row["canonical_score"] == 1.0
        assert row["canonical_locked"] == 1
        assert row["canonical_resolved_at"].startswith("2026-01-02")

    def test_an_unpinned_album_stays_unpinned(self, migrated_legacy_db, imported):
        conn = imported()
        row = conn.execute(
            "SELECT canonical_source FROM lib2_albums WHERE legacy_album_id=11").fetchone()
        assert row["canonical_source"] is None


class TestProviderEnrichment:
    """Every provider bio, wiki, tag list and stat the library ever fetched.
    Artists were carried; albums and tracks were not — the migration wrote an
    empty ``{}`` over a Last.fm wiki, a Discogs catalogue number and a
    Bandcamp label alike, and re-fetching them is thousands of API calls the
    user already paid for.

    The declaration lives in ``enrich._ENRICHMENT_PAYLOAD``. The migration is
    its only reader now, so a provider added there is carried by the upgrade
    without a second edit."""

    def test_album_payload_crosses(self, migrated_legacy_db, imported):
        _set(migrated_legacy_db,
             "UPDATE albums SET lastfm_wiki='A wiki', lastfm_playcount=42,"
             "                  discogs_label='XL', bandcamp_id='4204242' WHERE id=10")

        conn = imported()
        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_albums WHERE legacy_album_id=10").fetchone()[0])
        assert payload["lastfm"]["wiki"] == "A wiki"
        assert payload["lastfm"]["playcount"] == 42
        assert payload["discogs"]["label"] == "XL"
        assert payload["bandcamp"]["id"] == "4204242"

    def test_track_payload_crosses(self, migrated_legacy_db, imported):
        _set(migrated_legacy_db,
             "UPDATE tracks SET lastfm_playcount=777, lastfm_tags='[\"rap\"]',"
             "                  genius_description='About the song',"
             "                  bandcamp_id='77' WHERE id=100")

        conn = imported()
        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_tracks WHERE legacy_track_id=100").fetchone()[0])
        assert payload["lastfm"]["playcount"] == 777
        assert payload["lastfm"]["tags"] == ["rap"]
        assert payload["genius"]["description"] == "About the song"
        assert payload["bandcamp"]["id"] == "77"

    def test_artist_payload_still_crosses(self, migrated_legacy_db, imported):
        _set(migrated_legacy_db,
             "UPDATE artists SET lastfm_bio='A bio', discogs_bio='Another' WHERE id=1")

        conn = imported()
        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_artists WHERE legacy_artist_id=1").fetchone()[0])
        assert payload["lastfm"]["bio"] == "A bio"
        assert payload["discogs"]["bio"] == "Another"

    def test_a_provider_that_wrote_nothing_leaves_no_bucket(
            self, migrated_legacy_db, imported):
        conn = imported()
        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_albums WHERE legacy_album_id=11").fetchone()[0])
        assert payload == {}


class TestSoulId:
    """The SoulID worker spends an API round-trip per artist to derive the
    cross-install content key, and records WHICH derivation it used. Only
    'canonical' is reproducible elsewhere; the album fallback depends on what
    this library happened to own that day, so the path cannot be recomputed
    after the fact — it has to travel with the id or it is gone when the legacy
    tables go."""

    def test_the_id_and_its_derivation_path_both_cross(
            self, migrated_legacy_db, imported):
        _set(migrated_legacy_db,
             "UPDATE artists SET soul_id='soul_abc', soul_id_path='canonical' "
             "WHERE id=1")

        conn = imported()
        row = conn.execute(
            "SELECT soul_id, soul_id_path FROM lib2_artists WHERE legacy_artist_id=1"
        ).fetchone()
        assert row["soul_id"] == "soul_abc"
        assert row["soul_id_path"] == "canonical"
