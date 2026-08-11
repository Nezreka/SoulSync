"""Album and track provider payload reaches lib2 (docs §50.4.4.3).

Only ``lib2_artists`` had an ``enrichment`` column, so the album and track
Last.fm/Discogs/Bandcamp/Genius payload the workers collect had nowhere to go
and the mirror dropped it silently — invisible to the divergence metric too,
which can only audit what the declaration names.

Nezreka's bar is "i don't want to lose any enrichment functionality or data in
the move to v2", so this belongs before the legacy tables are deleted, not after.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.enrich import resync_entity_from_legacy
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.executescript(
        """
        CREATE TABLE albums(
            id INTEGER PRIMARY KEY, title TEXT, thumb_url TEXT, genres TEXT,
            label TEXT, explicit INTEGER, upc TEXT, style TEXT, mood TEXT,
            release_date TEXT,
            lastfm_listeners INTEGER, lastfm_playcount INTEGER, lastfm_tags TEXT,
            lastfm_wiki TEXT,
            discogs_genres TEXT, discogs_styles TEXT, discogs_label TEXT,
            discogs_catno TEXT, discogs_country TEXT, discogs_rating REAL,
            discogs_rating_count INTEGER,
            bandcamp_tags TEXT, bandcamp_label TEXT
        );
        CREATE TABLE tracks(
            id INTEGER PRIMARY KEY, title TEXT, bpm REAL, explicit INTEGER,
            genius_lyrics TEXT, copyright TEXT, style TEXT, mood TEXT,
            disc_number INTEGER,
            lastfm_listeners INTEGER, lastfm_playcount INTEGER, lastfm_tags TEXT,
            genius_description TEXT, genius_url TEXT,
            bandcamp_tags TEXT, bandcamp_label TEXT
        );
        """
    )
    c.commit()
    yield c
    c.close()


def _album(conn, **legacy):
    legacy_id = conn.execute(
        f"INSERT INTO albums(title, {', '.join(legacy)}) "
        f"VALUES('Album', {', '.join('?' for _ in legacy)})",
        tuple(legacy.values()),
    ).lastrowid
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('A','A')").lastrowid
    lib2_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,legacy_album_id) "
        "VALUES(?,'Album','album',?)", (artist_id, legacy_id)).lastrowid
    conn.commit()
    return lib2_id, legacy_id


def _track(conn, **legacy):
    legacy_id = conn.execute(
        f"INSERT INTO tracks(title, {', '.join(legacy)}) "
        f"VALUES('Track', {', '.join('?' for _ in legacy)})",
        tuple(legacy.values()),
    ).lastrowid
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('A','A')").lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Album','album')", (artist_id,)).lastrowid
    lib2_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,legacy_track_id) VALUES(?,'Track',?)",
        (album_id, legacy_id)).lastrowid
    conn.commit()
    return lib2_id, legacy_id


class TestTheColumnExists:
    def test_albums_and_tracks_have_an_enrichment_column(self, conn):
        for table in ("lib2_albums", "lib2_tracks"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "enrichment" in columns, table

    def test_an_existing_database_gains_it_on_the_next_start(self, conn):
        """Upgrades run the migration list, not the CREATE."""
        for table in ("lib2_albums", "lib2_tracks"):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN enrichment")
        conn.commit()

        ensure_library_v2_schema(conn)

        for table in ("lib2_albums", "lib2_tracks"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "enrichment" in columns, table


class TestAlbumPayload:
    def test_discogs_and_bandcamp_all_arrive(self, conn):
        lib2_id, legacy_id = _album(
            conn, discogs_genres='["Electronic"]', discogs_styles='["Trip Hop"]',
            discogs_label="Virgin", discogs_catno="CDV 2883",
            discogs_country="UK", discogs_rating=4.6, discogs_rating_count=1200,
            bandcamp_tags='["bristol"]', bandcamp_label="Self-released")

        assert resync_entity_from_legacy(conn, "album", lib2_id, legacy_id) is True

        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_albums WHERE id=?", (lib2_id,)
        ).fetchone()["enrichment"])
        assert payload["discogs"]["label"] == "Virgin"
        assert payload["discogs"]["catno"] == "CDV 2883"
        assert payload["discogs"]["rating"] == 4.6
        assert payload["discogs"]["genres"] == ["Electronic"]
        assert payload["bandcamp"] == {"tags": ["bristol"], "label": "Self-released"}

    def test_release_date_reaches_its_own_column(self, conn):
        lib2_id, legacy_id = _album(conn, release_date="1998-04-20")

        resync_entity_from_legacy(conn, "album", lib2_id, legacy_id)

        assert conn.execute(
            "SELECT release_date FROM lib2_albums WHERE id=?", (lib2_id,)
        ).fetchone()["release_date"] == "1998-04-20"

    def test_a_provider_that_wrote_nothing_leaves_no_empty_bucket(self, conn):
        lib2_id, legacy_id = _album(conn, discogs_label="Virgin")

        resync_entity_from_legacy(conn, "album", lib2_id, legacy_id)

        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_albums WHERE id=?", (lib2_id,)
        ).fetchone()["enrichment"])
        assert set(payload) == {"discogs"}

    def test_a_migrated_services_legacy_columns_no_longer_arrive(self, conn):
        """Last.fm's worker writes lib2 itself now. Mirroring its legacy columns
        on top would push a stale value over the fresh native one."""
        lib2_id, legacy_id = _album(
            conn, lastfm_listeners=90210, lastfm_wiki="A landmark record.",
            discogs_label="Virgin")

        resync_entity_from_legacy(conn, "album", lib2_id, legacy_id)

        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_albums WHERE id=?", (lib2_id,)
        ).fetchone()["enrichment"])
        assert set(payload) == {"discogs"}


class TestTrackPayload:
    def test_genius_and_bandcamp_all_arrive(self, conn):
        lib2_id, legacy_id = _track(
            conn, genius_description="About the song.", genius_url="http://genius/x",
            bandcamp_tags='["b"]', bandcamp_label="Label")

        resync_entity_from_legacy(conn, "track", lib2_id, legacy_id)

        payload = json.loads(conn.execute(
            "SELECT enrichment FROM lib2_tracks WHERE id=?", (lib2_id,)
        ).fetchone()["enrichment"])
        assert payload["genius"] == {
            "description": "About the song.", "url": "http://genius/x"}
        assert payload["bandcamp"] == {"tags": ["b"], "label": "Label"}

    def test_disc_number_reaches_its_own_column(self, conn):
        lib2_id, legacy_id = _track(conn, disc_number=2)

        resync_entity_from_legacy(conn, "track", lib2_id, legacy_id)

        assert conn.execute(
            "SELECT disc_number FROM lib2_tracks WHERE id=?", (lib2_id,)
        ).fetchone()["disc_number"] == 2


class TestTheAuditSeesTheNewFields:
    def test_an_unmirrored_album_payload_is_a_divergence(self, conn):
        """The whole point of declaring them: the metric can now see them."""
        from core.library2.integrity_reconciler import _Collector, _mirror_divergence_findings

        lib2_id, legacy_id = _album(conn, discogs_label="Virgin")
        resync_entity_from_legacy(conn, "album", lib2_id, legacy_id)
        conn.execute("UPDATE albums SET discogs_label='4AD' WHERE id=?", (legacy_id,))
        conn.commit()

        collector = _Collector(10)
        _mirror_divergence_findings(collector, conn)

        finding = next(item for item in collector.items
                       if item.code == "lib2_mirror_divergence")
        assert finding.details["fields"] == ["enrichment.discogs.label"]
