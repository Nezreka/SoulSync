"""Song identification routes: readiness, bounded work, JSON failures, cleanup.

The browser has already opened a microphone sheet by the time these routes run,
so every failure below the view has to be a JSON answer, and every path that
holds the recognition slot or an event loop has to let go of it. These pin:

  * readiness names its exact runtime dependencies;
  * ffmpeg timeout / decode refusal / operational error each get their own
    status and JSON code;
  * Shazam is bounded by RECOGNIZE_TIMEOUT_S and its session is closed on both
    the timed-out and failed paths;
  * the two recognition slots are released after every failure;
  * configure() accepts only the dependencies the module declares.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
import types
from io import BytesIO
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage

import api.listen_routes as listen

_ROOT = Path(__file__).resolve().parents[1]


def test_web_server_registers_the_blueprint_with_live_deps():
    """The feature is inert if the blueprint is not mounted by the real app."""
    source = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="replace")
    assert "from api.listen_routes import configure as _configure_listen_api" in source
    assert "build_search_deps=_build_search_deps" in source
    assert "library_check=_search_library_check" in source
    assert "app.register_blueprint(_create_listen_blueprint())" in source


def test_the_button_lives_in_the_react_search_bar():
    """note sonar is part of the search field now: no sheet or script of its own on the page."""
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
    assert "note-sonar.css" not in index
    assert "note-sonar.js" not in index
    bar = (_ROOT / "webui" / "src" / "routes" / "search" / "-ui" / "search-bar.tsx").read_text(
        encoding="utf-8"
    )
    assert "<NoteSonar" in bar
    assert (_ROOT / "webui" / "static" / "note-sonar.png").is_file()


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setattr(listen, "_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(listen, "_engine_ok", None)
    monkeypatch.setattr(listen, "_engine_reason", "")
    listen.configure(
        get_database=None,
        build_search_deps=None,
        library_check=None,
        media_server_engine=None,
        config_manager=None,
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(listen.create_blueprint())
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _clip(data: bytes = b"not really audio") -> dict:
    return {"audio": (BytesIO(data), "clip.webm")}


def _raise(exc):
    def _inner(*_args, **_kwargs):
        raise exc
    return _inner


def _ok_engine(monkeypatch):
    monkeypatch.setattr(listen, "_engine", lambda: {"ok": True, "reason": ""})


# ── readiness ────────────────────────────────────────────────────────────────

def test_status_names_the_exact_dependencies(client, monkeypatch):
    monkeypatch.setattr(listen, "_engine", lambda: {"ok": True, "reason": ""})
    response = client.get("/api/listen/status")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["available"] is True
    assert body["dependencies"] == ["ffmpeg", "shazamio"]


def test_status_reports_why_the_engine_cannot_run(client, monkeypatch):
    monkeypatch.setattr(listen, "_engine", lambda: {
        "ok": False, "reason": "shazamio is not installed or could not be imported",
    })
    body = client.get("/api/listen/status").get_json()

    assert body["available"] is False
    assert "shazamio" in body["reason"]


def test_engine_accepts_dependencies_that_are_present(app, monkeypatch):
    monkeypatch.setattr(listen.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(listen, "_module_available", lambda _name: True)

    assert listen._engine() == {"ok": True, "reason": ""}


def test_engine_names_a_missing_ffmpeg(app, monkeypatch):
    monkeypatch.setattr(listen.shutil, "which", lambda _name: None)
    monkeypatch.setattr(listen, "_module_available", lambda _name: True)

    result = listen._engine()

    assert result["ok"] is False
    assert "ffmpeg" in result["reason"]


# ── dependency injection surface ─────────────────────────────────────────────

def test_configure_refuses_an_undeclared_dependency():
    with pytest.raises(KeyError) as caught:
        listen.configure(media_server_engine=lambda: None, surprise=object())
    assert "surprise" in str(caught.value)


def test_configure_accepts_only_the_declared_names():
    listen.configure(
        get_database=lambda: None,
        build_search_deps=lambda: None,
        library_check=object(),
        media_server_engine=lambda: None,
        config_manager=object(),
    )
    assert callable(listen.get_database)
    assert listen.library_check is not None


# ── route failures stay JSON, and release the recognition slot ───────────────

def test_ffmpeg_timeout_is_a_json_504(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", _raise(listen.ClipTimeout("too slow")))

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 504
    assert response.get_json() == {
        "success": False,
        "error": "decode_timeout",
        "message": "That clip took too long to decode.",
    }


def test_undecodable_clip_is_a_json_400(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", _raise(listen.ClipUndecodable("no stream")))

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 400
    assert response.get_json()["error"] == "undecodable"


def test_ffmpeg_workspace_error_is_a_json_500(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", _raise(listen.ClipUnreadable("disk full")))

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 500
    assert response.get_json()["error"] == "clip_unreadable"


def test_upload_save_error_is_a_json_500(client, monkeypatch):
    _ok_engine(monkeypatch)

    def _fail_save(self, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(FileStorage, "save", _fail_save)

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 500
    assert response.get_json()["error"] == "clip_unreadable"


def test_recogniser_timeout_is_a_json_504(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", lambda _src, _dst: None)
    monkeypatch.setattr(listen, "_recognize", _raise(listen.RecognizerTimeout("slow")))

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 504
    assert response.get_json()["error"] == "recogniser_timeout"


def test_recogniser_failure_is_a_json_502(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", lambda _src, _dst: None)
    monkeypatch.setattr(listen, "_recognize", _raise(listen.RecognizerError("unreachable")))

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 502
    assert response.get_json()["error"] == "recogniser_failed"


def test_unexpected_recogniser_error_is_still_json(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", lambda _src, _dst: None)

    def _explode(_wav):
        raise ValueError("unexpected")

    monkeypatch.setattr(listen, "_recognize", _explode)

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 500
    assert response.get_json()["error"] == "internal_error"


def test_every_failure_returns_its_slots(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", _raise(listen.ClipTimeout("too slow")))

    for _ in range(3):
        response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")
        assert response.status_code == 504, "a leaked slot turned the third request into 429"


def test_missing_audio_and_oversize_are_json(client, monkeypatch):
    _ok_engine(monkeypatch)

    assert client.post("/api/listen/identify", data={}, content_type="multipart/form-data").get_json()["error"] == "no_audio"

    monkeypatch.setattr(listen, "MAX_UPLOAD_BYTES", 4)
    response = client.post("/api/listen/identify", data=_clip(b"x" * 32), content_type="multipart/form-data")
    assert response.status_code == 413
    assert response.get_json()["error"] == "too_large"


def test_busy_is_json_429_and_does_not_consume_a_slot(client, monkeypatch):
    _ok_engine(monkeypatch)
    assert listen._slots.acquire(blocking=False)
    assert listen._slots.acquire(blocking=False)
    try:
        response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")
    finally:
        listen._slots.release()
        listen._slots.release()

    assert response.status_code == 429
    assert response.get_json()["error"] == "busy"


# ── success shape ────────────────────────────────────────────────────────────

def test_identification_is_resolved_and_returned(client, monkeypatch):
    _ok_engine(monkeypatch)
    monkeypatch.setattr(listen, "_to_wav", lambda _src, _dst: None)
    monkeypatch.setattr(listen, "_recognize", lambda _wav: {
        "track": {
            "title": "Nightcall",
            "subtitle": "Kavinsky",
            "key": "123",
            "sections": [{"metadata": [{"title": "Album", "text": "OutRun"}]}],
            "images": {"coverart": "https://example.invalid/cover.jpg"},
        }
    })
    row = {"id": "sp-1", "name": "Nightcall", "artist": "Kavinsky"}
    monkeypatch.setattr(listen, "_resolve", lambda _ident: row)
    monkeypatch.setattr(listen, "_in_library", lambda _row: True)

    response = client.post("/api/listen/identify", data=_clip(), content_type="multipart/form-data")

    assert response.status_code == 200
    body = response.get_json()
    assert body["identification"]["title"] == "Nightcall"
    assert body["identification"]["album"] == "OutRun"
    assert body["match"] == row
    assert body["in_library"] is True
    assert body["query"] == "Kavinsky Nightcall"


# ── helpers: typing and bounds ───────────────────────────────────────────────

def test_to_wav_maps_subprocess_timeout():
    original = subprocess.run
    subprocess.run = _raise(subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60))
    try:
        with pytest.raises(listen.ClipTimeout):
            listen._to_wav(Path("in.webm"), Path("out.wav"))
    finally:
        subprocess.run = original


def test_to_wav_maps_decode_failure():
    original = subprocess.run
    subprocess.run = _raise(subprocess.CalledProcessError(1, "ffmpeg", b"", b"invalid data"))
    try:
        with pytest.raises(listen.ClipUndecodable):
            listen._to_wav(Path("in.webm"), Path("out.wav"))
    finally:
        subprocess.run = original


def test_to_wav_maps_missing_ffmpeg():
    original = subprocess.run
    subprocess.run = _raise(FileNotFoundError("ffmpeg"))
    try:
        with pytest.raises(listen.EngineMissing):
            listen._to_wav(Path("in.webm"), Path("out.wav"))
    finally:
        subprocess.run = original


class _SlowShazam:
    instances = []

    def __init__(self):
        self.closed = False
        _SlowShazam.instances.append(self)

    async def recognize(self, _path):
        await asyncio.sleep(30)
        return {}

    async def close(self):
        self.closed = True


class _BrokenShazam:
    instances = []

    def __init__(self):
        self.closed = False
        _BrokenShazam.instances.append(self)

    async def recognize(self, _path):
        raise RuntimeError("far end closed the connection")

    async def close(self):
        self.closed = True


class _UnconstructableShazam:
    def __init__(self):
        raise RuntimeError("no aiohttp session")


def test_recognize_construction_failure_is_typed(monkeypatch):
    monkeypatch.setitem(sys.modules, "shazamio",
                        types.SimpleNamespace(Shazam=_UnconstructableShazam))
    with pytest.raises(listen.RecognizerError):
        listen._recognize(Path("clip.wav"))


def test_recognize_is_bounded_and_closes_the_session(monkeypatch):
    _SlowShazam.instances.clear()
    monkeypatch.setitem(sys.modules, "shazamio", types.SimpleNamespace(Shazam=_SlowShazam))
    monkeypatch.setattr(listen, "RECOGNIZE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(listen, "RECOGNIZE_CLOSE_TIMEOUT_S", 1)

    started = time.monotonic()
    with pytest.raises(listen.RecognizerTimeout):
        listen._recognize(Path("clip.wav"))
    elapsed = time.monotonic() - started

    assert elapsed < 2, "the recogniser was not bounded"
    assert _SlowShazam.instances and _SlowShazam.instances[0].closed, "Shazam was not closed"


def test_recognize_failure_still_closes_the_session(monkeypatch):
    _BrokenShazam.instances.clear()
    monkeypatch.setitem(sys.modules, "shazamio", types.SimpleNamespace(Shazam=_BrokenShazam))
    monkeypatch.setattr(listen, "RECOGNIZE_TIMEOUT_S", 1)
    monkeypatch.setattr(listen, "RECOGNIZE_CLOSE_TIMEOUT_S", 1)

    with pytest.raises(listen.RecognizerError):
        listen._recognize(Path("clip.wav"))

    assert _BrokenShazam.instances and _BrokenShazam.instances[0].closed, "Shazam was not closed"


def test_a_clip_with_no_declared_size_is_refused_before_it_is_read(client, monkeypatch):
    """a chunked upload says nothing about its size; it is refused instead of
    being written to disk and measured after"""
    _ok_engine(monkeypatch)
    saved = []
    monkeypatch.setattr("werkzeug.datastructures.FileStorage.save", lambda self, dst: saved.append(dst))

    import io
    raw = b"--x\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"c\"\r\n\r\nabc\r\n--x--\r\n"
    response = client.post("/api/listen/identify", input_stream=io.BytesIO(raw),
                           headers={"Content-Type": "multipart/form-data; boundary=x",
                                    "Transfer-Encoding": "chunked"})
    assert response.status_code == 411
    assert response.get_json()["error"] == "length_required"
    assert saved == []


def test_ffmpeg_reads_only_the_uploaded_file(monkeypatch, tmp_path):
    import api.listen_routes as lr
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        (tmp_path / "out.wav").write_bytes(b"x" * 100)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(lr.subprocess, "run", fake_run)
    lr._to_wav(tmp_path / "in", tmp_path / "out.wav")
    argv = seen[0]
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert argv.index("-protocol_whitelist") < argv.index("-i")
