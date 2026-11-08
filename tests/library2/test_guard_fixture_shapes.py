"""Guard: test fixtures in the shape of the real 3.6.1 data.

The upgrade's worst bug this week (a join that scanned every legacy row per
lib2 row and hung a real upgrade, Nov 7) stayed hidden because the shared
fixture declared the 3.6.1 ids INTEGER; the real tables declare them TEXT,
holding Plex ratingKeys, Jellyfin GUIDs and Navidrome hashes. Two checks:

* the shared legacy fixture (tests/library2/conftest.py) declares every
  column it has with the type the real 3.6.1 table declares
  (tests/support/legacy_361_schema.json, read from a real install);
* the importer gives the same catalogue for the same library whatever shape
  the server's ids have (review 5 X3).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.lib2_seed import row_conn

_SNAPSHOT = json.loads((Path(__file__).resolve().parents[1] / "support"
                        / "legacy_361_schema.json").read_text(encoding="utf-8"))


def test_the_shared_legacy_fixture_declares_the_real_types(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    try:
        wrong = []
        for table, real in _SNAPSHOT.items():
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if not cols:
                continue
            for _cid, name, declared, *_rest in cols:
                if name not in real:
                    wrong.append(f"{table}.{name}: not a 3.6.1 column")
                elif (declared or "").upper() != (real[name] or "").upper():
                    wrong.append(f"{table}.{name}: {declared} here, {real[name]} in 3.6.1")
    finally:
        conn.close()
    assert not wrong, "\n".join(wrong)


def test_the_snapshot_keys_3_6_1_on_text_ids():
    """the property the fixture exists to keep"""
    assert _SNAPSHOT["tracks"]["id"] == "TEXT"
    assert _SNAPSHOT["albums"]["artist_id"] == "TEXT"



class LegacyDBShim:
    def __init__(self, path):
        self.path = path

    @property
    def database_path(self):
        return self.path

    def _get_connection(self):
        conn = row_conn(self.path)
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

_DDL = """
CREATE TABLE artists(id {K} PRIMARY KEY, name TEXT, thumb_url TEXT, genres TEXT, summary TEXT,
    spotify_artist_id TEXT, musicbrainz_id TEXT, server_source TEXT);
CREATE TABLE albums(id {K} PRIMARY KEY, artist_id {K}, title TEXT, year INTEGER,
    thumb_url TEXT, genres TEXT, track_count INTEGER, release_date TEXT, server_source TEXT);
CREATE TABLE tracks(id {K} PRIMARY KEY, album_id {K}, artist_id {K}, title TEXT,
    track_number INTEGER, duration INTEGER, file_path TEXT, bitrate INTEGER,
    file_size INTEGER, track_artist TEXT, server_source TEXT);
CREATE TABLE listening_history(id INTEGER PRIMARY KEY, track_id TEXT, title TEXT, artist TEXT,
    album TEXT, played_at TIMESTAMP, duration_ms INTEGER, server_source TEXT, db_track_id INTEGER,
    profile_id INTEGER DEFAULT 1, lib2_track_id INTEGER);
"""

IDS = {
    "int": dict(K="INTEGER", ar=1, al1=10, al2=11, t1=100, t2=101, t3=102, src="plex"),
    "plex_text": dict(K="TEXT", ar="1", al1="10", al2="11", t1="100", t2="101", t3="102", src="plex"),
    "jellyfin": dict(K="TEXT", ar="369dcfe2b649cf4a43dcf100f03623d4", al1="0015c50a3b0c8309df179e77aa253dc8",
                     al2="0824089d084ba81f9c6b61aeca79f83e", t1="01487b14a9c94bc432426339df907145",
                     t2="0153af5fbfbc13000ab6bcfa65a89367", t3="020dae7f2e553e3f9ec8de35436000d8", src="jellyfin"),
    "navidrome": dict(K="TEXT", ar="1mNJ8hAlbt4jJc2dFaf38y", al1="0IOchBY6UeMfO9StCNxMil",
                      al2="0ahEQmmX7ZBvx2aoWcAjsc", t1="0c3V7mbYf8kntpm4ihF3Jf", t2="lgBTEM6LUqlvJlwyFgIQPj",
                      t3="RFqiLHn3ADTEzpcMA1s2v9", src="navidrome"),
}


def _make(tmp_path, shape):
    v = IDS[shape]
    path = str(tmp_path / f"{shape}.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL.format(K=v["K"]))
    s = v["src"]
    conn.execute("INSERT INTO artists VALUES(?,?,?,?,?,?,?,?)", (v["ar"], "Drake", "http://img", '["rap"]', "A rapper", "sp1", None, s))
    conn.execute("INSERT INTO albums VALUES(?,?,?,?,?,?,?,?,?)", (v["al1"], v["ar"], "Views", 2016, None, '["rap"]', 2, "2016-04-29", s))
    conn.execute("INSERT INTO albums VALUES(?,?,?,?,?,?,?,?,?)", (v["al2"], v["ar"], "One Dance", 2016, None, None, 1, None, s))
    conn.execute("INSERT INTO tracks VALUES(?,?,?,?,?,?,?,?,?,?,?)", (v["t1"], v["al1"], v["ar"], "One Dance", 1, 200000, "/m/01.flac", 1000000, 5000, "Drake feat. Wizkid", s))
    conn.execute("INSERT INTO tracks VALUES(?,?,?,?,?,?,?,?,?,?,?)", (v["t2"], v["al1"], v["ar"], "Hotline Bling", 2, 180000, "/m/02.flac", None, None, None, s))
    conn.execute("INSERT INTO tracks VALUES(?,?,?,?,?,?,?,?,?,?,?)", (v["t3"], v["al2"], v["ar"], "One Dance", 1, 200000, "/m/single.flac", 900, 5000, None, s))
    conn.execute("INSERT INTO listening_history(track_id,title,artist,played_at,server_source,db_track_id) VALUES(?,?,?,?,?,?)",
                 (str(v["t2"]), "Hotline Bling", "Drake", "2026-01-01 00:00:00", s, v["t2"]))
    conn.commit()
    conn.close()
    return LegacyDBShim(path), v


def _summary(shim, v):
    conn = row_conn(shim.path)
    back = {str(v[k]): k for k in ("ar", "al1", "al2", "t1", "t2", "t3")}
    out = {}
    out["artists"] = sorted((r["name"], back.get(str(r["legacy_artist_id"]))) for r in conn.execute(
        "SELECT name, legacy_artist_id FROM lib2_artists"))
    out["albums"] = sorted((r["title"], r["album_type"], back.get(str(r["legacy_album_id"]))) for r in conn.execute(
        "SELECT title, album_type, legacy_album_id FROM lib2_albums"))
    out["tracks"] = sorted((r["title"], r["at"], back.get(str(r["legacy_track_id"])), r["path"]) for r in conn.execute(
        "SELECT t.title, al.title AS at, t.legacy_track_id, f.path FROM lib2_tracks t "
        "JOIN lib2_albums al ON al.id=t.album_id LEFT JOIN lib2_track_files f ON f.track_id=t.id"))
    out["files_legacy"] = sorted(back.get(str(r[0])) for r in conn.execute("SELECT legacy_track_id FROM lib2_track_files"))
    out["play_link"] = [r[0] for r in conn.execute(
        "SELECT t.title FROM listening_history h LEFT JOIN lib2_tracks t ON t.id=h.lib2_track_id")]
    conn.close()
    return out


@pytest.mark.parametrize("shape", ["plex_text", "jellyfin", "navidrome"])
def test_every_server_id_shape_imports_the_same_catalogue(tmp_path, shape):
    from core.library2.importer import import_legacy_library
    base_shim, base_v = _make(tmp_path, "int")
    import_legacy_library(base_shim)
    shim, v = _make(tmp_path, shape)
    import_legacy_library(shim)
    assert _summary(shim, v) == _summary(base_shim, base_v)
