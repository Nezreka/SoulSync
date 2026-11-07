"""Regression tests for #2013: a valid cookies.txt failed in SoulSync.

``parse_youtube_playlist`` (web_server.py) skips the yt-dlp webpage download
to dodge the ~100-item webpage cap (#908). With the webpage skipped, yt-dlp
never extracts ytcfg, and its playlist authcheck then raises FATALLY for any
authenticated request (``is_authenticated`` = the cookies carry LOGIN_INFO +
a SAPISID) before the InnerTube API endpoint is tried:

    ERROR: [youtube:tab] LM: Playlists that require authentication may not
    extract correctly without a successful webpage download...

Standalone yt-dlp works with the same cookies because it downloads the
webpage. The fix skips ``authcheck`` alongside ``webpage`` in the
``youtubetab`` extractor args: extraction then proceeds to the API endpoint
with the cookies instead of raising. (yt-dlp prescribes the skip for
non-private content or first-account cookies; the production comment in
web_server.py names the private-playlist trade-off explicitly.)

Coverage, honestly split: one regression test function (parametrized over
both cookie modes — pasted file and browser store) guards the fix —
``test_playlist_parse_skips_authcheck_alongside_webpage`` runs the real
``parse_youtube_playlist`` body (extracted by AST, the house pattern for
web_server functions) against a fake ``yt_dlp.YoutubeDL`` that captures its
options, and fails on the pre-fix code. Two further tests pin the
empty-result warning the fix adds (it fires for a cookie-configured parse
that returns 0 tracks, and only then). The remaining two tests are
characterization tests of the installed yt-dlp's own authcheck — they pass
identically before and after the fix, and document the mechanism: the
pre-fix skip list raises the reporter's fatal error (asserted by message),
the fixed list does not raise. The link between the webpage skip and the
authcheck firing (webpage skipped → no ytcfg → ``_extract_data`` calls the
check with ``fatal=True``) is from reading yt-dlp's ``_tab.py``, not from
these tests, which call the check directly. And because yt-dlp is
deliberately unpinned in this repo, the characterization tests skip rather
than fail if upstream renames its internals.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

WEB_SERVER = Path(__file__).resolve().parent.parent / "web_server.py"
# The reporter's exact URL form: a music.youtube.com Liked Music link.
PLAYLIST_URL = "https://music.youtube.com/playlist?list=LM"


def _load_parse_youtube_playlist(captured: dict, cookie_opts=None, entries=None):
    """Exec the real parse_youtube_playlist with a capturing fake yt-dlp."""
    if cookie_opts is None:
        cookie_opts = {"cookiefile": "/fake/cookies.txt"}
    if entries is None:
        entries = [
            {
                "title": "Example Artist - Example Song",
                "uploader": "Example Artist - Topic",
                "duration": 200,
                "id": "exampleVid1",
            }
        ]
    tree = ast.parse(WEB_SERVER.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "parse_youtube_playlist"
    )
    node.decorator_list = []

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "Liked Music",
                "id": "LM",
                "thumbnail": "",
                "entries": entries,
            }

    scope = {
        "logger": logging.getLogger("test_parse_youtube_playlist"),
        "yt_dlp": SimpleNamespace(YoutubeDL=FakeYDL),
        "_youtube_cookie_opts": lambda: cookie_opts,
        "_ytmusic_auth_headers": lambda: None,
        "clean_youtube_artist": lambda s: s,
        "clean_youtube_track_title": lambda t, a=None: t,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "web_server.py", "exec"), scope)
    return scope["parse_youtube_playlist"]


@pytest.mark.parametrize(
    "cookie_opts,cookie_key,cookie_value",
    [
        # Pasted cookies.txt (the reporter's mode, and the only one that
        # works in Docker)…
        ({"cookiefile": "/fake/cookies.txt"}, "cookiefile", "/fake/cookies.txt"),
        # …and a browser store, whose cookies make yt-dlp authenticated in
        # exactly the same way and hit the identical bug on local installs.
        ({"cookiesfrombrowser": ("firefox",)}, "cookiesfrombrowser", ("firefox",)),
    ],
)
def test_playlist_parse_skips_authcheck_alongside_webpage(
    monkeypatch, cookie_opts, cookie_key, cookie_value
):
    # The reporter's branch sequence: the ytmusicapi catalog path is tried
    # first for a music.youtube.com URL and returns None ("returned no
    # usable tracks for LM"), so the yt-dlp fallback below it is what runs.
    # Stub that seam — the function imports the name from the module at call
    # time — so the test stays hermetic where ytmusicapi is installed, and
    # RECORD the call so the branch sequence itself is pinned, not assumed.
    import core.youtube_music_meta as ymm

    ytmusic_calls: list = []

    def _fake_fetch(url, auth=None):
        ytmusic_calls.append(url)
        return None

    monkeypatch.setattr(ymm, "fetch_ytmusic_playlist", _fake_fetch)

    captured: dict = {}
    parse = _load_parse_youtube_playlist(captured, cookie_opts)

    result = parse(PLAYLIST_URL)

    assert ytmusic_calls == [PLAYLIST_URL]
    assert result is not None
    assert result["track_count"] == 1
    # The configured cookies still reach yt-dlp…
    assert captured.get(cookie_key) == cookie_value
    # …and the webpage skip that dodges the #908 page cap keeps its
    # authcheck companion, or yt-dlp raises before trying the API.
    skip = captured["extractor_args"]["youtubetab"]["skip"]
    assert "webpage" in skip
    assert "authcheck" in skip


def test_empty_cookie_parse_warns(monkeypatch, caplog):
    """A cookie-configured parse returning 0 tracks must say so out loud —
    that is the authcheck trade-off's silent-failure shape (#2013)."""
    import core.youtube_music_meta as ymm

    monkeypatch.setattr(ymm, "fetch_ytmusic_playlist", lambda url, auth=None: None)

    captured: dict = {}
    parse = _load_parse_youtube_playlist(captured, entries=[])

    with caplog.at_level(logging.WARNING, logger="test_parse_youtube_playlist"):
        result = parse(PLAYLIST_URL)

    assert result is not None
    assert result["track_count"] == 0
    assert any("returned 0 tracks" in r.message for r in caplog.records)


def test_empty_cookie_parse_warning_only_fires_in_that_case(monkeypatch, caplog):
    """No warning for an anonymous empty parse, nor for a cookie-configured
    parse that actually returns tracks — the warning's condition is both
    halves (0 tracks AND cookies configured), not either one alone."""
    import core.youtube_music_meta as ymm

    monkeypatch.setattr(ymm, "fetch_ytmusic_playlist", lambda url, auth=None: None)

    with caplog.at_level(logging.WARNING, logger="test_parse_youtube_playlist"):
        anon = _load_parse_youtube_playlist({}, cookie_opts={}, entries=[])
        assert anon(PLAYLIST_URL)["track_count"] == 0
        full = _load_parse_youtube_playlist({})
        assert full(PLAYLIST_URL)["track_count"] == 1

    assert not any("returned 0 tracks" in r.message for r in caplog.records)


def _authcheck_error(skip_list, cookiefile: Path):
    """The ExtractorError message yt-dlp's authcheck raises, or None."""
    yt_dlp = pytest.importorskip("yt_dlp")
    try:
        from yt_dlp.extractor.youtube._tab import YoutubeTabIE
    except Exception:  # upstream renamed its internals — nothing to check
        pytest.skip("yt-dlp tab extractor not importable")
    if not hasattr(YoutubeTabIE, "_report_playlist_authcheck"):
        pytest.skip("yt-dlp authcheck internals changed")

    from yt_dlp.utils import ExtractorError

    with yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtubetab": {"skip": skip_list}},
        "cookiefile": str(cookiefile),
    }) as ydl:
        ie = YoutubeTabIE(ydl)
        try:
            authenticated = ie.is_authenticated
        except Exception:  # upstream changed the predicate — nothing to check
            pytest.skip("yt-dlp is_authenticated not readable")
        if not authenticated:
            pytest.skip("yt-dlp does not consider the fake cookies authenticated")
        try:
            ie._report_playlist_authcheck(None, fatal=True)
        except ExtractorError as e:
            return str(e)
        except Exception:  # signature/behaviour changed upstream
            pytest.skip("yt-dlp authcheck call shape changed")
    return None


def _write_auth_cookies(tmp_path) -> Path:
    cookiefile = tmp_path / "cookies.txt"
    cookiefile.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tfake-sapisid\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tLOGIN_INFO\tfake-login-info\n",
        encoding="utf-8",
    )
    return cookiefile


def test_webpage_skip_alone_trips_ytdlp_authcheck(tmp_path):
    """The pre-fix opts reproduce the reporter's fatal error, by message."""
    error = _authcheck_error(["webpage"], _write_auth_cookies(tmp_path))
    assert error is not None
    assert "Playlists that require authentication may not extract correctly" in error
    assert "youtubetab:skip=authcheck" in error


def test_webpage_and_authcheck_skip_passes_ytdlp_authcheck(tmp_path):
    """With 'authcheck' skipped, yt-dlp proceeds instead of raising."""
    assert _authcheck_error(["webpage", "authcheck"], _write_auth_cookies(tmp_path)) is None
