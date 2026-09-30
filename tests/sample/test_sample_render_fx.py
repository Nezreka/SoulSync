"""sample studio: the backend half of the sep 29 page work, plus the review fixes.

the page shipped fx (normalize/reverse/fade/space/delay), trim silence and the
key display, but nothing on the server read any of it: fx were dropped so
previews and saved chops came out dry, /sample/trim 404'd, and the key was
never computed. these walk each one through the real blueprint, plus:

- previews decode just the selected region, not the whole song
- a replaced source file re-analyzes and gets a fresh waveform
- the demucs model name is the real checkpoint and its hash is checked
"""

import os
import time

import numpy as np
import pytest
import soundfile as sf
from flask import Blueprint, Flask

SR = 22050


def _stereo_track(path, seconds=6.0, bpm=120.0):
    """kick-ish clicks on every beat over a C F G C progression, left and
    right slightly different, with a second of silence at each end."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    progression = ((261.63, 329.63, 392.0), (349.23, 440.0, 523.25),
                   (392.0, 493.88, 587.33), (261.63, 329.63, 392.0))
    chord = np.zeros(n)
    seg = n // len(progression)
    for i, freqs in enumerate(progression):
        sl = slice(i * seg, n if i == len(progression) - 1 else (i + 1) * seg)
        chord[sl] = sum(np.sin(2 * np.pi * f * t[sl]) for f in freqs) / 3 * 0.25
    y = np.zeros((n, 2), dtype=np.float32)
    y[:, 0] = chord
    y[:, 1] = chord * 0.9
    beat = int(SR * 60 / bpm)
    click = np.hanning(200).astype(np.float32) * 0.7
    for i in range(0, n - 200, beat):
        y[i: i + 200] += click[:, None]
    pad = np.zeros((SR, 2), dtype=np.float32)
    sf.write(str(path), np.vstack([pad, y, pad]), SR, subtype="PCM_16")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    import api.sample as sample_api

    monkeypatch.setattr(sample_api, "require_api_key", lambda f: f)
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("sample_fx_e2e", __name__)
    sample_api.register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")

    wav = tmp_path / "track.wav"
    _stereo_track(wav)
    db = mdb.get_database()
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'FX Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'FX Album')")
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (1, 1, 1, 'FX Track', ?)",
            (str(wav),),
        )
        conn.commit()
    finally:
        conn.close()
    with app.test_client() as c:
        yield c, wav


def _preview_audio(c, **body):
    body.setdefault("track_id", 1)
    r = c.post("/api/v1/sample/preview", json=body)
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["data"]["preview_id"]
    r = c.get(f"/api/v1/sample/preview/{pid}")
    assert r.status_code == 200
    import io

    y, sr = sf.read(io.BytesIO(r.data), dtype="float32", always_2d=True)
    return y, sr


def _seed_analysis(bpm=120.0):
    from core.sample import store

    store.save_analysis(1, {"bpm": bpm, "onsets": [], "duration_s": 8.0})


# ── fx params ──────────────────────────────────────────────────────────


def test_parse_fx_defaults_and_validation():
    from core.sample.fx import FADE_MS_DEFAULT, parse_fx

    fx = parse_fx({})
    assert (fx.normalize, fx.reverse, fx.space, fx.delay) == (False, False, None, None)
    assert fx.fade_ms == FADE_MS_DEFAULT
    fx = parse_fx({"normalize": "peak", "reverse": True, "fade_ms": 10, "space": 0.5,
                   "delay": {"time": "1/8", "feedback": 0.35, "mix": 0.2}})
    assert fx.normalize and fx.reverse and fx.space == 0.5 and fx.delay.time == "1/8"
    for bad in ({"normalize": "rms"}, {"fade_ms": 0}, {"fade_ms": 5000}, {"space": 3},
                {"reverse": "yes"}, {"delay": {"time": "1/16", "feedback": 0, "mix": 0}},
                {"delay": {"time": "1/4", "feedback": 1.0, "mix": 0.2}},
                {"delay": {"time": "1/4", "feedback": 0.2, "mix": 2}}, {"space": "nan"}):
        with pytest.raises(ValueError):
            parse_fx(bad)


def test_bad_fx_is_a_400_not_a_silent_drop(client):
    c, _ = client
    r = c.post("/api/v1/sample/preview",
               json={"track_id": 1, "start_s": 1, "end_s": 2, "space": 9})
    assert r.status_code == 400
    assert "space" in r.get_json()["error"]["message"]


# ── fx dsp ─────────────────────────────────────────────────────────────


def test_reverse_fade_normalize():
    from core.sample.fx import NORMALIZE_PEAK, RenderFx, apply_fx

    ramp = np.linspace(0.0, 0.5, SR, dtype=np.float32)[:, None]
    out = apply_fx(ramp, SR, RenderFx(reverse=True, fade_ms=0.5))
    assert out[100, 0] > out[-100, 0]  # loud end now comes first
    faded = apply_fx(np.full((SR, 1), 0.5, np.float32), SR, RenderFx(fade_ms=50))
    assert abs(faded[0, 0]) < 1e-3 and abs(faded[-1, 0]) < 1e-3
    assert faded[SR // 2, 0] == pytest.approx(0.5)
    norm = apply_fx(np.full((SR, 1), 0.1, np.float32), SR, RenderFx(normalize=True, fade_ms=0.5))
    assert float(np.max(np.abs(norm))) == pytest.approx(NORMALIZE_PEAK, rel=1e-4)


def test_space_adds_a_decaying_tail_and_is_deterministic():
    from core.sample.fx import RenderFx, apply_fx

    y = np.zeros((SR // 2, 2), np.float32)
    y[100:300] = 0.8
    a = apply_fx(y, SR, RenderFx(space=0.5))
    b = apply_fx(y, SR, RenderFx(space=0.5))
    assert len(a) > len(y)  # the tail rings past the dry end
    assert np.array_equal(a, b)  # same preview and save
    tail = a[len(y):]
    first, last = np.abs(tail[: len(tail) // 4]).mean(), np.abs(tail[-len(tail) // 4:]).mean()
    assert last < first / 10


def test_delay_lands_on_the_beat():
    from core.sample.fx import DelayFx, RenderFx, apply_fx

    y = np.zeros((SR, 1), np.float32)
    y[0] = 1.0
    fx = RenderFx(fade_ms=0.5, delay=DelayFx(time="1/4", feedback=0.5, mix=0.5))
    out = apply_fx(y, SR, fx, bpm=120.0)
    beat = int(round(0.5 * SR))  # 120 bpm quarter = 0.5 s
    assert out[beat, 0] == pytest.approx(0.5, abs=1e-3)
    assert out[2 * beat, 0] == pytest.approx(0.25, abs=1e-3)
    with pytest.raises(ValueError):
        apply_fx(y, SR, fx, bpm=None)


def test_delay_tail_is_capped():
    from core.sample.fx import DELAY_MAX_TAIL_S, DelayFx, RenderFx, apply_fx

    y = np.ones((SR // 10, 1), np.float32) * 0.1
    out = apply_fx(y, SR, RenderFx(delay=DelayFx(time="1/8", feedback=0.99, mix=0.5)), bpm=140)
    assert len(out) <= len(y) + DELAY_MAX_TAIL_S * SR + SR


# ── fx through preview + save ──────────────────────────────────────────


def test_preview_renders_the_fx_it_was_sent(client):
    """the bug: fx on the page, dry audio from the server."""
    c, _ = client
    dry, _ = _preview_audio(c, start_s=1.0, end_s=2.0)
    rev, _ = _preview_audio(c, start_s=1.0, end_s=2.0, reverse=True)
    assert len(dry) == len(rev)
    assert not np.allclose(dry, rev, atol=1e-3)
    assert np.allclose(dry[::-1][2000:-2000], rev[2000:-2000], atol=2e-3)
    wet, _ = _preview_audio(c, start_s=1.0, end_s=2.0, space=0.6)
    assert len(wet) > len(dry)


def test_delay_needs_a_tempo(client):
    c, _ = client
    body = {"track_id": 1, "start_s": 1, "end_s": 2,
            "delay": {"time": "1/4", "feedback": 0.3, "mix": 0.3}}
    r = c.post("/api/v1/sample/preview", json=body)
    assert r.status_code == 409
    _seed_analysis(120.0)
    r = c.post("/api/v1/sample/preview", json=body)
    assert r.status_code == 200, r.get_json()


def test_saved_chop_bakes_and_echoes_the_recipe(client):
    c, _ = client
    _seed_analysis(120.0)
    recipe = {"normalize": "peak", "fade_ms": 12.5, "reverse": True, "space": 0.4,
              "delay": {"time": "1/8", "feedback": 0.3, "mix": 0.25}}
    r = c.post("/api/v1/sample/chop", json={"track_id": 1, "start_s": 1, "end_s": 2,
                                           "name": "fx chop", **recipe})
    assert r.status_code == 201, r.get_json()
    entry = r.get_json()["data"]
    for k, v in recipe.items():
        assert entry[k] == v, k
    y, _ = sf.read(entry["file_path"], always_2d=True)
    assert np.max(np.abs(y)) == pytest.approx(10 ** (-0.1 / 20), abs=2e-3)  # normalized
    listed = c.get("/api/v1/sample/stash").get_json()["data"]["entries"][0]
    for k, v in recipe.items():
        assert listed[k] == v, k


def test_old_stash_rows_read_back_with_defaults(client):
    from core.sample import store

    entry = store.create_stash_entry(
        name="old", tags=[], track_id=1, start_s=0, end_s=1, pitch_st=0,
        target_bpm=None, format="wav16", file_path="/nope.wav",
    )
    assert entry["normalize"] is None and entry["reverse"] is False
    assert entry["space"] is None and entry["delay"] is None and entry["fade_ms"] == 5.0


# ── region decode ──────────────────────────────────────────────────────


def test_decode_region_matches_a_slice_of_the_whole(client):
    from core.sample.render import decode_region, decode_stereo

    _, wav = client
    full, sr = decode_stereo(str(wav))
    part, sr2, dur = decode_region(str(wav), 1.5, 2.25)
    assert sr2 == sr and dur == pytest.approx(len(full) / sr)
    assert np.array_equal(part, full[int(1.5 * sr): int(2.25 * sr)])


def test_render_never_decodes_the_whole_track(client, monkeypatch):
    import core.sample.render as render

    def _boom(*a, **k):
        raise AssertionError("full-track decode on a preview")

    monkeypatch.setattr(render, "decode_stereo", _boom)
    c, _ = client
    _preview_audio(c, start_s=1.0, end_s=1.5)


def test_out_point_past_the_end_still_clamps(client):
    c, _ = client
    y, sr = _preview_audio(c, start_s=7.0, end_s=30.0)
    assert len(y) / sr == pytest.approx(1.0, abs=0.05)


# ── trim ───────────────────────────────────────────────────────────────


def test_sounding_bounds():
    from core.sample.trim import sounding_bounds

    y = np.zeros((SR * 3, 1), np.float32)
    y[SR: 2 * SR] = 0.5
    start, end = sounding_bounds(y, SR)
    assert start == pytest.approx(1.0, abs=0.012)
    assert end == pytest.approx(2.0, abs=0.025)
    assert sounding_bounds(np.zeros((SR, 2), np.float32), SR) is None


def test_trim_endpoint_tightens_to_the_music(client):
    """the track has a second of silence each side of the music."""
    c, _ = client
    r = c.post("/api/v1/sample/trim", json={"track_id": 1, "start_s": 0, "end_s": 8})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["trimmed"] is True
    assert data["start_s"] == pytest.approx(1.0, abs=0.02)
    assert data["end_s"] == pytest.approx(7.0, abs=0.03)


def test_trim_all_silence_leaves_the_selection_alone(client):
    c, _ = client
    r = c.post("/api/v1/sample/trim", json={"track_id": 1, "start_s": 0.1, "end_s": 0.9})
    assert r.get_json()["data"] == {"start_s": 0.1, "end_s": 0.9, "trimmed": False}


def test_trim_validates(client):
    c, _ = client
    assert c.post("/api/v1/sample/trim", json={"track_id": 1, "start_s": 2, "end_s": 1}).status_code == 400
    assert c.post("/api/v1/sample/trim", json={"track_id": 99, "start_s": 0, "end_s": 1}).status_code == 404
    assert c.post("/api/v1/sample/trim",
                  json={"track_id": 1, "start_s": 0, "end_s": 1, "stem": "kazoo"}).status_code == 400


# ── key ────────────────────────────────────────────────────────────────


def test_key_from_chroma_profiles():
    from core.sample.key import MAJOR_PROFILE, MINOR_PROFILE, key_from_chroma

    assert key_from_chroma(MAJOR_PROFILE)["name"] == "C major"
    assert key_from_chroma(np.roll(MINOR_PROFILE, 9))["name"] == "A minor"
    assert key_from_chroma(np.roll(MAJOR_PROFILE, 3))["name"] == "Eb major"
    assert key_from_chroma(np.zeros(12)) is None
    assert key_from_chroma(np.ones(12)) is None


def test_analysis_returns_the_key(client):
    """analyzer v3 stores the key and the page gets it on the next poll."""
    c, _ = client
    from core.sample import worker

    worker._process_one(1)
    r = c.get("/api/v1/sample/analysis", query_string={"track_id": 1})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["key"]["name"] == "C major"
    assert 0 < data["key"]["confidence"] <= 1
    assert "source_sig" not in data


def test_v2_rows_reanalyze_for_the_key(client):
    """rows from before the key existed must not be served as done forever."""
    c, _ = client
    from core.sample import store

    store.save_analysis(1, {"bpm": 99.0, "onsets": [], "duration_s": 8.0, "analyzer_version": 2})
    r = c.get("/api/v1/sample/analysis", query_string={"track_id": 1})
    assert r.status_code == 202
    data = r.get_json()["data"]
    assert data["bpm"] == 99.0  # the old numbers stay visible meanwhile
    assert data["status"] in ("pending", "running", "done")


# ── stale source files ─────────────────────────────────────────────────


def test_replaced_file_reanalyzes_and_redraws(client):
    c, wav = client
    from core.sample import store, worker

    worker._process_one(1)
    assert store.is_current(1)
    peaks_a = c.get("/api/v1/sample/peaks", query_string={"track_id": 1, "buckets": 64}).get_json()["data"]

    # the file gets upgraded in place: same path, different audio
    y = np.zeros((SR * 4, 2), np.float32)
    y[: SR, :] = 0.9
    sf.write(str(wav), y, SR, subtype="PCM_24")
    os.utime(wav, ns=(time.time_ns() + 5_000_000_000, time.time_ns() + 5_000_000_000))
    worker._resolve_cache.clear()

    assert not store.is_current(1)
    peaks_b = c.get("/api/v1/sample/peaks", query_string={"track_id": 1, "buckets": 64}).get_json()["data"]
    assert peaks_b["duration_s"] == pytest.approx(4.0, abs=0.01)
    assert peaks_b != peaks_a
    peaks_dir = os.path.dirname(store.peaks_path(1, 64))
    assert len([n for n in os.listdir(peaks_dir) if n.startswith("1_64")]) == 1


def test_drop_stale_peaks_spares_other_stems(tmp_path, monkeypatch):
    from core.sample import store

    monkeypatch.setattr(store, "sample_data_dir", lambda: str(tmp_path))
    keep = store.peaks_path(5, 64, None, sig="a")
    for p in (store.peaks_path(5, 64, None), store.peaks_path(5, 64, None, sig="b"),
              store.peaks_path(5, 64, "drums", sig="c"), store.peaks_path(55, 64, None, sig="d"), keep):
        open(p, "w").close()
    store.drop_stale_peaks(5, 64, None, keep=keep)
    left = sorted(os.listdir(os.path.dirname(keep)))
    assert os.path.basename(keep) in left
    assert any(n.startswith("5_64_drums") for n in left)
    assert any(n.startswith("55_64") for n in left)
    assert len(left) == 3


# ── separation methods ─────────────────────────────────────────────────


def test_rough_methods_are_gone(client):
    """the built-in rough splits sounded bad and were pulled. asking for them
    is a plain 400 now, not a silent demucs run."""
    c, _ = client
    for method in ("rough-drums", "rough-center", "karaoke"):
        r = c.post("/api/v1/sample/stems", json={"track_id": 1, "method": method})
        assert r.status_code == 400, method
    assert c.get("/api/v1/sample/stems/1/drums-rough/audio").status_code == 400


# ── demucs model ───────────────────────────────────────────────────────


def test_model_is_pinned_and_hash_checked():
    """one exact file from one exact commit, checked against its full sha256.
    (the torch version's filename didn't exist anywhere, so it could never
    download at all.)"""
    from core.sample import stems

    assert stems.MODEL_FILENAME == "htdemucs_fp16weights.onnx"
    assert len(stems.MODEL_SHA256) == 64
    assert all(stems._MODEL_COMMIT in u and u.endswith(stems.MODEL_FILENAME) for u in stems.MODEL_URLS)


def test_model_hash_must_match(tmp_path, monkeypatch):
    from core.sample import stems

    monkeypatch.setattr(stems, "models_dir", lambda: str(tmp_path))
    monkeypatch.setattr(stems, "_MIN_MODEL_BYTES", 10)
    monkeypatch.setattr(stems, "_model_verified", False)
    with open(stems.model_path(), "wb") as f:
        f.write(b"x" * 100)
    with pytest.raises(RuntimeError, match="checksum"):
        stems.ensure_model()
    monkeypatch.setattr(stems, "MODEL_SHA256", stems._sha256_file(stems.model_path()))
    assert stems.ensure_model() == stems.model_path()


def test_onnxruntime_checked_before_any_download(monkeypatch):
    """can't run it: fail on the import, never fetch 166 MB first."""
    import sys

    from core.sample import stems

    fetched = []
    monkeypatch.setattr(stems, "ensure_model", lambda: fetched.append(1))
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with pytest.raises(ImportError):
        stems._open_session()
    assert fetched == []


def test_session_is_built_lean(monkeypatch):
    """graph optimizations turn the fp16 weights into fp32 copies: one 7.8s
    chunk went past 4 GB with them on, ~1 GB with them off. pin the settings."""
    import sys
    import types

    from core.sample import stems

    made = {}

    class _Opts:
        pass

    class _Session:
        def __init__(self, path, sess_options=None, providers=None):
            made["opts"] = sess_options
            made["providers"] = providers

    fake = types.ModuleType("onnxruntime")
    fake.SessionOptions = _Opts
    fake.InferenceSession = _Session
    fake.GraphOptimizationLevel = types.SimpleNamespace(ORT_DISABLE_ALL="off", ORT_ENABLE_ALL="all")
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(stems, "ensure_model", lambda: "/m.onnx")
    stems._open_session()
    opts = made["opts"]
    assert opts.graph_optimization_level == "off"
    assert opts.enable_cpu_mem_arena is False and opts.enable_mem_pattern is False
    assert 1 <= opts.intra_op_num_threads <= max(1, (os.cpu_count() or 2))
    assert made["providers"] == ["CPUExecutionProvider"]


# ── the separation loop, with a fake model ─────────────────────────────


def _split_by_channel_model(chunk):
    """stand-in for htdemucs: drums = left, bass = right, other/vocals = 0.
    shape (1, 2, N) -> (1, 4, 2, N)."""
    x = chunk[0]
    out = np.zeros((1, 4, 2, x.shape[1]), np.float32)
    out[0, 0, 0] = x[0]
    out[0, 1, 1] = x[1]
    return out


def test_separate_array_round_trips_long_audio():
    """chunks + quarter overlap + crossfade + normalize/denormalize must hand
    the audio back exactly where it was, across many chunk boundaries."""
    from core.sample.stems import CHUNK, MODEL_SR, separate_array

    n = int(CHUNK * 3.4)
    t = np.arange(n) / MODEL_SR
    y = np.stack([0.4 * np.sin(2 * np.pi * 110 * t), 0.2 * np.sin(2 * np.pi * 330 * t) + 0.05], axis=1)
    seen = []
    stems, sr = separate_array(y.astype(np.float32), MODEL_SR, _split_by_channel_model, seen.append)
    assert sr == MODEL_SR
    assert set(stems) == {"drums", "bass", "other", "vocals"}
    # the fake model routes left -> drums, right -> bass. after crossfading
    # and undoing the normalization they must come back sample for sample
    assert stems["drums"].shape == (n, 2)
    assert np.abs(stems["drums"][:, 0] - y[:, 0]).max() < 1e-4
    assert np.abs(stems["bass"][:, 1] - y[:, 1]).max() < 1e-4
    assert seen[-1] == pytest.approx(1.0) and seen == sorted(seen) and len(seen) >= 4


def test_separate_array_keeps_the_first_and_last_samples():
    """the reference loop faded the first chunk in from exactly 0, so the song's
    opening samples came out silent. a single short chunk too."""
    from core.sample.stems import MODEL_SR, separate_array

    for n in (1000, 400000):
        y = np.full((n, 2), 0.3, np.float32)
        y[:, 1] = -0.2
        stems, _ = separate_array(y, MODEL_SR, _split_by_channel_model)
        assert stems["drums"][0, 0] == pytest.approx(0.3, abs=1e-4), n
        assert stems["drums"][-1, 0] == pytest.approx(0.3, abs=1e-4), n
        assert stems["bass"][0, 1] == pytest.approx(-0.2, abs=1e-4), n


def test_separate_array_resamples_and_upmixes_mono():
    from core.sample.stems import MODEL_SR, separate_array

    sr = 22050
    y = (0.3 * np.sin(2 * np.pi * 220 * np.arange(sr * 2) / sr)).astype(np.float32)
    stems, out_sr = separate_array(y, sr, _split_by_channel_model)
    assert out_sr == MODEL_SR
    assert stems["drums"].shape == (MODEL_SR * 2, 2)


def test_stems_worker_reports_progress(client, monkeypatch):
    """the page shows "Separating… 42%" from the status poll."""
    import threading

    from core.sample import stems as stems_mod
    from core.sample import stems_worker as sw

    c, _ = client
    gate = threading.Event()
    reached = threading.Event()

    class _Slow(stems_mod.StubSeparator):
        def separate(self, track_path, out_dir, progress=None):
            progress(0.42)
            reached.set()
            gate.wait(10)
            progress(1.0)
            return super().separate(track_path, out_dir)

    monkeypatch.setattr(stems_mod, "stems_available", lambda: True)
    monkeypatch.setattr(stems_mod, "get_separator", lambda method, backend=None: _Slow())
    assert c.post("/api/v1/sample/stems", json={"track_id": 1}).status_code == 202
    assert reached.wait(10)
    data = c.get("/api/v1/sample/stems/status", query_string={"track_id": 1}).get_json()["data"]
    assert data["status"] == "running" and data["progress"] == pytest.approx(0.42)
    gate.set()
    for _ in range(100):
        data = c.get("/api/v1/sample/stems/status", query_string={"track_id": 1}).get_json()["data"]
        if data["status"] == "done":
            break
        time.sleep(0.1)
    assert data["status"] == "done" and "progress" not in data
    assert sw.get_progress(1) is None


# ── unreachable files ──────────────────────────────────────────────────


def test_unreachable_file_explains_itself(client):
    import api.sample as sample_api
    from core.sample import store

    stored = "/mnt/navidrome-only/Artist/Album/01.flac"
    orig = store.get_track_file_path
    try:
        store.get_track_file_path = lambda track_id: stored
        with pytest.raises(sample_api.SampleHttpError) as exc_info:
            sample_api._resolve_source_path(1, None)
    finally:
        store.get_track_file_path = orig
    msg = exc_info.value.message
    assert exc_info.value.code == "FILE_MISSING"
    assert stored in msg and "media server" in msg and "Settings > Library" in msg
