"""sample studio on a jellyfin or navidrome library (discord, Specialmed).

tracks.id is TEXT: jellyfin ids are guids, navidrome ids are alphanumeric.
every studio route cast the id with int(), so analysis and the waveform were
a 400 on every track, the page blamed the connection, and the waveform stayed
black. the analysis table also couldn't hold a text id. these run the real
blueprint over a real database and the real analysis worker.
"""

import sqlite3
import time

import numpy as np
import pytest
import soundfile as sf
from flask import Blueprint, Flask

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

    db = mdb.get_database()

    def add_track(track_id, title="Song"):
        wav = tmp_path / f"{track_id}.wav"
        _click_track(wav)
        conn = db._get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'Some Artist')")
            conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('al1', 'ar1', 'Some Album')")
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (?, 'al1', 'ar1', ?, ?)",
                         (track_id, title, str(wav)))
            conn.commit()
        finally:
            conn.close()

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


def test_a_jellyfin_track_analyzes_draws_and_chops(studio):
    c, _, add_track = studio
    add_track(GUID)

    analysis = _analysis_done(c, GUID)
    assert analysis["track_id"] == GUID
    assert abs(analysis["bpm"] - 100) < 5

    r = c.get("/api/v1/sample/peaks", query_string={"track_id": GUID, "buckets": 200})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["data"]["max"]) == 200

    r = c.post("/api/v1/sample/chop", json={"track_id": GUID, "start_s": 0, "end_s": 1,
                                            "name": "guid chop", "format": "wav16"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["track_id"] == GUID
    stash = c.get("/api/v1/sample/stash").get_json()["data"]["entries"]
    assert [e["track_id"] for e in stash] == [GUID]


def test_stems_routes_take_a_text_id(studio):
    c, _, add_track = studio
    add_track(GUID)
    r = c.get("/api/v1/sample/stems/status", query_string={"track_id": GUID})
    assert r.status_code == 200, r.get_json()
    r = c.get(f"/api/v1/sample/stems/{GUID}/drums/audio")
    assert r.status_code == 404                     # not split yet, not a 500
    assert "split" in r.get_json()["error"]["message"]


def test_a_plex_id_comes_back_as_text_and_matches_its_stash(studio):
    """stash rows came back numeric and tracks as text, so the page's
    "is this the open track" check never matched."""
    c, _, add_track = studio
    add_track("12345")
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
    add_track(GUID, title="Leuchtturm")
    from api.sample import recent_library_tracks
    tracks = recent_library_tracks(10)
    assert tracks[0]["id"] == GUID
    assert tracks[0]["artist_name"] == "Some Artist"
    assert tracks[0]["album_title"] == "Some Album"
