"""Seam tests for the yt-dlp JS-runtime startup check.

YouTube gates downloadable formats behind JS challenges; without Deno on PATH
every stream / music-video download fails with the cryptic "Requested format
is not available". The check must say so plainly in the log — once — and stay
silent when deno is present.
"""

from __future__ import annotations

import core.youtube_client as yc


def _reset():
    yc._JS_RUNTIME_WARNED = False


def _capture_warnings(monkeypatch):
    calls = []
    monkeypatch.setattr(yc.logger, 'warning', lambda msg, *a: calls.append(msg % a if a else msg))
    return calls


def test_warns_once_when_deno_missing(monkeypatch):
    _reset()
    warnings = _capture_warnings(monkeypatch)
    monkeypatch.setattr('shutil.which', lambda name: None)

    yc._warn_if_no_js_runtime()
    yc._warn_if_no_js_runtime()  # second call must not duplicate

    assert len(warnings) == 1
    assert 'Requested format is not available' in warnings[0]
    assert 'Deno' in warnings[0] or 'deno' in warnings[0]


def test_silent_when_deno_present(monkeypatch):
    _reset()
    warnings = _capture_warnings(monkeypatch)
    monkeypatch.setattr('shutil.which', lambda name: '/usr/local/bin/deno' if name == 'deno' else None)

    yc._warn_if_no_js_runtime()

    assert warnings == []
