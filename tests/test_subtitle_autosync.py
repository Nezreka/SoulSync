"""Opt-in auto-sync via ffsubsync: best-effort wrapper, never raises, failures
fall back to the original subtitle. ffsubsync/subprocess are stubbed.
"""

from __future__ import annotations

import subprocess

from core.video.subtitles import autosync as autosync_mod
from core.video.subtitles.autosync import sync_subtitle_text


class _Proc:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def _stub(monkeypatch, binary="/bin/ffsubsync", proc=None, exc=None,
          out_text="1\n00:00:01,000 --> 00:00:02,000\nhi\n"):
    monkeypatch.setattr(autosync_mod.shutil, "which",
                        lambda name: binary if name == "ffsubsync" else None)
    calls = []

    def _run(cmd, **kw):
        calls.append((cmd, kw))
        if exc is not None:
            raise exc
        # Simulate ffsubsync writing its output file.
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as f:
            f.write(out_text)
        return proc if proc is not None else _Proc(0)
    monkeypatch.setattr(autosync_mod.subprocess, "run", _run)
    return calls


def _video(tmp_path):
    p = tmp_path / "m.mkv"
    p.write_bytes(b"x")
    return str(p)


def test_success_returns_synced_text(monkeypatch, tmp_path):
    calls = _stub(monkeypatch, out_text="1\n00:00:05,000 --> 00:00:06,000\nx\n")
    out = sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                             _video(tmp_path))
    assert out == "1\n00:00:05,000 --> 00:00:06,000\nx\n"
    cmd, kw = calls[0]
    assert cmd[0] == "/bin/ffsubsync"
    assert "--skip-sync-on-low-quality" in cmd
    assert kw.get("timeout") == 600


def test_missing_binary_is_none(monkeypatch, tmp_path):
    _stub(monkeypatch, binary=None)
    assert sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                              _video(tmp_path)) is None


def test_nonzero_exit_is_none(monkeypatch, tmp_path):
    _stub(monkeypatch, proc=_Proc(1))
    assert sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                              _video(tmp_path)) is None


def test_timeout_is_none(monkeypatch, tmp_path):
    _stub(monkeypatch, exc=subprocess.TimeoutExpired("ffsubsync", 600))
    assert sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                              _video(tmp_path)) is None


def test_os_error_is_none(monkeypatch, tmp_path):
    _stub(monkeypatch, exc=OSError("nope"))
    assert sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                              _video(tmp_path)) is None


def test_empty_output_is_none(monkeypatch, tmp_path):
    _stub(monkeypatch, out_text="   \n")
    assert sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                              _video(tmp_path)) is None


def test_bad_inputs_are_none(monkeypatch, tmp_path):
    _stub(monkeypatch)
    assert sync_subtitle_text("", _video(tmp_path)) is None
    assert sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                              "/nope/missing.mkv") is None
    assert sync_subtitle_text(None, None) is None


def test_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(autosync_mod.shutil, "which",
                        lambda name: (_ for _ in ()).throw(RuntimeError("x")))
    assert sync_subtitle_text("x", _video(tmp_path)) is None


def test_ffmpeg_path_passed_through(monkeypatch, tmp_path):
    monkeypatch.setattr(autosync_mod, "_resolve_ffmpeg",
                        lambda: "/tools/ffmpeg")
    calls = _stub(monkeypatch)
    sync_subtitle_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                       _video(tmp_path))
    cmd, _kw = calls[0]
    assert "--ffmpeg-path" in cmd
    assert cmd[cmd.index("--ffmpeg-path") + 1] == "/tools/ffmpeg"


def test_organization_normalize_autosync():
    from core.video import organization
    assert organization.normalize({})["subtitle_autosync"] is False
    assert organization.normalize({"subtitle_autosync": True})[
        "subtitle_autosync"] is True
    assert organization.normalize({"subtitle_autosync": "yes"})[
        "subtitle_autosync"] is True


def test_worker_maybe_autosync_gating(monkeypatch):
    from core.video.subtitles import worker as worker_mod
    # Disabled by default.
    assert worker_mod._maybe_autosync("text", "/v.mkv", "opensubtitles",
                                     lambda k, d=None: d) is None
    # Local providers are already timed — skipped even when enabled.
    gs = lambda k, d=None: True if k == "subtitle_autosync" else d
    assert worker_mod._maybe_autosync("text", "/v.mkv", "whisper", gs) is None
    assert worker_mod._maybe_autosync("text", "/v.mkv", "embedded", gs) is None
