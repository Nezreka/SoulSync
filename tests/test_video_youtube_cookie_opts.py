"""Video-side yt-dlp cookie opts must honor ALL Settings->YouTube modes.

The paste-cookies.txt mode ('custom') was skipped on the video side, so
headless/Docker users (no browser to borrow cookies from — llovi's report)
ran video-side YouTube cookie-less while the music side worked.
"""

from __future__ import annotations

import config.settings as cfg
from core.video.youtube import _cookie_opts


class _Cfg:
    def __init__(self, d):
        self._d = d

    def get(self, key, default=None):
        return self._d.get(key, default)


def test_paste_mode_emits_cookiefile(monkeypatch, tmp_path):
    cookie = tmp_path / "youtube_cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(cfg, "config_manager", _Cfg({
        "youtube.cookies_browser": "custom",
        "youtube.cookies_file": str(cookie),
    }))
    assert _cookie_opts() == {"cookiefile": str(cookie)}


def test_paste_mode_with_missing_file_is_anonymous(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "config_manager", _Cfg({
        "youtube.cookies_browser": "custom",
        "youtube.cookies_file": str(tmp_path / "gone.txt"),
    }))
    assert _cookie_opts() == {}          # stale path must never break yt-dlp


def test_browser_mode_still_works(monkeypatch):
    monkeypatch.setattr(cfg, "config_manager", _Cfg({
        "youtube.cookies_browser": "firefox",
    }))
    assert _cookie_opts() == {"cookiesfrombrowser": ("firefox",)}


def test_no_mode_is_anonymous(monkeypatch):
    monkeypatch.setattr(cfg, "config_manager", _Cfg({}))
    assert _cookie_opts() == {}
