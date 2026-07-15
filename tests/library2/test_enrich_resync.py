"""docs/library-v2.md §44 — Enrich only re-queries providers into the LEGACY
row (see web_server.py's ``_run_single_enrichment``); lib2 rows are a
point-in-time mirror, so without a resync step the refreshed data would stay
invisible in the lib2 UI until a full re-import.
``core.library2.enrich.resync_entity_from_legacy`` closes that gap for one
entity right after an Enrich call.
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
        CREATE TABLE artists(
            id INTEGER PRIMARY KEY, name TEXT, thumb_url TEXT, genres TEXT,
            summary TEXT, style TEXT, mood TEXT, label TEXT, banner_url TEXT
        );
        CREATE TABLE albums(
            id INTEGER PRIMARY KEY, title TEXT, thumb_url TEXT, genres TEXT,
            label TEXT, explicit INTEGER, upc TEXT
        );
        CREATE TABLE tracks(
            id INTEGER PRIMARY KEY, title TEXT, bpm REAL, explicit INTEGER,
            genius_lyrics TEXT, copyright TEXT
        );
        """
    )
    c.commit()
    yield c
    c.close()


def test_resync_artist_overwrites_from_legacy_row(conn):
    conn.execute(
        "INSERT INTO lib2_artists(name, genres, summary, style, label, legacy_artist_id) "
        "VALUES('A', '[\"old\"]', 'old bio', 'old style', 'old label', 501)"
    )
    lib2_id = conn.execute("SELECT id FROM lib2_artists").fetchone()["id"]
    conn.execute(
        "INSERT INTO artists(id, name, thumb_url, genres, summary, style, mood, label, banner_url) "
        "VALUES(501, 'A', 'http://img', '[\"synthwave\",\"phonk\"]', 'fresh bio', "
        "'fresh style', 'moody', 'fresh label', 'http://banner')"
    )
    conn.commit()

    assert resync_entity_from_legacy(conn, "artist", lib2_id, 501) is True

    row = conn.execute(
        "SELECT image_url, genres, summary, style, mood, label, banner_url "
        "FROM lib2_artists WHERE id=?", (lib2_id,)
    ).fetchone()
    assert row["image_url"] == "http://img"
    assert json.loads(row["genres"]) == ["synthwave", "phonk"]
    assert row["summary"] == "fresh bio"
    assert row["style"] == "fresh style"
    assert row["mood"] == "moody"
    assert row["label"] == "fresh label"
    assert row["banner_url"] == "http://banner"


def test_resync_artist_never_clobbers_with_untouched_null_column(conn):
    """A provider that only touches ONE column (e.g. a bio-only Last.fm
    lookup) must not blank out other fields the legacy row never had set."""
    conn.execute(
        "INSERT INTO lib2_artists(name, genres, label, legacy_artist_id) "
        "VALUES('A', '[\"rap\"]', 'Keep This Label', 502)"
    )
    lib2_id = conn.execute("SELECT id FROM lib2_artists").fetchone()["id"]
    conn.execute(
        "INSERT INTO artists(id, name, genres, label) VALUES(502, 'A', NULL, NULL)"
    )
    conn.commit()

    resync_entity_from_legacy(conn, "artist", lib2_id, 502)

    row = conn.execute(
        "SELECT genres, label FROM lib2_artists WHERE id=?", (lib2_id,)
    ).fetchone()
    assert json.loads(row["genres"]) == ["rap"]
    assert row["label"] == "Keep This Label"


def test_resync_album_overwrites_from_legacy_row(conn):
    conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('A')"
    )
    artist_id = conn.execute("SELECT id FROM lib2_artists").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Views', 601)", (artist_id,)
    )
    lib2_id = conn.execute("SELECT id FROM lib2_albums").fetchone()["id"]
    conn.execute(
        "INSERT INTO albums(id, title, thumb_url, genres, label, explicit, upc) "
        "VALUES(601, 'Views', 'http://cover', '[\"rap\"]', 'OVO Sound', 1, '123456789012')"
    )
    conn.commit()

    assert resync_entity_from_legacy(conn, "album", lib2_id, 601) is True

    row = conn.execute(
        "SELECT image_url, genres, label, explicit, upc FROM lib2_albums WHERE id=?",
        (lib2_id,),
    ).fetchone()
    assert row["image_url"] == "http://cover"
    assert json.loads(row["genres"]) == ["rap"]
    assert row["label"] == "OVO Sound"
    assert row["explicit"] == 1
    assert row["upc"] == "123456789012"


def test_resync_track_overwrites_from_legacy_row(conn):
    conn.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    artist_id = conn.execute("SELECT id FROM lib2_artists").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Views')",
        (artist_id,),
    )
    album_id = conn.execute("SELECT id FROM lib2_albums").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, legacy_track_id) "
        "VALUES(?, 'One Dance', 701)", (album_id,)
    )
    lib2_id = conn.execute("SELECT id FROM lib2_tracks").fetchone()["id"]
    conn.execute(
        "INSERT INTO tracks(id, title, bpm, explicit, genius_lyrics, copyright) "
        "VALUES(701, 'One Dance', 104.0, 0, 'some lyrics', '(C) 2016 OVO')"
    )
    conn.commit()

    assert resync_entity_from_legacy(conn, "track", lib2_id, 701) is True

    row = conn.execute(
        "SELECT bpm, explicit, genius_lyrics, copyright FROM lib2_tracks WHERE id=?",
        (lib2_id,),
    ).fetchone()
    assert row["bpm"] == 104.0
    assert row["explicit"] == 0
    assert row["genius_lyrics"] == "some lyrics"
    assert row["copyright"] == "(C) 2016 OVO"


def test_resync_returns_false_when_legacy_row_is_gone(conn):
    conn.execute("INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('A', 999)")
    lib2_id = conn.execute("SELECT id FROM lib2_artists").fetchone()["id"]
    conn.commit()

    assert resync_entity_from_legacy(conn, "artist", lib2_id, 999) is False


def test_resync_returns_false_for_unknown_entity_type(conn):
    assert resync_entity_from_legacy(conn, "playlist", 1, 1) is False
