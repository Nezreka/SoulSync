"""What the mirror does with a field whose worker has moved to lib2.

Dropping the field from the mirror outright — what the first three conversions
did — closes the stale-overwrite hazard but opens a quieter one. Take a legacy row
where AudioDB wrote ``style='trip hop'`` two years ago, whose lib2 twin never
received it, and whose attempt ledger says ``matched`` so the worker will not
re-fetch. Drop ``style`` from the mirror and that value is simply gone from lib2,
with nothing left that would ever carry it across. Nezreka's bar for this whole
move is "i don't want to lose any enrichment functionality or data", so that is
not an acceptable trade.

Backfill is the resolution, and it satisfies both sides at once: a migrated
service's fields still cross, but only into a lib2 field that is empty. A fresh
native write is never overwritten (the hazard), and a legacy value lib2 never got
still arrives (the loss). It also keeps the divergence metric honest — the only
disagreement it can now report for such a field is "lib2 empty, legacy has data",
which the sweep can actually fix, so the expected value of 0 stays reachable.

The second half is which columns count. Prefix matching finds ``genius_lyrics``,
but AudioDB's fields are ``style``, ``mood``, ``label`` and ``banner_url`` — shared
column names that carry no service in them, while AudioDB is in fact their only
legacy writer. Those have to be declared.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.enrich import (
    MIRROR_SPECS, mirror_divergence, resync_entity_from_legacy,
)
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.executescript(
        """
        CREATE TABLE artists(
            id TEXT PRIMARY KEY, name TEXT, thumb_url TEXT, genres TEXT,
            summary TEXT, style TEXT, mood TEXT, label TEXT, aliases TEXT,
            banner_url TEXT, audiodb_id TEXT, spotify_artist_id TEXT,
            musicbrainz_id TEXT, deezer_id TEXT,
            lastfm_bio TEXT, lastfm_listeners INTEGER, lastfm_playcount INTEGER,
            lastfm_tags TEXT, lastfm_similar TEXT, lastfm_url TEXT,
            genius_description TEXT, genius_alt_names TEXT, genius_url TEXT,
            genius_id TEXT, discogs_bio TEXT, discogs_members TEXT,
            discogs_urls TEXT, discogs_id TEXT
        );
        CREATE TABLE tracks(
            id INTEGER PRIMARY KEY, title TEXT, bpm REAL, explicit INTEGER,
            genius_lyrics TEXT, copyright TEXT, style TEXT, mood TEXT,
            disc_number INTEGER, spotify_track_id TEXT,
            musicbrainz_recording_id TEXT, isrc TEXT, genius_id TEXT,
            lastfm_listeners INTEGER, lastfm_playcount INTEGER, lastfm_tags TEXT,
            genius_description TEXT, genius_url TEXT, bandcamp_tags TEXT,
            bandcamp_label TEXT, bandcamp_url TEXT
        );
        """
    )
    c.commit()
    yield c
    c.close()


def _artist(conn, *, lib2=None, **legacy):
    # artists.id is a TEXT primary key holding a media-server ratingKey, so it has
    # to be supplied — an INSERT that omits it leaves it NULL, not auto-numbered.
    legacy_id = str(conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0] + 1)
    conn.execute(
        f"INSERT INTO artists(id, name, {', '.join(legacy)}) "
        f"VALUES(?, 'A', {', '.join('?' for _ in legacy)})",
        (legacy_id, *legacy.values()),
    )
    lib2_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, legacy_artist_id) "
        "VALUES('A','A',?)", (legacy_id,)).lastrowid
    for column, value in (lib2 or {}).items():
        conn.execute(f"UPDATE lib2_artists SET {column}=? WHERE id=?",
                     (value, lib2_id))
    conn.commit()
    return lib2_id, legacy_id


def _track(conn, *, lib2=None, **legacy):
    legacy_id = conn.execute(
        f"INSERT INTO tracks(title, {', '.join(legacy)}) "
        f"VALUES('T', {', '.join('?' for _ in legacy)})",
        tuple(legacy.values()),
    ).lastrowid
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('A','A')").lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Al','album')", (artist_id,)).lastrowid
    lib2_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,legacy_track_id) VALUES(?,'T',?)",
        (album_id, legacy_id)).lastrowid
    for column, value in (lib2 or {}).items():
        conn.execute(f"UPDATE lib2_tracks SET {column}=? WHERE id=?",
                     (value, lib2_id))
    conn.commit()
    return lib2_id, legacy_id


def _row(conn, table, entity_id):
    return conn.execute(
        f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()


@pytest.fixture
def nothing_migrated(monkeypatch):
    """The mirror before any handover, so the overwrite path stays under test.

    Needed because every provider the mirror knows about has since migrated; without
    this the overwrite branch would have no live example and would quietly stop being
    exercised.
    """
    import core.library2.enrich as enrich

    monkeypatch.setattr(enrich, "MIGRATED_SERVICES", frozenset())


@pytest.fixture
def audiodb_has_migrated(monkeypatch):
    """Declares AudioDB migrated for the duration of a test.

    The shared-column cases are about whether ``_SCALAR_OWNERS`` is consulted at
    all, which has to hold whatever order the workers happen to move in. Waiting
    for AudioDB's own conversion to make them pass would tie a mechanism test to a
    migration schedule.
    """
    import core.library2.enrich as enrich

    monkeypatch.setattr(
        enrich, "MIGRATED_SERVICES", enrich.MIGRATED_SERVICES | {"audiodb"})


class TestAMigratedServicesScalarColumn:
    def test_it_fills_an_empty_lib2_field(self, conn):
        """The loss half: a value legacy has had for years and lib2 never got."""
        lib2_id, legacy_id = _track(conn, genius_lyrics="the old words")

        resync_entity_from_legacy(conn, "track", lib2_id, legacy_id)

        assert _row(conn, "lib2_tracks", lib2_id)["genius_lyrics"] == "the old words"

    def test_it_never_overwrites_what_lib2_already_has(self, conn):
        """The hazard half: the worker's own fresh write must survive the drain."""
        lib2_id, legacy_id = _track(
            conn, genius_lyrics="stale words", lib2={"genius_lyrics": "fresh words"})

        resync_entity_from_legacy(conn, "track", lib2_id, legacy_id)

        assert _row(conn, "lib2_tracks", lib2_id)["genius_lyrics"] == "fresh words"

    def test_a_shared_column_name_counts_too(self, conn, audiodb_has_migrated):
        """AudioDB's fields are style/mood/label/banner_url — no service in the
        name, and AudioDB is their only legacy writer, so prefix matching cannot
        find them and they have to be declared."""
        lib2_id, legacy_id = _track(
            conn, style="trip hop", mood="brooding",
            lib2={"style": "downtempo"})

        resync_entity_from_legacy(conn, "track", lib2_id, legacy_id)

        row = _row(conn, "lib2_tracks", lib2_id)
        assert row["style"] == "downtempo", "a native write is not overwritten"
        assert row["mood"] == "brooding", "an empty field is still filled"

    def test_an_empty_json_array_counts_as_a_gap(self, conn):
        """``aliases`` and ``genres`` default to ``'[]'``, not NULL or ''. A backfill
        that only treated NULL as empty would never fire for them — the column is
        never NULL — so the legacy value would be silently unreachable, which is the
        loss this whole mechanism exists to prevent."""
        lib2_id, legacy_id = _artist(
            conn, aliases='["\u6fa4\u91ce\u5f18\u4e4b"]')

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        assert json.loads(_row(conn, "lib2_artists", lib2_id)["aliases"]) == [
            "\u6fa4\u91ce\u5f18\u4e4b"]

    def test_an_unmigrated_services_column_still_overwrites(self, conn,
                                                            nothing_migrated):
        """Legacy remains authoritative wherever legacy is still the only writer —
        otherwise a corrected value could never reach lib2."""
        lib2_id, legacy_id = _artist(
            conn, summary="the corrected bio", lib2={"summary": "the old bio"})

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        assert _row(conn, "lib2_artists", lib2_id)["summary"] == "the corrected bio"


class TestAMigratedServicesJsonPayload:
    def test_it_fills_a_bucket_lib2_does_not_have(self, conn):
        lib2_id, legacy_id = _artist(conn, lastfm_bio="an old bio",
                                     lastfm_listeners=90210)

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        payload = json.loads(_row(conn, "lib2_artists", lib2_id)["enrichment"])
        assert payload["lastfm"]["bio"] == "an old bio"
        assert payload["lastfm"]["listeners"] == 90210

    def test_it_leaves_a_key_the_worker_already_wrote(self, conn):
        lib2_id, legacy_id = _artist(
            conn, lastfm_bio="stale", lastfm_listeners=1,
            lib2={"enrichment": json.dumps({"lastfm": {"bio": "fresh"}})})

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        payload = json.loads(_row(conn, "lib2_artists", lib2_id)["enrichment"])
        assert payload["lastfm"]["bio"] == "fresh"
        assert payload["lastfm"]["listeners"] == 1, "a key lib2 lacks still arrives"

    def test_a_migrated_provider_id_fills_but_does_not_replace(self, conn):
        lib2_id, legacy_id = _artist(
            conn, genius_id="111", discogs_id="222",
            lib2={"external_ids": json.dumps({"genius": "999"})})

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        ids = json.loads(_row(conn, "lib2_artists", lib2_id)["external_ids"])
        assert ids["genius"] == "999", "the worker's own id wins"
        assert ids["discogs"] == "222", "an id lib2 lacks still arrives"

    def test_a_promoted_id_column_is_backfill_only_too(self, conn):
        """The third place the handover has to reach. lib2 stores Spotify and
        MusicBrainz twice — a promoted column the read paths join on, plus
        external_ids — and the column was still being mirrored outright, so the drain
        would have pushed a stale legacy id over the worker's fresh one."""
        lib2_id, legacy_id = _artist(
            conn, spotify_artist_id="sp-stale",
            lib2={"spotify_id": "sp-fresh"})

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        assert _row(conn, "lib2_artists", lib2_id)["spotify_id"] == "sp-fresh"

    def test_an_empty_promoted_id_column_is_still_filled(self, conn):
        lib2_id, legacy_id = _artist(conn, spotify_artist_id="sp-1")

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        assert _row(conn, "lib2_artists", lib2_id)["spotify_id"] == "sp-1"

    def test_an_unmigrated_provider_id_still_replaces(self, conn, nothing_migrated):
        """Legacy stays authoritative wherever legacy is still the only writer, or a
        corrected provider id could never reach lib2.

        Every provider in ``match_status.SERVICES`` has now migrated, so there is no
        live example left to point at — the fixture puts the mirror back in its
        pre-handover state to keep the overwrite path under test for whatever is
        declared next.
        """
        lib2_id, legacy_id = _artist(
            conn, deezer_id="dz-new",
            lib2={"external_ids": json.dumps({"deezer": "dz-old"})})

        resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)

        ids = json.loads(_row(conn, "lib2_artists", lib2_id)["external_ids"])
        assert ids["deezer"] == "dz-new"


class TestTheMetricStaysReachable:
    def test_a_field_the_worker_owns_is_not_reported_as_divergent(
            self, conn, audiodb_has_migrated):
        """Otherwise the sweep reports a disagreement it deliberately will not
        fix, and a metric whose expected value of 0 is unreachable measures
        nothing."""
        lib2_id, legacy_id = _track(
            conn, genius_lyrics="stale", style="trip hop",
            lib2={"genius_lyrics": "fresh", "style": "downtempo"})

        fields = mirror_divergence(
            MIRROR_SPECS["track"],
            _row(conn, "tracks", legacy_id), _row(conn, "lib2_tracks", lib2_id))

        assert "genius_lyrics" not in fields
        assert "style" not in fields

    def test_an_empty_lib2_field_is_still_reported(self, conn):
        """This one the sweep can fix, so it belongs in the metric."""
        lib2_id, legacy_id = _track(conn, genius_lyrics="the old words")

        fields = mirror_divergence(
            MIRROR_SPECS["track"],
            _row(conn, "tracks", legacy_id), _row(conn, "lib2_tracks", lib2_id))

        assert fields["genius_lyrics"]["legacy"] == "the old words"

    def test_a_promoted_id_column_the_worker_owns_is_not_reported(self, conn):
        lib2_id, legacy_id = _artist(
            conn, spotify_artist_id="sp-stale", lib2={"spotify_id": "sp-fresh"})

        fields = mirror_divergence(
            MIRROR_SPECS["artist"],
            _row(conn, "artists", legacy_id), _row(conn, "lib2_artists", lib2_id))

        assert "spotify_id" not in fields
        # external_ids IS still reported here, and correctly: lib2's JSON is empty,
        # so that half is a gap the sweep can fill rather than a value it would
        # overwrite.
        assert fields["external_ids.spotify"]["lib2"] is None

    def test_a_migrated_payload_key_lib2_already_has_is_not_reported(self, conn):
        lib2_id, legacy_id = _artist(
            conn, lastfm_bio="stale",
            lib2={"enrichment": json.dumps({"lastfm": {"bio": "fresh"}})})

        fields = mirror_divergence(
            MIRROR_SPECS["artist"],
            _row(conn, "artists", legacy_id), _row(conn, "lib2_artists", lib2_id))

        assert fields == {}


def test_every_declared_scalar_owner_is_a_real_column():
    """A typo would silently exempt nothing, leaving the hazard in place."""
    from core.library2.enrich import _SCALAR_OWNERS

    for entity_type, owners in _SCALAR_OWNERS.items():
        spec = MIRROR_SPECS[entity_type]
        columns = {field[0] for field in spec.scalars}
        unknown = set(owners) - columns
        assert not unknown, (entity_type, sorted(unknown))
