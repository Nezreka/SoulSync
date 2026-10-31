"""M11: native audio analysis runs in a child process, so a numba segfault
fails one analysis instead of killing the server."""

import subprocess
import sys
import time

import pytest

from core.sample import isolated


@pytest.fixture(autouse=True)
def _no_child_left():
    yield
    with isolated._LOCK:
        if isolated._REAPER is not None:
            isolated._REAPER.cancel()
            isolated._REAPER = None
        isolated._stop_child_locked()


def _wait_for(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.05)
    return check()


def test_an_analysis_error_comes_back_as_an_exception():
    with pytest.raises(RuntimeError):
        isolated.analyze_track_isolated("/nonexistent/track.wav")


def test_a_crashed_child_fails_only_its_request(monkeypatch):
    # dies mid-request, the way a segfault in the analysis would
    dying = subprocess.Popen(
        [sys.executable, "-c", "import os, sys; sys.stdin.readline(); os._exit(139)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    monkeypatch.setattr(isolated, "_CHILD", dying)

    with pytest.raises(RuntimeError, match="crashed"):
        isolated.analyze_track_isolated("/any.wav")
    assert isolated._CHILD is None
    # ...and the next request gets a fresh child that answers again
    with pytest.raises(RuntimeError, match="audio file not found"):
        isolated.analyze_track_isolated("/nonexistent/track.wav")


def test_an_idle_child_exits_and_the_next_request_starts_a_fresh_one(monkeypatch):
    # #1938: librosa, numba and llvm stay resident in the child after an
    # analysis, so an idle child gives its memory back by exiting
    monkeypatch.setattr(isolated, "IDLE_EXIT_S", 0.3)
    with pytest.raises(RuntimeError):
        isolated.analyze_track_isolated("/nonexistent/track.wav")
    first = isolated._CHILD
    assert first is not None and first.poll() is None

    assert _wait_for(lambda: isolated._CHILD is None)
    assert first.wait(timeout=5) == 0   # closed its input and left, not killed

    with pytest.raises(RuntimeError, match="audio file not found"):
        isolated.analyze_track_isolated("/nonexistent/track.wav")
    assert isolated._CHILD is not None and isolated._CHILD is not first


def test_a_child_in_use_is_never_reaped(monkeypatch):
    # a backfill's tracks come one after another: one child for the batch
    monkeypatch.setattr(isolated, "IDLE_EXIT_S", 0.6)
    children = set()
    for _ in range(6):
        with pytest.raises(RuntimeError):
            isolated.analyze_track_isolated("/nonexistent/track.wav")
        children.add(isolated._CHILD.pid)
        time.sleep(0.2)
    assert len(children) == 1


def test_zero_keeps_the_child(monkeypatch):
    monkeypatch.setattr(isolated, "IDLE_EXIT_S", 0)
    with pytest.raises(RuntimeError):
        isolated.analyze_track_isolated("/nonexistent/track.wav")
    time.sleep(0.4)
    assert isolated._REAPER is None
    assert isolated._CHILD is not None and isolated._CHILD.poll() is None


def test_the_child_gets_a_numba_cache_it_can_write(monkeypatch, tmp_path):
    # a fresh child loads compiled kernels instead of recompiling them; numba's
    # default sits beside librosa, which a container can't write
    seen = {}
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        seen["env"] = kwargs.get("env") or {}
        return real_popen(*args, **kwargs)

    monkeypatch.delenv("NUMBA_CACHE_DIR", raising=False)
    monkeypatch.setattr(isolated.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError):
        isolated.analyze_track_isolated("/nonexistent/track.wav")
    assert seen["env"]["NUMBA_CACHE_DIR"].endswith("soulsync-numba-cache")

    with isolated._LOCK:
        isolated._stop_child_locked()
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        isolated.analyze_track_isolated("/nonexistent/track.wav")
    assert seen["env"]["NUMBA_CACHE_DIR"] == str(tmp_path)   # a set one is kept
