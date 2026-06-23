"""Settings → YouTube cookie options: browser store vs a pasted cookies.txt.

#902: syncing a YouTube *Music* "Liked Music" playlist (list=LM) needs auth, and on
a server/Docker box there's no local browser for cookiesfrombrowser to read — so we
let users paste a cookies.txt (yt-dlp cookiefile). These pin the precedence (so the
two cookie sources can never both be emitted), the paste validation (junk must not be
written out and break yt-dlp), and the fail-safe write (a blank save never wipes a
saved file).
"""

from __future__ import annotations

from core.youtube_cookies import (
    PASTE_MODE,
    build_youtube_cookie_opts,
    looks_like_cookiefile,
    write_pasted_cookiefile,
)

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tsecretvalue\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tanother\n"
)


# ── precedence (pure opts) ──────────────────────────────────────────────────

def test_empty_mode_is_anonymous():
    assert build_youtube_cookie_opts("") == {}
    assert build_youtube_cookie_opts(None) == {}


def test_browser_mode_uses_cookiesfrombrowser():
    assert build_youtube_cookie_opts("firefox") == {"cookiesfrombrowser": ("firefox",)}


def test_paste_mode_uses_cookiefile_when_present():
    opts = build_youtube_cookie_opts(PASTE_MODE, "/cfg/youtube_cookies.txt", cookiefile_exists=True)
    assert opts == {"cookiefile": "/cfg/youtube_cookies.txt"}


def test_paste_mode_without_a_real_file_is_anonymous_not_broken():
    # stale/missing path must NOT become a cookiefile arg yt-dlp would choke on
    assert build_youtube_cookie_opts(PASTE_MODE, "/cfg/gone.txt", cookiefile_exists=False) == {}
    assert build_youtube_cookie_opts(PASTE_MODE, "", cookiefile_exists=True) == {}


def test_sources_are_mutually_exclusive():
    # a browser name is never PASTE_MODE, so cookiefile + cookiesfrombrowser can't co-occur
    for mode in ("chrome", "firefox", PASTE_MODE, ""):
        opts = build_youtube_cookie_opts(mode, "/x.txt", cookiefile_exists=True)
        assert not ("cookiefile" in opts and "cookiesfrombrowser" in opts)


# ── paste validation ────────────────────────────────────────────────────────

def test_accepts_netscape_header_and_cookie_rows():
    assert looks_like_cookiefile(NETSCAPE) is True
    # no header but a valid tab-separated cookie row still counts
    assert looks_like_cookiefile(".youtube.com\tTRUE\t/\tTRUE\t123\tSID\tv") is True


def test_rejects_junk_paste():
    assert looks_like_cookiefile("") is False
    assert looks_like_cookiefile("   ") is False
    assert looks_like_cookiefile(None) is False
    assert looks_like_cookiefile("https://music.youtube.com/playlist?list=LM") is False
    assert looks_like_cookiefile('{"cookies": []}') is False
    assert looks_like_cookiefile("# Netscape HTTP Cookie File\n# only comments\n") is False


# ── fail-safe write ─────────────────────────────────────────────────────────

def test_write_persists_valid_cookiefile(tmp_path):
    dest = tmp_path / "youtube_cookies.txt"
    out = write_pasted_cookiefile(NETSCAPE, str(dest))
    assert out == str(dest)
    assert dest.read_text().startswith("# Netscape HTTP Cookie File")


def test_write_appends_trailing_newline(tmp_path):
    dest = tmp_path / "c.txt"
    write_pasted_cookiefile(NETSCAPE.rstrip("\n"), str(dest))
    assert dest.read_text().endswith("\n")


def test_write_refuses_junk_and_leaves_no_file(tmp_path):
    dest = tmp_path / "c.txt"
    assert write_pasted_cookiefile("not a cookie file", str(dest)) == ""
    assert not dest.exists()


def test_write_refuses_junk_without_clobbering_existing(tmp_path):
    # a blank/garbage save must NOT wipe a previously-saved cookie file
    dest = tmp_path / "c.txt"
    write_pasted_cookiefile(NETSCAPE, str(dest))
    before = dest.read_text()
    assert write_pasted_cookiefile("", str(dest)) == ""
    assert dest.read_text() == before
