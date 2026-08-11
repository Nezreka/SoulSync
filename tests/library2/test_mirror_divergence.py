"""iss32-T01a — divergence between legacy and lib2 as a *metric*.

docs/library-v2-issues.md §32.3.1 makes three promises that are what keeps the
two table worlds safe while both exist.  Promise 2: the read-only integrity
report carries a divergence figure over the mirrored fields, expected value 0,
and any other value is a bug with row numbers attached.  The report had a
divergence check for the file indices only; the enrichment fields the mirror
copies (``core.library2.enrich``) were never compared.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.enrich import resync_entity_from_legacy
from core.library2.integrity_reconciler import build_integrity_report
from core.library2.legacy_mirror import ensure_legacy_mirror_schema
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.executescript(
        """
        CREATE TABLE artists(
            id INTEGER PRIMARY KEY, name TEXT, thumb_url TEXT, genres TEXT,
            summary TEXT, style TEXT, mood TEXT, label TEXT, banner_url TEXT,
            aliases TEXT, spotify_artist_id TEXT, musicbrainz_id TEXT,
            lastfm_bio TEXT, lastfm_listeners INTEGER, lastfm_tags TEXT,
            discogs_bio TEXT
        );
        CREATE TABLE albums(
            id INTEGER PRIMARY KEY, title TEXT, thumb_url TEXT, genres TEXT,
            label TEXT, explicit INTEGER, upc TEXT, style TEXT, mood TEXT,
            spotify_album_id TEXT, musicbrainz_release_id TEXT
        );
        CREATE TABLE tracks(
            id INTEGER PRIMARY KEY, title TEXT, file_path TEXT, bpm REAL,
            explicit INTEGER, genius_lyrics TEXT, copyright TEXT, style TEXT,
            mood TEXT, isrc TEXT, spotify_track_id TEXT,
            musicbrainz_recording_id TEXT
        );
        """
    )
    ensure_legacy_mirror_schema(c.cursor())
    c.commit()
    yield c
    c.close()


def _mirrored_artist(conn, **legacy):
    """One legacy artist and its lib2 twin, brought in step by the mirror."""
    columns = ", ".join(legacy)
    holes = ", ".join("?" for _ in legacy)
    legacy_id = conn.execute(
        f"INSERT INTO artists(name, {columns}) VALUES('Artist', {holes})",
        tuple(legacy.values()),
    ).lastrowid
    lib2_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, legacy_artist_id) "
        "VALUES('Artist','Artist',?)",
        (legacy_id,),
    ).lastrowid
    resync_entity_from_legacy(conn, "artist", lib2_id, legacy_id)
    conn.execute("DELETE FROM lib2_legacy_dirty")
    conn.commit()
    return lib2_id, legacy_id


def _divergences(report):
    return [
        finding for finding in report.findings
        if finding.code == "lib2_mirror_divergence"
    ]


def test_mirrored_rows_in_step_count_zero(conn):
    _mirrored_artist(conn, thumb_url="http://img", genres='["phonk"]',
                     summary="bio", spotify_artist_id="sp-1")
    before = conn.total_changes

    report = build_integrity_report(conn)

    assert conn.total_changes == before
    assert report.counts.get("lib2_mirror_divergence", 0) == 0
    assert report.observed["mirror_checked"] == 1
    assert report.coverage["mirror_divergence"] is True


def test_unmirrored_legacy_enrichment_is_reported_with_row_ids(conn):
    lib2_id, legacy_id = _mirrored_artist(conn, summary="old bio")
    # An enrichment worker writes legacy directly; the queue drain has not run.
    conn.execute("UPDATE artists SET summary='fresh bio', style='synth' WHERE id=?",
                 (legacy_id,))
    conn.execute("DELETE FROM lib2_legacy_dirty")
    conn.commit()

    report = build_integrity_report(conn)

    finding = next(iter(_divergences(report)))
    assert finding.severity == "error"
    assert finding.component == "mirror_divergence"
    assert finding.entity == str(lib2_id)
    assert finding.details["legacy_id"] == legacy_id
    assert finding.details["entity_type"] == "artist"
    assert finding.details["fields"] == ["style", "summary"]
    assert finding.details["values"]["summary"] == {
        "legacy": "fresh bio", "lib2": "old bio",
    }
    assert report.counts["lib2_mirror_divergence"] == 1


def test_queued_row_is_pending_not_divergent(conn):
    """A row the trigger has already queued is work in flight, not a bug —
    counting it would make the metric flicker under normal operation and cost
    it the one property that makes it useful: expected value 0."""
    _, legacy_id = _mirrored_artist(conn, summary="old bio")
    conn.execute("UPDATE artists SET summary='fresh bio' WHERE id=?", (legacy_id,))
    conn.commit()

    report = build_integrity_report(conn)

    assert conn.execute("SELECT COUNT(*) FROM lib2_legacy_dirty").fetchone()[0] == 1
    assert _divergences(report) == []
    assert report.observed["mirror_pending"] == 1
    assert report.observed["mirror_checked"] == 0


def test_provider_ids_and_bios_diverge_per_key(conn):
    """Last.fm is deliberately absent here: its worker writes lib2 directly now,
    so it left the mirror (``enrich.MIGRATED_SERVICES``) and a legacy Last.fm
    column is no longer something lib2 is expected to match."""
    _, legacy_id = _mirrored_artist(conn, spotify_artist_id="sp-1",
                                    discogs_bio="old bio")
    conn.execute(
        "UPDATE artists SET spotify_artist_id='sp-2', discogs_bio='new bio' WHERE id=?",
        (legacy_id,),
    )
    conn.execute("DELETE FROM lib2_legacy_dirty")
    conn.commit()

    report = build_integrity_report(conn)

    finding = next(iter(_divergences(report)))
    assert finding.details["fields"] == [
        "enrichment.discogs.bio", "external_ids.spotify", "spotify_id",
    ]


def test_native_row_without_legacy_link_is_not_compared(conn):
    """Natively created entities have no legacy twin.  Comparing them would
    report every one of them as divergent forever."""
    conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, summary) "
        "VALUES('Native','Native','native bio')"
    )
    conn.commit()

    report = build_integrity_report(conn)

    assert _divergences(report) == []
    assert report.observed["mirror_checked"] == 0


def test_missing_legacy_row_is_a_dangling_link_not_a_divergence(conn):
    """A media-server rescan can change a ratingKey, which is the legacy
    primary key — the old row disappears and the mirror can never update that
    lib2 row again.  Worth reporting, but it is not a field disagreement."""
    lib2_id, legacy_id = _mirrored_artist(conn, summary="bio")
    conn.execute("DELETE FROM artists WHERE id=?", (legacy_id,))
    conn.commit()

    report = build_integrity_report(conn)

    assert _divergences(report) == []
    finding = next(
        item for item in report.findings
        if item.code == "lib2_mirror_legacy_row_missing"
    )
    assert finding.entity == str(lib2_id)
    assert finding.details["legacy_id"] == legacy_id


def test_track_and_album_fields_are_compared_too(conn):
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('A','A')").lastrowid
    legacy_album = conn.execute(
        "INSERT INTO albums(title, label) VALUES('Album','old label')").lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,legacy_album_id) "
        "VALUES(?,'Album','album',?)", (artist_id, legacy_album)).lastrowid
    legacy_track = conn.execute(
        "INSERT INTO tracks(title, bpm) VALUES('Track', 120.0)").lastrowid
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,legacy_track_id) VALUES(?,'Track',?)",
        (album_id, legacy_track)).lastrowid
    resync_entity_from_legacy(conn, "album", album_id, legacy_album)
    resync_entity_from_legacy(conn, "track", track_id, legacy_track)
    conn.execute("UPDATE albums SET label='new label' WHERE id=?", (legacy_album,))
    conn.execute("UPDATE tracks SET bpm=128.0 WHERE id=?", (legacy_track,))
    conn.execute("DELETE FROM lib2_legacy_dirty")
    conn.commit()

    report = build_integrity_report(conn)

    by_entity = {
        (item.details["entity_type"], item.details["fields"][0])
        for item in _divergences(report)
    }
    assert by_entity == {("album", "label"), ("track", "bpm")}
    assert report.observed["mirror_checked"] == 2


def test_dropped_legacy_tables_disable_the_check(conn):
    """docs §32.3.1 Stufe 4 drops the legacy tables.  The auditor has to go
    quiet on its own then, not raise."""
    _mirrored_artist(conn, summary="bio")
    conn.executescript("DROP TABLE artists; DROP TABLE albums; DROP TABLE tracks;")
    conn.commit()

    report = build_integrity_report(conn)

    assert _divergences(report) == []
    assert report.coverage["mirror_divergence"] is False


def test_unmirrorable_legacy_null_is_not_divergence(conn):
    """The mirror is COALESCE-based: a legacy column no provider ever filled
    must not blank the lib2 value, so lib2 keeping a richer value is the
    contract, not a disagreement."""
    lib2_id, _ = _mirrored_artist(conn, summary="bio")
    conn.execute("UPDATE lib2_artists SET label='set by lib2' WHERE id=?", (lib2_id,))
    conn.commit()

    report = build_integrity_report(conn)

    assert _divergences(report) == []


@pytest.fixture
def text_id_conn(tmp_path):
    """The production legacy schema: ``artists.id`` is ``TEXT PRIMARY KEY``
    holding a media-server ratingKey, while ``lib2_artists.legacy_artist_id``
    is ``INTEGER``.  SQLite matches the two through column affinity; a Python
    dict keyed on the raw value does not."""
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.executescript(
        """
        CREATE TABLE artists(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, thumb_url TEXT,
            genres TEXT, summary TEXT, style TEXT, mood TEXT, label TEXT,
            banner_url TEXT, aliases TEXT, spotify_artist_id TEXT,
            musicbrainz_id TEXT
        );
        """
    )
    ensure_legacy_mirror_schema(c.cursor())
    c.execute("INSERT INTO artists(id, name, summary) VALUES('436328401','V','bio')")
    c.execute(
        "INSERT INTO lib2_artists(name, sort_name, summary, legacy_artist_id) "
        "VALUES('V','V','bio',436328401)"
    )
    c.commit()
    yield c
    c.close()


def test_text_legacy_id_matches_its_integer_lib2_link(text_id_conn):
    text_id_conn.execute("UPDATE artists SET summary='fresh bio' WHERE id='436328401'")
    text_id_conn.execute("DELETE FROM lib2_legacy_dirty")
    text_id_conn.commit()

    report = build_integrity_report(text_id_conn)

    assert [item.code for item in report.findings if item.component == "mirror_divergence"] == [
        "lib2_mirror_divergence"
    ]
    assert report.observed["mirror_checked"] == 1
    assert report.observed["mirror_dangling"] == 0


def test_queued_text_legacy_id_is_pending(text_id_conn):
    text_id_conn.execute("UPDATE artists SET summary='fresh bio' WHERE id='436328401'")
    text_id_conn.commit()

    report = build_integrity_report(text_id_conn)

    assert _divergences(report) == []
    assert report.observed["mirror_pending"] == 1


def test_json_genres_shape_difference_is_not_divergence(conn):
    """Legacy stores genres as a comma string or a JSON array; lib2 always
    normalizes.  Comparing the raw text would report every single row."""
    _, legacy_id = _mirrored_artist(conn, genres="rock, pop")
    conn.execute("UPDATE artists SET genres='rock,pop' WHERE id=?", (legacy_id,))
    conn.execute("DELETE FROM lib2_legacy_dirty")
    conn.commit()

    stored = conn.execute("SELECT genres FROM lib2_artists").fetchone()["genres"]
    assert json.loads(stored) == ["rock", "pop"]
    assert _divergences(build_integrity_report(conn)) == []
