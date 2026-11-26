"""wsgi sets the GIL switch interval to 1 ms (SOULSYNC_GIL_SWITCH_INTERVAL_MS).

A thread that gives the GIL up and wants it back waits up to the interval
while a CPU-bound thread runs: a 15k-row query took 24 ms alone, 39 s next to
two busy threads at CPython's 5 ms, 4.9 s at 1 ms."""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def restore_interval():
    before = sys.getswitchinterval()
    yield
    sys.setswitchinterval(before)


@pytest.mark.parametrize("value,expected", [(None, 0.001), ("2", 0.002), ("0.5", 0.0005), ("bad", 0.001)])
def test_the_interval_comes_from_the_environment(monkeypatch, restore_interval, value, expected):
    if value is None:
        monkeypatch.delenv("SOULSYNC_GIL_SWITCH_INTERVAL_MS", raising=False)
    else:
        monkeypatch.setenv("SOULSYNC_GIL_SWITCH_INTERVAL_MS", value)
    import wsgi
    importlib.reload(wsgi)
    assert sys.getswitchinterval() == pytest.approx(expected)


def test_zero_keeps_pythons_interval(monkeypatch, restore_interval):
    sys.setswitchinterval(0.005)
    monkeypatch.setenv("SOULSYNC_GIL_SWITCH_INTERVAL_MS", "0")
    import wsgi
    importlib.reload(wsgi)
    assert sys.getswitchinterval() == pytest.approx(0.005)
