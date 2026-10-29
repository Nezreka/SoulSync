"""Upstream Sample Studio regressions through native Library-v2 identities.

Jellyfin/Navidrome IDs live in media mappings; Sample Studio uses the native
ID returned by Library v2. Real server-mapped files still analyze, draw and
chop, and track/stash IDs use the same string representation. Server IDs do
not become a second way to bypass native file ownership.
"""

import sqlite3
import time

import numpy as np
import pytest
import soundfile as sf
from flask import Blueprint, Flask
from tests.lib2_seed import file_track
from core.library2.media_mappings import upsert_mapping

GUID = "5f1c0a3e9b7d4e21a8c6f0b2d4e6a8c0"
SR = 22050


def _click_track(path, bpm=100.0, seconds=6.0):
    n = int(seconds * SR)
    y = 0.05 * np.sin(2 * np.pi * 55 * np.arange(n) / SR)
    click = np.exp(-np.arange(int(0.02 * SR)) / (0.004 * SR))
    b = 0.0
    while b < seconds - 0.05:
        i = int(b * SR)
        y[i:i + len(click)] += 0.9 * click
        b += 60.0 / bpm
    sf.write(str(path), (y / np.max(np.abs(y)) * 0.9).astype(np.float32), SR, subtype="PCM_16")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "_database_instances", {})
    import api.sample as sample_api
    monkeypatch.setattr(sample_api, "require_api_key", lambda f: f)

    app = Flask(__name__)
    bp = Blueprint("text_ids", __name__)
    sample_api.register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")

    from core.sample import worker
    monkeypatch.setattr(worker, "_status", {})
    monkeypatch.setattr(worker, "_pending", set())
    db = mdb.get_database()

    def add_track(server_id, title="Song", source="jellyfin"):
        track_id = int(server_id) if server_id.isdecimal() else 1
        wav = tmp_path / f"{server_id}.wav"
        _click_track(wav)
        conn = db._get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO lib2_artists (id, name) VALUES (1, 'Some Artist')")
            conn.execute("INSERT OR IGNORE INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Some Album')")
            file_track(conn, track_id, 1, title, str(wav))
            upsert_mapping(conn.cursor(), "track", track_id, source, server_id, server_path=str(wav))
            conn.commit()
        finally:
            conn.close()

        return str(track_id)

    with app.test_client() as c:
        yield c, db, add_track


def _analysis_done(c, track_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get("/api/v1/sample/analysis", query_string={"track_id": track_id})
        assert r.status_code in (200, 202), r.get_json()
        data = r.get_json()["data"]
        if data["status"] == "done":
            return data
        assert not data["status"].startswith("error"), data
        time.sleep(0.5)
    raise AssertionError("analysis never finished")


@pytest.mark.parametrize("source, server_id", [("jellyfin", GUID), ("navidrome", "nd-song_9af")])
def test_a_server_mapped_track_analyzes_draws_and_chops(studio, source, server_id):
    c, _, add_track = studio
    track_id = add_track(server_id, source=source)

    analysis = _analysis_done(c, track_id)
    assert analysis["track_id"] == track_id
    assert abs(analysis["bpm"] - 100) < 5

    r = c.get("/api/v1/sample/peaks", query_string={"track_id": track_id, "buckets": 200})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["data"]["max"]) == 200

    r = c.post("/api/v1/sample/chop", json={"track_id": track_id, "start_s": 0, "end_s": 1,
                                            "name": "guid chop", "format": "wav16"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["track_id"] == track_id
    stash = c.get("/api/v1/sample/stash").get_json()["data"]["entries"]
    assert [e["track_id"] for e in stash] == [track_id]


def test_stems_routes_take_a_text_id(studio):
    c, _, add_track = studio
    track_id = add_track(GUID)
    r = c.get("/api/v1/sample/stems/status", query_string={"track_id": track_id})
    assert r.status_code == 200, r.get_json()
    r = c.get(f"/api/v1/sample/stems/{track_id}/drums/audio")
    assert r.status_code == 404                     # not split yet, not a 500
    assert "split" in r.get_json()["error"]["message"]


def test_a_plex_id_comes_back_as_text_and_matches_its_stash(studio):
    """stash rows came back numeric and tracks as text, so the page's
    "is this the open track" check never matched."""
    c, _, add_track = studio
    add_track("12345", source="plex")
    assert _analysis_done(c, 12345)["track_id"] == "12345"   # a number still works
    r = c.post("/api/v1/sample/chop", json={"track_id": "12345", "start_s": 0, "end_s": 1,
                                            "name": "plex chop", "format": "wav16"})
    assert r.status_code == 201, r.get_json()
    assert c.get("/api/v1/sample/stash").get_json()["data"]["entries"][0]["track_id"] == "12345"


@pytest.mark.parametrize("bad", ["", "../etc", "a b", "x" * 200, "a/b"])
def test_ids_that_could_escape_a_path_are_refused(studio, bad):
    c, _, _ = studio
    r = c.get("/api/v1/sample/analysis", query_string={"track_id": bad})
    assert r.status_code == 400


def test_old_integer_table_is_rebuilt_and_keeps_its_rows(tmp_path):
    """an install from before this keyed sample_analysis by INTEGER PRIMARY
    KEY, which refuses text outright. the rebuild keeps plex rows."""
    from database.music_database import MusicDatabase

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE sample_analysis (track_id INTEGER PRIMARY KEY, bpm REAL,
                    onsets_json TEXT, duration_s REAL, analyzed_at REAL,
                    analyzer_version INTEGER DEFAULT 1)""")
    conn.execute("INSERT INTO sample_analysis (track_id, bpm, onsets_json, duration_s) VALUES (7, 120.0, '[]', 3.0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sample_analysis (track_id, bpm) VALUES (?, 1)", (GUID,))
    conn.commit()
    conn.close()

    MusicDatabase(database_path=str(path))

    conn = sqlite3.connect(str(path))
    try:
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(sample_analysis)")}
        assert cols["track_id"] == "TEXT"
        assert "source_sig" in cols and "key_name" in cols
        assert conn.execute("SELECT bpm FROM sample_analysis WHERE track_id = '7'").fetchone() == (120.0,)
        conn.execute("INSERT INTO sample_analysis (track_id, bpm) VALUES (?, 1)", (GUID,))
    finally:
        conn.close()


def test_recent_tracks_carry_artist_and_album_names(studio):
    """the panel listed every track as "Unknown artist": the recent route
    returned bare track rows with no names."""
    _, _, add_track = studio
    track_id = add_track(GUID, title="Leuchtturm")
    from api.sample import recent_library_tracks
    tracks = recent_library_tracks(10)
    assert str(tracks[0]["id"]) == track_id
    assert tracks[0]["artist_name"] == "Some Artist"
    assert tracks[0]["album_title"] == "Some Album"


def test_a_server_guid_is_not_an_alternate_catalogue_identity(studio):
    c, db, add_track = studio
    track_id = add_track(GUID)
    with db._get_connection() as conn:
        row = conn.execute("SELECT entity_id, server_id FROM lib2_media_server_mappings WHERE server_source='jellyfin'").fetchone()
        assert str(row["entity_id"]) == track_id and row["server_id"] == GUID
    assert c.get("/api/v1/sample/analysis", query_string={"track_id": GUID}).status_code == 404
    assert c.get("/api/v1/sample/stems/status", query_string={"track_id": track_id}).status_code == 200
