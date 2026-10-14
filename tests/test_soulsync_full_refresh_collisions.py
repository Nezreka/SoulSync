"""Regression tests for #1696 — standalone Full Refresh silently dropped
tracks whose `_stable_id` (md5 % 1e9) collided with another file's id:
the INSERT OR IGNORE was ignored, and the old code counted it as a
success anyway. Same hazard for artist/album ids (names differing only
by case hash to the same lowercased id).

The fix follows the import path's C4 precedent
(core/imports/side_effects.py): remint a deterministic suffixed id on
collision, log it, and only count rows that actually landed.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

import api.database_admin as da
from core.soulsync_client import _stable_id


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT, server_source TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE albums (id TEXT PRIMARY KEY,"
        " artist_id TEXT REFERENCES artists(id), title TEXT,"
        " year INTEGER, track_count INTEGER, server_source TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE tracks (id TEXT PRIMARY KEY,"
        " album_id TEXT REFERENCES albums(id),"
        " artist_id TEXT REFERENCES artists(id),"
        " title TEXT, track_number INTEGER, disc_number INTEGER, duration INTEGER,"
        " file_path TEXT, bitrate INTEGER, year INTEGER, server_source TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )

    class _DB:
        def __init__(self):
            self.cleared = []

        def clear_server_data(self, source):
            self.cleared.append(source)

        def _get_connection(self):
            return conn

    return conn, _DB()


def _tags(title, artist, album, year="2020", mbid="", comment=""):
    return {
        "title": title, "artist": artist, "album_artist": artist, "album": album,
        "track_number": 1, "disc_number": 1, "year": year, "genre": "",
        "duration_ms": 1000, "bitrate": 320,
        "musicbrainz_albumid": mbid, "musicbrainz_albumcomment": comment,
    }


@pytest.fixture()
def refresh(monkeypatch, tmp_path):
    """Run _run_soulsync_full_refresh against a fake transfer tree.

    `files` maps a relative file name to its fake tag dict. Returns the
    (connection, finished-callback calls) pair.
    """
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    conn, db = _make_db()
    finished = []
    summaries = []

    monkeypatch.setattr(da, "config_manager",
                        SimpleNamespace(get=lambda key, default=None: str(transfer)))
    monkeypatch.setattr(da, "docker_resolve_path", lambda p: p)
    monkeypatch.setattr(da, "get_database", lambda: db)
    monkeypatch.setattr(da, "_db_update_phase_callback", lambda *a: None)
    monkeypatch.setattr(da, "_db_update_error_callback",
                        lambda msg: (_ for _ in ()).throw(AssertionError(msg)))
    monkeypatch.setattr(da, "_db_update_finished_callback",
                        lambda *a: finished.append(a))
    monkeypatch.setattr(da, "add_activity_item",
                        lambda *a: summaries.append(a[2] if len(a) > 2 else ""))

    def run(files, preseed=None):
        if preseed is not None:
            preseed(conn)
        tags_by_path = {}
        for name, tags in files.items():
            path = transfer / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            tags_by_path[str(path)] = tags
        monkeypatch.setattr("core.soulsync_client._read_tags",
                            lambda p: tags_by_path[p])
        da._run_soulsync_full_refresh()
        return conn, finished, summaries

    return run


def _colliding_pair(transfer_dir):
    """Find two distinct file paths under transfer_dir whose _stable_id
    collides — expected after ~40k candidates by the birthday bound."""
    seen = {}
    for i in range(400_000):
        path = os.path.join(transfer_dir, f"track{i}.flac")
        sid = _stable_id(path)
        if sid in seen:
            return seen[sid], path
        seen[sid] = path
    raise AssertionError("no collision found — birthday paradox took a day off")


def test_reporters_known_pair_collides_at_the_helper_level():
    a = "/app/Transfer/Demo Artist 19610/Demo Album (2020)/01 - Demo Track 19610.flac"
    b = "/app/Transfer/Demo Artist 23487/Demo Album (2020)/01 - Demo Track 23487.flac"
    assert _stable_id(a) == _stable_id(b) == "463751062"


def test_colliding_track_files_both_land(refresh, tmp_path):
    transfer = str(tmp_path / "Transfer")
    first, second = _colliding_pair(transfer)
    assert _stable_id(first) == _stable_id(second)
    base_id = _stable_id(first) + "::soulsync"

    conn, finished, summaries = refresh({
        os.path.basename(first): _tags("First", "Artist", "Album"),
        os.path.basename(second): _tags("Second", "Artist", "Album"),
    })

    rows = conn.execute("SELECT id, file_path FROM tracks ORDER BY file_path").fetchall()
    assert len(rows) == 2
    assert {r["file_path"] for r in rows} == {first, second}
    assert rows[0]["id"] != rows[1]["id"]
    # the walk is sorted, so the file that sorts first keeps the plain
    # id and the other one is reminted — deterministically
    assert rows[0]["file_path"] == min(first, second)
    assert rows[0]["id"] == base_id
    # finished callback: (artists, albums, total, successful, failed)
    assert finished[-1][3] == 2
    assert finished[-1][4] == 0
    # exactly one row was reminted, and the summary says so
    assert any("1 rows reminted after id collisions" in s for s in summaries)


def test_case_differing_artists_and_albums_do_not_merge(refresh):
    conn, finished, summaries = refresh({
        "one.flac": _tags("One", "Demo Artist", "Demo Album"),
        "two.flac": _tags("Two", "demo artist", "demo album"),
    })

    artists = conn.execute("SELECT id, name FROM artists").fetchall()
    albums = conn.execute("SELECT id, title, track_count FROM albums").fetchall()
    tracks = conn.execute("SELECT album_id, file_path FROM tracks").fetchall()
    assert len(artists) == 2
    assert len(albums) == 2
    assert len(tracks) == 2
    # each track filed under its own album, and each album's stored
    # track_count describes its own rows
    assert {t["album_id"] for t in tracks} == {a["id"] for a in albums}
    assert all(a["track_count"] == 1 for a in albums)
    assert any("2 rows reminted after id collisions" in s for s in summaries)


def test_distinct_musicbrainz_releases_sharing_a_name_split(refresh):
    conn, finished, summaries = refresh({
        "std.flac": _tags("Std", "Artist", "Album", mbid="rel-1", comment="standard"),
        "dlx.flac": _tags("Dlx", "Artist", "Album", mbid="rel-2", comment="deluxe"),
    })

    albums = conn.execute("SELECT id, track_count FROM albums").fetchall()
    tracks = conn.execute("SELECT album_id FROM tracks").fetchall()
    assert len(albums) == 2
    assert {t["album_id"] for t in tracks} == {a["id"] for a in albums}
    assert all(a["track_count"] == 1 for a in albums)
    # finished callback reports the split: 1 artist, 2 albums
    assert finished[-1][0] == 1
    assert finished[-1][1] == 2


def test_same_album_tracks_share_one_row_with_true_count(refresh):
    conn, finished, summaries = refresh({
        "a.flac": _tags("A", "Artist", "Album", year="2020"),
        "b.flac": _tags("B", "Artist", "Album", year="2021"),
    })

    albums = conn.execute("SELECT track_count FROM albums").fetchall()
    assert len(albums) == 1
    assert albums[0]["track_count"] == 2
    assert finished[-1][3] == 2


# ---------------------------------------------------------------------------
# Remint chains and the release-suffix seed — driven by a fake _stable_id
# so the collisions are deterministic instead of brute-forced.
# ---------------------------------------------------------------------------

_REAL_STABLE_ID = _stable_id


def _fake_stable_id(exact=None, suffix=None):
    """Deterministic _stable_id stand-in: exact string matches first
    (album keys and remint seeds are tmp-path independent), then
    endswith matches (file paths), then the real hash."""
    exact = exact or {}
    suffix = suffix or {}

    def fake(text):
        if text in exact:
            return exact[text]
        for needle, value in suffix.items():
            if text.endswith(needle):
                return value
        return _REAL_STABLE_ID(text)
    return fake


def test_reminted_id_colliding_with_a_later_primary_remints_again(refresh, monkeypatch):
    """B collides with A and remints to 200; C's PRIMARY id is 200, so C
    must remint in turn — the chain resolves instead of C merging into B."""
    monkeypatch.setattr("core.soulsync_client._stable_id", _fake_stable_id(suffix={
        "b.flac::soulsync::1": "200",
        "c.flac::soulsync::1": "300",
        "b.flac": "100",
        "c.flac": "200",
        "a.flac": "100",
    }))

    conn, finished, summaries = refresh({
        "a.flac": _tags("A", "Artist", "Album"),
        "b.flac": _tags("B", "Artist", "Album"),
        "c.flac": _tags("C", "Artist", "Album"),
    })

    rows = conn.execute("SELECT id, file_path FROM tracks").fetchall()
    by_name = {os.path.basename(r["file_path"]): r["id"] for r in rows}
    assert by_name == {"a.flac": "100::soulsync", "b.flac": "200::soulsync",
                       "c.flac": "300::soulsync"}
    assert finished[-1][3] == 3
    assert finished[-1][4] == 0
    assert any("2 rows reminted after id collisions" in s for s in summaries)


def test_same_year_split_releases_remint_to_different_albums(refresh, monkeypatch):
    """A third album claims the id both releases hash to, so BOTH
    releases remint at attempt 1: the remint seed carries the release
    suffix, so the deluxe release does not re-merge into the standard
    one through the remint path (without the suffix both seeds are the
    same string and the second remint lands on the first's row)."""
    monkeypatch.setattr("core.soulsync_client._stable_id", _fake_stable_id(exact={
        "Artist::Album::rel-1::2020::soulsync::1": "501",
        "Artist::Album::rel-2::2020::soulsync::1": "502",
        "artist::album::rel-1": "500",
        "artist::album::rel-2": "500",
        "artist::claimant": "500",
    }))

    conn, finished, summaries = refresh({
        "aaa.flac": _tags("Claim", "Artist", "Claimant"),
        "std.flac": _tags("Std", "Artist", "Album", mbid="rel-1", comment="standard"),
        "dlx.flac": _tags("Dlx", "Artist", "Album", mbid="rel-2", comment="deluxe"),
    })

    albums = conn.execute("SELECT id, track_count FROM albums").fetchall()
    tracks = conn.execute("SELECT album_id, file_path FROM tracks").fetchall()
    assert {a["id"] for a in albums} == {"500::soulsync", "501::soulsync", "502::soulsync"}
    assert {t["album_id"] for t in tracks} == {"500::soulsync", "501::soulsync", "502::soulsync"}
    assert all(a["track_count"] == 1 for a in albums)
    assert finished[-1][1] == 3
    assert finished[-1][3] == 3
    assert finished[-1][4] == 0


def test_releases_without_disambiguation_comments_stay_merged(refresh):
    """_split_by_release deliberately does not split on release ids
    alone (no comment carried) — a lone-looking release keeps the plain
    key, matching the client scan."""
    conn, finished, summaries = refresh({
        "x.flac": _tags("X", "Artist", "Album", mbid="rel-1"),
        "y.flac": _tags("Y", "Artist", "Album", mbid="rel-2"),
    })

    albums = conn.execute("SELECT track_count FROM albums").fetchall()
    assert len(albums) == 1
    assert albums[0]["track_count"] == 2


def test_multi_year_album_group_warns(refresh, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        refresh({
            "a.flac": _tags("A", "Artist", "Album", year="2020"),
            "b.flac": _tags("B", "Artist", "Album", year="2021"),
        })
    assert any("has tracks with years" in r.message for r in caplog.records)


def test_claimed_album_id_guard_keeps_split_releases_apart(refresh, monkeypatch):
    """Two split releases hash to the SAME primary album id, with no
    third claimant: title, artist and year all match the first
    release's row, so only the claimed-ids guard stops the second
    release from merging into the first. (Mutation-checked: removing
    the guard fails this test and no other.)"""
    monkeypatch.setattr("core.soulsync_client._stable_id", _fake_stable_id(exact={
        "Artist::Album::rel-1::2020::soulsync::1": "501",
        "artist::album::rel-1": "500",
        "artist::album::rel-2": "500",
    }))

    conn, finished, summaries = refresh({
        "std.flac": _tags("Std", "Artist", "Album", mbid="rel-1", comment="standard"),
        "dlx.flac": _tags("Dlx", "Artist", "Album", mbid="rel-2", comment="deluxe"),
    })

    albums = conn.execute("SELECT id, track_count FROM albums").fetchall()
    tracks = conn.execute("SELECT album_id FROM tracks").fetchall()
    assert {a["id"] for a in albums} == {"500::soulsync", "501::soulsync"}
    assert {t["album_id"] for t in tracks} == {"500::soulsync", "501::soulsync"}
    assert all(a["track_count"] == 1 for a in albums)
    assert finished[-1][1] == 2
    assert finished[-1][4] == 0


def test_track_remint_exhaustion_counts_as_failed(refresh, monkeypatch):
    """Every remint for y.flac lands on x.flac's id: after 10 attempts
    the track is dropped, counted failed — not silently, not merged."""
    mapping = {f"y.flac::soulsync::{i}": "100" for i in range(1, 11)}
    mapping.update({"y.flac": "100", "x.flac": "100"})
    monkeypatch.setattr("core.soulsync_client._stable_id",
                        _fake_stable_id(suffix=mapping))

    conn, finished, summaries = refresh({
        "x.flac": _tags("X", "Artist", "Album"),
        "y.flac": _tags("Y", "Artist", "Album"),
    })

    rows = conn.execute("SELECT file_path FROM tracks").fetchall()
    assert len(rows) == 1
    assert rows[0]["file_path"].endswith("x.flac")
    assert finished[-1][3] == 1  # successful
    assert finished[-1][4] == 1  # failed


def test_album_remint_exhaustion_skips_album_and_counts_failed(refresh, monkeypatch):
    """Both albums hash to 700 and every remint for the second one does
    too: the album is skipped (its track counted failed) instead of
    being filed under the claimant's row."""
    exact = {f"Artist::Album::2020::soulsync::{i}": "700" for i in range(1, 11)}
    exact.update({"artist::album": "700", "artist::claimant": "700"})
    monkeypatch.setattr("core.soulsync_client._stable_id",
                        _fake_stable_id(exact=exact))

    conn, finished, summaries = refresh({
        "aaa.flac": _tags("Claim", "Artist", "Claimant"),
        "b.flac": _tags("B", "Artist", "Album"),
    })

    albums = conn.execute("SELECT title FROM albums").fetchall()
    tracks = conn.execute("SELECT title FROM tracks").fetchall()
    assert [a["title"] for a in albums] == ["Claimant"]
    assert [t["title"] for t in tracks] == ["Claim"]
    assert finished[-1][1] == 1  # album_count
    assert finished[-1][3] == 1  # successful
    assert finished[-1][4] == 1  # failed


def test_surviving_rows_merge_and_duplicate_instead_of_splitting(refresh, tmp_path):
    """Rows that survive the wipe (the harness's clear_server_data is a
    no-op stub, like owner-profile rows in production) are recognised:
    the artist/album merge into the surviving rows and an already
    present file counts as a duplicate success, not a new row."""
    transfer = tmp_path / "Transfer"
    artist_id = _REAL_STABLE_ID("artist") + "::soulsync"
    album_id = _REAL_STABLE_ID("artist::album") + "::soulsync"
    a_path = str(transfer / "a.flac")
    track_id = _REAL_STABLE_ID(a_path) + "::soulsync"

    def preseed(conn):
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, 'Artist', 'soulsync')",
                     (artist_id,))
        conn.execute("INSERT INTO albums (id, artist_id, title, year, track_count, server_source)"
                     " VALUES (?, ?, 'Album', 2020, 1, 'soulsync')", (album_id, artist_id))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source)"
                     " VALUES (?, ?, ?, 'A', ?, 'soulsync')", (track_id, album_id, artist_id, a_path))
        conn.commit()

    conn, finished, summaries = refresh({
        "a.flac": _tags("A", "Artist", "Album"),
        "b.flac": _tags("B", "Artist", "Album"),
    }, preseed=preseed)

    assert conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 1
    albums = conn.execute("SELECT id, track_count FROM albums").fetchall()
    assert len(albums) == 1
    assert albums[0]["track_count"] == 2  # recomputed over both files
    assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 2
    assert finished[-1] == (1, 1, 2, 2, 0)
