"""Sample Studio Phase 3 — end-to-end through the real api/sample.py blueprint.

Spins up a minimal Flask app, registers the REAL register_routes with auth
stubbed out, points DATABASE_PATH at a tmp dir, inserts one artist/album/track
(synthetic 100 BPM click track), then walks the whole v1 flow:

  analyze -> poll analysis -> preview -> chop -> stash list -> export zip
  -> stash audio -> delete -> stash empty

The background analysis worker runs for real (daemon thread); the poll loop
waits for it with a timeout.
"""

import io
import json
import time
import zipfile

import numpy as np
import pytest
import soundfile as sf
from flask import Blueprint, Flask

SR = 22050
BPM = 100.0
BEAT_S = 60.0 / BPM


def _click_track(path, seconds=8.0):
    n = int(seconds * SR)
    y = np.zeros(n, dtype=np.float32)
    t = np.arange(n) / SR
    y += 0.05 * np.sin(2 * np.pi * 55 * t).astype(np.float32)
    click = np.exp(-np.arange(int(0.02 * SR)) / (0.004 * SR)).astype(np.float32)
    b = 0.0
    while b < seconds - 0.05:
        i = int(b * SR)
        y[i : i + len(click)] += 0.9 * click
        b += BEAT_S
    y = (y / np.max(np.abs(y)) * 0.9).astype(np.float32)
    sf.write(str(path), y, SR, subtype="PCM_16")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    # Fresh DB singletons per test (get_database is thread-local + cached).
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})

    import api.sample as sample_api

    monkeypatch.setattr(sample_api, "require_api_key", lambda f: f)

    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("sample_e2e", __name__)
    sample_api.register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")

    wav = tmp_path / "track.wav"
    _click_track(wav)

    db = mdb.get_database()
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'E2E Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'E2E Album')")
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, 'E2E Track', ?)",
            (str(wav),),
        )
        conn.commit()
    finally:
        conn.close()

    with app.test_client() as c:
        yield c, db


def _wait_for_analysis(client, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get("/api/v1/sample/analysis", query_string={"track_id": 1})
        payload = r.get_json()["data"]
        if payload.get("status") == "done":
            return payload
        assert payload.get("status") in ("pending", "running", "queued", "analyzing"), payload
        time.sleep(1)
    raise AssertionError("analysis worker never finished")


def test_full_chop_lifecycle(client):
    c, db = client

    # 1. Analyze (lazy enqueue) then poll until the worker finishes.
    r = c.post("/api/v1/sample/analyze", json={"track_id": 1})
    assert r.status_code == 200
    analysis = _wait_for_analysis(c)
    assert abs(analysis["bpm"] - BPM) / BPM < 0.05, analysis["bpm"]
    assert len(analysis["onsets"]) > 5

    # 2. Preview the first two seconds.
    r = c.post(
        "/api/v1/sample/preview",
        json={"track_id": 1, "start_s": 0, "end_s": 2, "pitch_st": 2},
    )
    assert r.status_code == 200, r.get_json()
    preview = r.get_json()["data"]
    assert preview["engine"] == "librosa"
    r = c.get(f"/api/v1/sample/preview/{preview['preview_id']}")
    assert r.status_code == 200
    assert r.content_type.startswith("audio/")

    # 3. Save a chop: file + bookmark row.
    r = c.post(
        "/api/v1/sample/chop",
        json={
            "track_id": 1,
            "start_s": 0,
            "end_s": 2,
            "pitch_st": 0,
            "name": "e2e chop",
            "tags": ["test", "e2e"],
            "format": "wav16",
        },
    )
    assert r.status_code == 201, r.get_json()
    entry = r.get_json()["data"]
    assert entry["name"] == "e2e chop"
    assert entry["tags"] == ["test", "e2e"]
    assert entry["track_id"] == 1
    assert entry["start_s"] == 0 and entry["end_s"] == 2
    import os

    assert os.path.isfile(entry["file_path"])

    # 4. Stash list shows it, with the joined track title.
    r = c.get("/api/v1/sample/stash")
    entries = r.get_json()["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["track_title"] == "E2E Track"
    entry_id = entries[0]["id"]

    # 5. Chop audio serves.
    r = c.get(f"/api/v1/sample/stash/{entry_id}/audio")
    assert r.status_code == 200
    assert r.content_type.startswith("audio/")

    # 6. ZIP export contains exactly the one chop.
    r = c.get("/api/v1/sample/stash/export")
    assert r.status_code == 200
    assert r.content_type == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert len(zf.namelist()) == 1
    assert zf.namelist()[0].endswith(".wav")

    # 7. Delete removes the row AND the file.
    r = c.delete(f"/api/v1/sample/stash/{entry_id}")
    assert r.status_code == 200
    assert not os.path.isfile(entry["file_path"])
    r = c.get("/api/v1/sample/stash")
    assert r.get_json()["data"]["entries"] == []


def test_preview_rejects_long_slice(client, tmp_path):
    c, db = client
    # The cap applies to the real slice: use a 70s track so 0..61 is really 61s.
    long_wav = str(tmp_path / "long.wav")
    _click_track(long_wav, seconds=70.0)
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (3, 1, 1, 'Long', ?)",
            (long_wav,),
        )
        conn.commit()
    finally:
        conn.close()
    r = c.post(
        "/api/v1/sample/preview",
        json={"track_id": 3, "start_s": 0, "end_s": 61},
    )
    assert r.status_code == 400


def test_chop_requires_name_and_known_track(client):
    c, _ = client
    r = c.post(
        "/api/v1/sample/chop",
        json={"track_id": 1, "start_s": 0, "end_s": 1, "name": "   "},
    )
    assert r.status_code == 400
    r = c.post(
        "/api/v1/sample/chop",
        json={"track_id": 999, "start_s": 0, "end_s": 1, "name": "x"},
    )
    assert r.status_code == 404


def test_stretch_needs_analysis_bpm(client):
    c, db = client
    # Track 2 has no analysis row: target_bpm must 409, plain chop works.
    import soundfile as _sf

    wav2 = str(db.database_path.parent / "t2.wav")
    _click_track(wav2)
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (2, 1, 1, 'T2', ?)",
            (wav2,),
        )
        conn.commit()
    finally:
        conn.close()
    r = c.post(
        "/api/v1/sample/preview",
        json={"track_id": 2, "start_s": 0, "end_s": 1, "target_bpm": 120},
    )
    assert r.status_code == 409
