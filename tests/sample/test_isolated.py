"""M11: native audio analysis runs in a child process, so a numba segfault
fails one analysis instead of killing the server."""

import subprocess
import sys

import pytest

from core.sample import isolated


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
