"""Phase 4: Whisper local transcription.

Covers: model-name validation (the path-traversal gate), the double-gate
``is_configured()`` logic, no-auto-download (only the endpoint's
``start_download`` ever downloads), corrupt-download handling, provider
ordering (whisper last), quota exemption, worker transcription as a last
resort, upgrade-loop exclusion, import-hook deferral, and the
download/status API endpoints.

No network: the HuggingFace listing/download and faster-whisper are stubbed.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from core.video.subtitles.providers import whisper as wmod
from core.video.subtitles.providers.base import SubtitleQuery
from database.video_database import VideoDatabase


# ── fixtures / helpers ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_download_state():
    """The download state machine is module-global; reset it around every
    test so threaded downloads can't leak state across tests."""
    yield
    with wmod._download_lock:
        wmod._download_state.update(state="idle", model=None,
                                    downloaded_bytes=0, total_bytes=0,
                                    error=None)


@pytest.fixture()
def models_root(tmp_path):
    root = tmp_path / "whisper_models"
    root.mkdir()
    wmod.set_models_dir_override(root)
    yield root
    wmod.set_models_dir_override(None)


@pytest.fixture()
def engine(monkeypatch):
    """Pretend faster-whisper is installed."""
    monkeypatch.setattr(wmod, "transcription_engine", lambda: "faster-whisper")


def _fake_model(models_root, name="tiny"):
    d = models_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"fake-model-bytes")
    return d


def _gs(**kw):
    def get_setting(k, d=None):
        return kw.get(k, d)
    return get_setting


def _video_file(tmp_path, name="Movie.2024.1080p.WEB-GROUP.mkv", size=200_000):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return str(p)


class FakeFS:
    def __init__(self):
        self.texts = {}

    def list_dir(self, folder):
        return []

    def write_text(self, path, content):
        self.texts[path] = content


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _dl(db, dest_path, **kw):
    d = {"kind": "movie", "title": "T", "status": "completed",
         "media_id": "603", "media_source": "tmdb", "search_ctx": "{}"}
    d.update(kw)
    dl_id = db.add_video_download(d)
    db.update_video_download(dl_id, dest_path=dest_path)
    return dl_id


# ── model-name validation (path-traversal gate) ────────────────────────────

def test_valid_model_name_allowlist():
    assert wmod.valid_model_name("small") == "small"
    assert wmod.valid_model_name(" SMALL ") == "small"
    assert wmod.valid_model_name("Tiny") == "tiny"
    assert wmod.valid_model_name("base") == "base"


def test_valid_model_name_rejects_traversal_and_garbage():
    assert wmod.valid_model_name("../../etc") is None
    assert wmod.valid_model_name("small/../../x") is None
    assert wmod.valid_model_name("/abs/path") is None
    assert wmod.valid_model_name("medium") is None
    assert wmod.valid_model_name("") is None
    assert wmod.valid_model_name(None) is None
    assert wmod.valid_model_name(123) is None


def test_model_dir_rejects_bad_name(models_root):
    with pytest.raises(ValueError):
        wmod.model_dir("../../etc")
    with pytest.raises(ValueError):
        wmod.model_file_path("nope")


def test_model_paths_stay_under_root(models_root):
    p = wmod.model_file_path("small")
    assert p.parent.parent == models_root
    assert p.name == "model.bin"


def test_safe_target_rejects_escape(models_root):
    with pytest.raises(RuntimeError):
        wmod._safe_target(models_root / "small", "../evil.bin")
    with pytest.raises(RuntimeError):
        wmod._safe_target(models_root / "small", "/abs/evil.bin")
    ok = wmod._safe_target(models_root / "small", "model.bin")
    assert ok.parent == models_root / "small"


# ── double-gate is_configured ──────────────────────────────────────────────

def _provider():
    return wmod.WhisperProvider()


def test_gate_disabled(models_root, engine):
    _fake_model(models_root)
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=False, whisper_model="tiny")) is False


def test_gate_no_model_file(models_root, engine):
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="tiny")) is False


def test_gate_no_engine(models_root, monkeypatch):
    _fake_model(models_root)
    monkeypatch.setattr(wmod, "transcription_engine", lambda: None)
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="tiny")) is False


def test_gate_bad_model_name(models_root, engine):
    _fake_model(models_root)
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="../../x")) is False


def test_gate_configured(models_root, engine):
    _fake_model(models_root)
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="tiny")) is True


def test_gate_never_assumes_file_still_there(models_root, engine):
    d = _fake_model(models_root)
    p = _provider()
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    assert p.is_configured(gs) is True
    (d / "model.bin").unlink()  # deleted out from under it
    assert p.is_configured(gs) is False


def test_provider_flags():
    p = _provider()
    assert p.needs_key is False
    assert p.burns_network_quota is False
    assert p.background_download_only is True
    assert p.id == "whisper"


# ── no-auto-download: only start_download() downloads ──────────────────────

def test_nothing_but_start_download_downloads(models_root, engine, monkeypatch, tmp_path):
    """is_configured / search / download / status / engine-check must never
    touch the network — only the endpoint-called start_download() may."""
    _fake_model(models_root)
    video = _video_file(tmp_path)

    def _nope(*a, **k):
        raise AssertionError("network download triggered outside start_download")

    monkeypatch.setattr(wmod, "_repo_tree", _nope)
    monkeypatch.setattr(wmod, "_download_one", _nope)
    monkeypatch.setattr(wmod, "download_model", _nope)
    monkeypatch.setattr(wmod, "transcribe_to_srt", lambda *a, **k: "1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    gs = _gs(whisper_enabled=True, whisper_model="tiny",
             whisper_model_downloaded=True)
    p = _provider()
    assert p.is_configured(gs) is True  # no download
    cands = p.search(SubtitleQuery(language="en", file_path=video))
    assert len(cands) == 1  # no download
    assert p.download(cands[0]) is not None  # transcribes, does not download
    payload = wmod.status_payload(gs)  # no download
    assert payload["downloaded"] is True  # flag (set) + file (on disk)
    assert payload["disk_model"] == "tiny"
    assert wmod.transcription_engine() == "faster-whisper"  # no download


# ── download mechanics (stubbed network) ───────────────────────────────────

def _stub_net(monkeypatch, files=(("model.bin", 8), ("config.json", 4))):
    def _tree(repo):
        return list(files)

    def _one(url, tmp_path, expected_size, on_chunk=None):
        tmp_path.write_bytes(b"x" * expected_size)
        if callable(on_chunk):
            on_chunk(expected_size)

    monkeypatch.setattr(wmod, "_repo_tree", _tree)
    monkeypatch.setattr(wmod, "_download_one", _one)


def test_download_model_writes_and_verifies(models_root, monkeypatch):
    _stub_net(monkeypatch)
    info = wmod.download_model("tiny")
    assert info["name"] == "tiny"
    assert info["size_bytes"] == 12
    assert info["downloaded_at"]
    assert (models_root / "tiny" / "model.bin").read_bytes() == b"x" * 8
    assert (models_root / "tiny" / "config.json").read_bytes() == b"x" * 4
    # no .part temp files left behind
    assert list((models_root / "tiny").glob("*.part")) == []


def test_download_model_bad_name_raises(models_root):
    with pytest.raises(ValueError):
        wmod.download_model("../../etc")


def test_download_model_size_mismatch_is_corrupt(models_root, monkeypatch):
    def _tree(repo):
        return [("model.bin", 8)]

    def _one(url, tmp_path, expected_size, on_chunk=None):
        tmp_path.write_bytes(b"short")  # 5 bytes, not 8

    monkeypatch.setattr(wmod, "_repo_tree", _tree)
    # emulate the real _download_one's size verification (the stub writes short)
    def _verifying(url, tmp_path, expected_size, on_chunk=None):
        _one(url, tmp_path, expected_size, on_chunk)
        actual = os.path.getsize(tmp_path)
        if actual != expected_size:
            raise RuntimeError("size mismatch")

    monkeypatch.setattr(wmod, "_download_one", _verifying)
    with pytest.raises(RuntimeError):
        wmod.download_model("tiny")
    # no half-model left for is_configured() to trust
    assert not (models_root / "tiny" / "model.bin").exists()
    assert wmod.model_file_exists("tiny") is False


def test_download_model_skips_matching_files(models_root, monkeypatch):
    _stub_net(monkeypatch)
    wmod.download_model("tiny")
    calls = []

    def _counting(url, tmp_path, expected_size, on_chunk=None):
        calls.append(url)
        tmp_path.write_bytes(b"x" * expected_size)

    monkeypatch.setattr(wmod, "_download_one", _counting)
    wmod.download_model("tiny")  # second run: everything already verified
    assert calls == []


def test_start_download_rejects_bad_name(models_root):
    ok, reason = wmod.start_download("../../etc")
    assert ok is False and reason


def test_start_download_success_and_state(models_root, monkeypatch):
    _stub_net(monkeypatch)
    done = {}

    def _on_complete(info, error):
        done["info"] = info
        done["error"] = error

    ok, reason = wmod.start_download("tiny", on_complete=_on_complete)
    assert ok is True, reason
    deadline = time.monotonic() + 10
    while wmod.get_download_status()["state"] == "downloading" \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    st = wmod.get_download_status()
    assert st["state"] == "done", st
    assert st["model"] == "tiny"
    assert done["error"] is None and done["info"]["name"] == "tiny"
    assert wmod.model_file_exists("tiny") is True


def test_start_download_refuses_concurrent(models_root, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def _slow(name, on_progress=None):
        started.set()
        assert release.wait(timeout=10)
        return {"name": name, "path": "p", "size_bytes": 1,
                "downloaded_at": "t"}

    monkeypatch.setattr(wmod, "download_model", _slow)
    ok, _ = wmod.start_download("tiny")
    assert ok is True
    assert started.wait(timeout=10)
    ok2, reason2 = wmod.start_download("base")
    assert ok2 is False and "already in progress" in reason2
    release.set()
    deadline = time.monotonic() + 10
    while wmod.get_download_status()["state"] == "downloading" \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    assert wmod.get_download_status()["state"] == "done"


def test_start_download_failure_state(models_root, monkeypatch):
    def _boom(name, on_progress=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(wmod, "download_model", _boom)
    seen = {}

    def _on_complete(info, error):
        seen["info"] = info
        seen["error"] = error

    ok, _ = wmod.start_download("tiny", on_complete=_on_complete)
    assert ok is True
    deadline = time.monotonic() + 10
    while wmod.get_download_status()["state"] == "downloading" \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    st = wmod.get_download_status()
    assert st["state"] == "error"
    assert "network down" in (st["error"] or "")
    assert seen["info"] is None and isinstance(seen["error"], RuntimeError)


def test_start_download_replaces_previous_model(models_root, monkeypatch):
    _stub_net(monkeypatch)
    (models_root / "tiny").mkdir()
    (models_root / "tiny" / "model.bin").write_bytes(b"old")
    (models_root / "rogue").mkdir()  # not allowlisted: never touched
    (models_root / "rogue" / "x").write_bytes(b"x")
    ok, _ = wmod.start_download("small")
    assert ok is True
    deadline = time.monotonic() + 10
    while wmod.get_download_status()["state"] == "downloading" \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    assert wmod.get_download_status()["state"] == "done"
    assert not (models_root / "tiny").exists()  # replaced
    assert (models_root / "rogue" / "x").exists()  # untouched
    assert wmod.model_file_exists("small") is True


# ── search / download ──────────────────────────────────────────────────────

def test_search_needs_a_real_file(models_root, engine):
    _fake_model(models_root)
    p = _provider()
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    assert p.is_configured(gs) is True
    assert p.search(SubtitleQuery(language="en")) == []  # no file_path
    assert p.search(SubtitleQuery(language="en",
                                  file_path="/no/such/file.mkv")) == []


def test_search_candidate_shape(models_root, engine, tmp_path):
    _fake_model(models_root)
    video = _video_file(tmp_path)
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="tiny"))
    (cands) = p.search(SubtitleQuery(language="en", file_path=video))
    assert len(cands) == 1
    c = cands[0]
    assert c.provider_id == "whisper"
    assert c.hash_match is True  # generated from this exact file
    assert c.download_ref["file_path"] == video
    assert c.download_ref["model"] == "tiny"


def test_search_unconfigured_is_empty(models_root, engine, tmp_path):
    video = _video_file(tmp_path)
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=False)) is False
    assert p.search(SubtitleQuery(language="en", file_path=video)) == []


def test_download_rejects_stale_model_ref(models_root, engine, monkeypatch, tmp_path):
    _fake_model(models_root)
    _fake_model(models_root, "small")
    video = _video_file(tmp_path)
    monkeypatch.setattr(wmod, "transcribe_to_srt",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not transcribe")))
    p = _provider()
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="tiny"))
    cands = p.search(SubtitleQuery(language="en", file_path=video))
    # user switches model between search and download
    assert p.is_configured(_gs(whisper_enabled=True, whisper_model="small"))
    assert p.download(cands[0]) is None


def test_segments_to_srt():
    class _Seg:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    out = wmod.segments_to_srt([_Seg(0, 1.5, "hello"), _Seg(61.25, 62, "  ")])
    assert out == ("1\n00:00:00,000 --> 00:00:01,500\nhello\n")
    assert wmod.segments_to_srt([]) == ""
    assert wmod._srt_timestamp(3723.456) == "01:02:03,456"


# ── provider registry: ordering, quota, chain behavior ─────────────────────

def test_whisper_registered_last():
    from core.video.subtitles.providers import get_providers
    ids = list(get_providers())
    assert ids[-1] == "whisper"


def test_chain_pins_whisper_last(models_root, engine):
    from core.video.subtitles.providers import iter_configured_providers
    _fake_model(models_root)
    gs = _gs(opensubtitles_api_key="k", whisper_enabled=True,
             whisper_model="tiny")
    # stored order names whisper first: it still runs last
    ids = [pid for pid, _ in iter_configured_providers(
        ["whisper", "opensubtitles"], gs)]
    assert ids == ["opensubtitles", "whisper"]
    # stored order predates whisper: it is appended when configured
    ids = [pid for pid, _ in iter_configured_providers(["opensubtitles"], gs)]
    assert ids == ["opensubtitles", "whisper"]


def test_chain_skip_background(models_root, engine):
    from core.video.subtitles.providers import iter_configured_providers
    _fake_model(models_root)
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    ids = [pid for pid, _ in iter_configured_providers(
        ["opensubtitles"], gs, skip_background=True)]
    assert "whisper" not in ids


def test_rank_candidates_excludes_whisper(models_root, engine, tmp_path):
    from core.video.subtitles.providers import rank_candidates
    _fake_model(models_root)
    video = _video_file(tmp_path)
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    q = SubtitleQuery(language="en", filename="Movie.mkv", file_path=video)
    assert rank_candidates(q, ["opensubtitles"], gs) == []


def test_provider_is_quota_exempt():
    from core.video.subtitles.providers import provider_is_quota_exempt
    assert provider_is_quota_exempt("whisper") is True
    assert provider_is_quota_exempt("opensubtitles") is False
    assert provider_is_quota_exempt("no-such-provider") is False


def test_fetch_chain_uses_whisper_as_final_fallback(models_root, engine,
                                                    monkeypatch, tmp_path):
    from core.video.subtitles.providers import fetch_subtitle_detailed
    _fake_model(models_root)
    video = _video_file(tmp_path)
    monkeypatch.setattr(wmod, "transcribe_to_srt",
                        lambda name, path, lang=None: "1\n00:00:00,000 --> 00:00:01,000\nx\n")
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    q = SubtitleQuery(language="en", filename="Movie.mkv", file_path=video)
    seen = []
    text, pid, cand, score = fetch_subtitle_detailed(
        q, ["opensubtitles"], gs, on_download=seen.append)
    assert pid == "whisper"
    assert text.startswith("1\n")
    assert seen == ["whisper"]  # on_download fires; bump skips it (below)
    assert score >= 100.0  # hash_match clears the gate


def test_fetch_chain_skip_background_never_transcribes(models_root, engine,
                                                       monkeypatch, tmp_path):
    from core.video.subtitles.providers import fetch_subtitle_detailed
    _fake_model(models_root)
    video = _video_file(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("transcription must not run with skip_background")

    monkeypatch.setattr(wmod, "transcribe_to_srt", _boom)
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    q = SubtitleQuery(language="en", filename="Movie.mkv", file_path=video)
    text, pid, _c, _s = fetch_subtitle_detailed(q, ["opensubtitles"], gs,
                                                skip_background=True)
    assert text is None and pid is None


# ── worker: last-resort transcription, quota exemption ─────────────────────

def _worker_settings(**kw):
    s = {"download_subtitles": True,
         "subtitle_provider_order": '["opensubtitles"]',
         "subtitle_worker_batch": 10, "subtitle_daily_quota": 20,
         "subtitle_min_score": 24.0,
         "whisper_enabled": True, "whisper_model": "tiny"}
    s.update(kw)
    return s


def test_worker_transcribes_as_last_resort(db, tmp_path, models_root, engine,
                                           monkeypatch):
    from core.video.subtitles import worker
    _fake_model(models_root)
    video = _video_file(tmp_path)
    dl_id = _dl(db, video)
    db.subtitle_want("download", dl_id, "en")
    monkeypatch.setattr(wmod, "transcribe_to_srt",
                        lambda name, path, lang=None: "1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    fs = FakeFS()
    stats = worker.run_cycle(db, _worker_settings(), fs)
    assert stats["downloaded"] == 1
    assert db.subtitle_have("download", dl_id, "en")
    srt_path = os.path.join(str(tmp_path), "Movie.2024.1080p.WEB-GROUP.en.srt")
    assert fs.texts[srt_path].startswith("1\n")
    # quota exemption: the transcription burned no network quota…
    assert db.subtitle_quota_used("whisper") == 0
    # …and history truthfully records the provider
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["provider"] == "whisper"


def test_worker_quota_ok_not_blocked_by_exhausted_network_quota(
        db, models_root, engine):
    from core.video.subtitles import worker
    from core.video import organization
    _fake_model(models_root)
    gs = _gs(whisper_enabled=True, whisper_model="tiny")
    # opensubtitles configured but its quota fully spent…
    db.subtitle_quota_bump("opensubtitles")
    assert worker.quota_ok(db, ["opensubtitles"],
                            _gs(opensubtitles_api_key="k"), 1) is False
    # …yet a configured whisper keeps the cycle alive.
    assert worker.quota_ok(db, ["opensubtitles"], gs, 1) is True


def test_upgrade_loop_never_transcribes(db, tmp_path, models_root, engine,
                                        monkeypatch):
    from core.video.subtitles import worker
    # first get a whisper subtitle via the normal cycle…
    _fake_model(models_root)
    video = _video_file(tmp_path)
    dl_id = _dl(db, video)
    db.subtitle_want("download", dl_id, "en")
    monkeypatch.setattr(wmod, "transcribe_to_srt",
                        lambda name, path, lang=None: "1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    worker.run_cycle(db, _worker_settings(), FakeFS())
    assert db.subtitle_have("download", dl_id, "en")
    # …then prove the upgrade pass never re-transcribes: a generated
    # transcript must not displace an existing subtitle.
    def _boom(*a, **k):
        raise AssertionError("upgrade must not transcribe")

    monkeypatch.setattr(wmod, "transcribe_to_srt", _boom)
    stats = worker.run_upgrade_cycle(db, _worker_settings(), FakeFS())
    assert stats["upgraded"] == 0


# ── import hook: defers whisper to the worker ──────────────────────────────

def test_import_hook_defers_whisper_to_worker(db, tmp_path, models_root, engine,
                                              monkeypatch):
    from core.video import organization
    from core.video.download_monitor import write_subtitles_for
    _fake_model(models_root)
    video = _video_file(tmp_path)
    dl_id = _dl(db, video)
    organization.save(db, {"subtitle_provider_order": '["opensubtitles"]',
                           "subtitle_langs": "en",
                           "whisper_enabled": True, "whisper_model": "tiny",
                           "subtitle_min_score": 24.0})

    def _boom(*a, **k):
        raise AssertionError("hook must not transcribe inline")

    monkeypatch.setattr(wmod, "transcribe_to_srt", _boom)
    fs = FakeFS()
    dl = {"id": dl_id, "kind": "movie", "media_id": "603",
          "media_source": "tmdb", "search_ctx": "{}"}
    settings = organization.load(db)
    write_subtitles_for(db, dl, video, settings, fs)
    # no transcription ran inline…
    assert fs.texts == {}
    # …and the row stays 'wanted' with attempts untouched, so the worker
    # picks it up WITHOUT backoff delay.
    rows = db.subtitle_get_for_video("download", dl_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "wanted" and int(rows[0]["attempts"]) == 0


# ── API: status + download endpoints ───────────────────────────────────────

@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    from flask import Flask, g
    videoapi._video_db = VideoDatabase(
        database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        g.is_admin = True
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(),
                           url_prefix="/api/video")
    return app.test_client()


@pytest.fixture()
def client_non_admin(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    from flask import Flask, g
    videoapi._video_db = VideoDatabase(
        database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        g.is_admin = False
        g.can_download = False

    app.register_blueprint(videoapi.create_video_blueprint(),
                           url_prefix="/api/video")
    return app.test_client()


def test_whisper_status_endpoint(client, models_root, engine):
    _fake_model(models_root)
    r = client.get("/api/video/subtitles/whisper/status")
    assert r.status_code == 200
    d = r.get_json()
    assert d["model"] == "small"  # default
    assert d["downloaded"] is False  # flag not set
    assert d["disk_model"] == "tiny"
    assert d["engine"] == "faster-whisper"
    assert set(d["models"]) == {"tiny", "base", "small"}
    assert d["download"]["state"] in ("idle", "done", "error")


def test_whisper_download_rejects_bad_model(client):
    r = client.post("/api/video/subtitles/whisper/download",
                    json={"model": "../../etc"})
    assert r.status_code == 400
    r = client.post("/api/video/subtitles/whisper/download", json={})
    assert r.status_code == 400


def test_whisper_download_requires_admin(client_non_admin):
    r = client_non_admin.post("/api/video/subtitles/whisper/download",
                              json={"model": "tiny"})
    assert r.status_code == 403


def test_whisper_download_flow_persists_state(client, models_root, monkeypatch):
    from core.video import organization
    import api.video as videoapi

    def _fake_dl(name, on_progress=None):
        d = models_root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.bin").write_bytes(b"x" * 10)
        return {"name": name, "path": str(d / "model.bin"),
                "size_bytes": 10, "downloaded_at": "2026-10-10T00:00:00+00:00"}

    monkeypatch.setattr(wmod, "download_model", _fake_dl)
    r = client.post("/api/video/subtitles/whisper/download",
                    json={"model": "tiny"})
    assert r.status_code == 202
    assert r.get_json()["model"] == "tiny"
    # progress visible while running
    r = client.get("/api/video/subtitles/whisper/status")
    assert r.get_json()["download"]["state"] in ("downloading", "done")
    # wait for the worker thread, then the blob must carry the state
    deadline = time.monotonic() + 10
    while wmod.get_download_status()["state"] == "downloading" \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    st = wmod.get_download_status()
    assert st["state"] == "done", st
    saved = organization.load(videoapi._video_db)
    assert saved["whisper_model"] == "tiny"
    assert saved["whisper_model_downloaded"] is True
    assert saved["whisper_model_path"].endswith("model.bin")
    assert saved["whisper_model_size_bytes"] == 10
    assert saved["whisper_model_downloaded_at"]


def test_whisper_download_409_while_running(client, monkeypatch):
    release = threading.Event()

    def _slow(name, on_progress=None):
        assert release.wait(timeout=10)
        return {"name": name, "path": "p", "size_bytes": 1,
                "downloaded_at": "t"}

    monkeypatch.setattr(wmod, "download_model", _slow)
    try:
        r = client.post("/api/video/subtitles/whisper/download",
                        json={"model": "tiny"})
        assert r.status_code == 202
        r = client.post("/api/video/subtitles/whisper/download",
                        json={"model": "base"})
        assert r.status_code == 409
    finally:
        release.set()


# ── organization settings ──────────────────────────────────────────────────

def test_organization_whisper_defaults_and_normalize():
    from core.video import organization
    d = organization.default_settings()
    assert d["whisper_enabled"] is False
    assert d["whisper_model"] == "small"
    assert d["whisper_model_downloaded"] is False
    n = organization.normalize({"whisper_enabled": True,
                                "whisper_model": "TINY",
                                "whisper_model_downloaded": True,
                                "whisper_model_path": "/x/model.bin",
                                "whisper_model_size_bytes": "12",
                                "whisper_model_downloaded_at": "2026-10-10"})
    assert n["whisper_enabled"] is True
    assert n["whisper_model"] == "tiny"
    assert n["whisper_model_downloaded"] is True
    assert n["whisper_model_path"] == "/x/model.bin"
    assert n["whisper_model_size_bytes"] == 12
    assert n["whisper_model_downloaded_at"] == "2026-10-10"
    # a bad model name keeps the default — never flows anywhere
    n = organization.normalize({"whisper_model": "../../etc"})
    assert n["whisper_model"] == "small"


# ── settings UI carries the section ────────────────────────────────────────

def test_settings_ui_has_whisper_section():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui/index.html").read_text(encoding="utf-8")
    js = (root / "webui/static/video/video-settings.js").read_text(
        encoding="utf-8")
    for el in ("vo-whisper-model", "vo-whisper-dl", "vo-whisper-enabled",
               "vo-whisper-status", "vo-whisper-progress"):
        assert ('id="%s"' % el) in html, el
    for fn in ("loadWhisperStatus", "renderWhisperStatus",
               "startWhisperDownload", "whisper_enabled", "whisper_model"):
        assert fn in js, fn


# ── language guard (oct 14 review) ───────────────────────────────────────────
def _fake_faster_whisper(monkeypatch, tmp_path, heard):
    import sys
    import types
    from core.video.subtitles.providers import whisper as wm
    calls = []

    class Seg:
        def __init__(self, text):
            self.start, self.end, self.text = 0.0, 1.0, text

    class Info:
        language = heard

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            calls.append(kw)
            task = kw.get("task", "transcribe")
            return iter([Seg("%s:%s" % (task, kw.get("language")))]), Info()

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(wm, "transcription_engine", lambda: "faster-whisper")
    wm.set_models_dir_override(tmp_path)
    (tmp_path / "tiny").mkdir()
    (tmp_path / "tiny" / "model.bin").write_bytes(b"x")
    return wm, calls


def test_transcribe_same_language_transcribes(monkeypatch, tmp_path):
    wm, calls = _fake_faster_whisper(monkeypatch, tmp_path, "en")
    try:
        out = wm.transcribe_to_srt("tiny", str(tmp_path / "v.mkv"), "en")
    finally:
        wm.set_models_dir_override(None)
    assert "transcribe:None" in out and len(calls) == 1


def test_transcribe_foreign_audio_translates_into_english(monkeypatch, tmp_path):
    wm, calls = _fake_faster_whisper(monkeypatch, tmp_path, "ja")
    try:
        out = wm.transcribe_to_srt("tiny", str(tmp_path / "v.mkv"), "en")
    finally:
        wm.set_models_dir_override(None)
    assert "translate:ja" in out


def test_transcribe_never_fakes_a_non_english_target(monkeypatch, tmp_path):
    # english audio, spanish wanted: whisper can't translate into spanish, so
    # this must be a miss, not minutes of junk labeled .es.srt
    wm, calls = _fake_faster_whisper(monkeypatch, tmp_path, "en")
    try:
        out = wm.transcribe_to_srt("tiny", str(tmp_path / "v.mkv"), "es")
    finally:
        wm.set_models_dir_override(None)
    assert out is None
    assert all(c.get("task") != "translate" for c in calls)


def test_transcribe_regional_code_matches_base_language(monkeypatch, tmp_path):
    wm, _calls = _fake_faster_whisper(monkeypatch, tmp_path, "pt")
    try:
        out = wm.transcribe_to_srt("tiny", str(tmp_path / "v.mkv"), "pt-br")
    finally:
        wm.set_models_dir_override(None)
    assert out and "transcribe" in out
