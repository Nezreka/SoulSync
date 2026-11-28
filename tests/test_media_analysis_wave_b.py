"""tests for Wave B: Music analysis (waveform, transitions, BPM, key),
Sonic similarity / Sonic Adventure, and Jellyfin-compatible Trickplay thumbnails."""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

from flask import Flask
from PIL import Image

from api.media_analysis import bp as media_analysis_bp
from core.analysis import music_analysis, sonic
from core.video import trickplay
from database.music_database import MusicDatabase


def _write_test_wav(path: Path, freq_hz: float, *, duration_s: float = 6.0, lead_silence_s: float = 0.6, pulse_hz: float = 2.0):
    sr = 11025
    total = int(sr * duration_s)
    lead = int(sr * lead_silence_s)
    trail = int(sr * 0.5)
    samples = array.array("h")
    for i in range(total):
        if i < lead or i >= total - trail:
            samples.append(0)
            continue
        t = i / float(sr)
        # rhythmic amplitude pulse + harmonic tone
        env = 0.35 + 0.65 * max(0.0, math.sin(2.0 * math.pi * pulse_hz * t))
        val = int(18000 * env * (0.7 * math.sin(2.0 * math.pi * freq_hz * t) + 0.3 * math.sin(2.0 * math.pi * freq_hz * 1.5 * t)))
        samples.append(max(-32767, min(32767, val)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


def test_music_analysis_and_sonic_similarity_and_adventure(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    # Track 1 & 2: similar A4 (440 Hz) 120 BPM tracks; Track 3: different D#5 (622 Hz) slower track
    w1, w2, w3 = tmp_path / "t1.wav", tmp_path / "t2.wav", tmp_path / "t3.wav"
    _write_test_wav(w1, 440.0, pulse_hz=2.0)
    _write_test_wav(w2, 440.0, pulse_hz=2.0)
    _write_test_wav(w3, 622.25, pulse_hz=1.25)

    r1 = music_analysis.analyze_track_file(db, 101, str(w1))
    r2 = music_analysis.analyze_track_file(db, 102, str(w2))
    r3 = music_analysis.analyze_track_file(db, 103, str(w3))

    assert len(r1["waveform"]) == music_analysis.WAVEFORM_BUCKETS
    assert r1["leading_silence_s"] >= 0.4
    assert r1["trailing_silence_s"] >= 0.3
    assert r1["mix_out_s"] > r1["mix_in_s"]
    assert r1["camelot"] in music_analysis.CAMELOT_MAP.values()
    assert music_analysis.get_track_analysis(db, 101)["track_id"] == 101

    # Track 102 should be the #1 most sonically similar track to 101
    sim = sonic.similar_tracks(db, 101, limit=5)
    assert sim[0]["track_id"] == 102
    assert sim[0]["score"] > sim[1]["score"]

    # Sonic Adventure from 101 -> 103 visits 102 in between
    adv = sonic.sonic_adventure(db, 101, 103, steps=3)
    assert [step["track_id"] for step in adv] == [101, 102, 103]
    assert r2["duration_s"] > 5.0 and r3["duration_s"] > 5.0


def test_trickplay_jellyfin_layout_and_api_gates(tmp_path, monkeypatch):
    video = tmp_path / "Inception (2010).mkv"
    video.write_bytes(b"FAKEVIDEO")
    tp_dir = trickplay.trickplay_folder(str(video), width=320, cols=10, rows=10)
    tp_dir.mkdir(parents=True)
    # create a real 3200x1800 sprite sheet (10x10 tiles of 320x180)
    Image.new("RGB", (3200, 1800), (20, 40, 80)).save(tp_dir / "0.jpg", "JPEG")
    Image.new("RGB", (3200, 1800), (40, 20, 80)).save(tp_dir / "1.jpg", "JPEG")

    manifest = trickplay.get_trickplay_manifest(str(video))
    assert manifest is not None
    assert manifest["tile_width"] == 320
    assert manifest["tile_height"] == 180
    assert manifest["sprite_count"] == 2

    # 125 seconds at 10s interval = tile 12 -> sprite 0, row 1, col 2
    t = trickplay.tile_for_timestamp(manifest, 125.0)
    assert t == {"sprite": 0, "x": 640, "y": 180, "width": 320, "height": 180}
    # 1050 seconds = tile 105 -> sprite 1, row 0, col 5
    t2 = trickplay.tile_for_timestamp(manifest, 1050.0)
    assert t2 == {"sprite": 1, "x": 1600, "y": 0, "width": 320, "height": 180}

    # verify @admin_only on POST trickplay route
    app = Flask(__name__)
    app.secret_key = "test-ma"
    app.register_blueprint(media_analysis_bp)
    c = app.test_client()
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: False)
    assert c.post("/api/analysis/video/movie/1/trickplay").status_code == 403
    assert c.post("/api/analysis/track/1").status_code == 403
