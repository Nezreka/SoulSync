"""A YouTube block must say so, not read as "nothing found" (#1126).

fabian42069 ran SoulSync in Docker on a server. It worked for a day, then
YouTube stopped answering — the classic datacenter-IP bot gate. SoulSync is not
what broke, and cannot unbreak it. But everything he was shown pointed AWAY from
the cause:

  · Search → "No results found for 'the living'"
  · Settings → "soulseek service check failed: YouTube download source not
    available."
  · Downloads → "0 tracks downloaded" / "Download status: Not Found"

Three messages that describe SoulSync failing to FIND something, when what
happened is YouTube refusing us. He then deleted his config, re-pulled the
image, re-exported cookies twice and switched download source — none of which
could have helped, and all of which the messages invited.

Two defects behind that, pinned here:

1. ``check_connection`` was the only yt-dlp call in the client that went out
   WITHOUT cookies (search and download both apply them), so the Settings test
   could fail while real work succeeded.
2. Failures were logged and swallowed. The classifier that already tells
   YouTube's failures apart for the video side now runs on the music side too,
   and the reason reaches the status text.
"""

from __future__ import annotations

import pytest

from core.youtube_errors import BLOCKED, classify, human_reason


BOT_GATE = (
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)


# ── the classifier is shared, not duplicated ─────────────────────────────────

def test_the_video_module_is_the_same_object_after_the_move():
    """It moved to core.youtube_errors so BOTH sides classify identically. A
    second copy would drift, and the two halves would name one failure two
    ways."""
    from core.video import youtube_errors as video_shim

    assert video_shim.classify is classify
    assert video_shim.human_reason is human_reason


def test_the_bot_gate_is_recognised_and_explained():
    assert classify(BOT_GATE) == BLOCKED
    reason = human_reason(BOT_GATE)
    assert reason and "yt-dlp" in reason


# ── the client records WHY ───────────────────────────────────────────────────

def _client(monkeypatch):
    """A YouTubeClient with no __init__ side effects (no ffmpeg probe, no
    network, no config read)."""
    from core.youtube_client import YouTubeClient

    client = YouTubeClient.__new__(YouTubeClient)
    client.download_opts = {}
    return client


def test_a_failed_search_records_the_reason_instead_of_swallowing_it(monkeypatch):
    from core.youtube_client import YouTubeClient

    client = _client(monkeypatch)
    tracks, albums = _run(YouTubeClient.search(client, "the living"), monkeypatch,
                          raises=Exception(BOT_GATE))

    # The empty result is still returned — callers' contract is unchanged …
    assert (tracks, albums) == ([], [])
    # … but the client can now say why, which is the whole point.
    assert "yt-dlp" in (client.last_failure_reason() or "")
    assert client.last_error_kind == BLOCKED


def test_a_successful_check_clears_a_stale_reason(monkeypatch):
    from core.youtube_client import YouTubeClient

    client = _client(monkeypatch)
    client.last_error_reason = "something old"
    ok = _run(YouTubeClient.check_connection(client), monkeypatch, returns=True)

    assert ok is True
    assert client.last_failure_reason() is None


def test_an_unexplainable_failure_does_not_invent_a_reason(monkeypatch):
    """Only say something specific when we actually know something specific."""
    from core.youtube_client import YouTubeClient

    client = _client(monkeypatch)
    _run(YouTubeClient.search(client, "x"), monkeypatch,
         raises=Exception("connection reset by peer"))

    assert client.last_failure_reason() is None


def _run(coro, monkeypatch, *, returns=None, raises=None):
    """Drive one of the client's coroutines with run_blocking stubbed, so no
    yt-dlp and no thread pool are involved."""
    import asyncio

    import core.youtube_client as yc

    async def _fake_run_blocking(fn, *a, **k):
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(yc, "run_blocking", _fake_run_blocking)
    return asyncio.run(coro)


# ── the probe carries the user's cookies ─────────────────────────────────────

def test_the_connection_probe_uses_the_same_cookies_as_the_real_work(monkeypatch):
    """It was the one call that didn't. A user with working cookies still got
    "YouTube download source not available" from the Settings test."""
    import asyncio

    import core.youtube_client as yc
    from core.youtube_client import YouTubeClient

    monkeypatch.setattr(yc, "_resolve_cookie_opts",
                        lambda: {"cookiefile": "/config/yt-cookies.txt"})
    seen = {}

    class _FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x"}

    monkeypatch.setattr(yc.yt_dlp, "YoutubeDL", _FakeYDL)

    async def _real_run_blocking(fn, *a, **k):
        return fn(*a, **k)
    monkeypatch.setattr(yc, "run_blocking", _real_run_blocking)

    client = _client(monkeypatch)
    assert asyncio.run(YouTubeClient.check_connection(client)) is True
    assert seen.get("cookiefile") == "/config/yt-cookies.txt"


# ── the status text the user actually reads ──────────────────────────────────

def test_the_settings_toast_carries_the_real_reason(monkeypatch):
    """fabian's toast said "YouTube download source not available." and nothing
    else. The source knew more than that and wasn't asked."""
    import core.connection_test as ct

    class _Source:
        def last_failure_reason(self):
            return "YouTube refused the download. Update yt-dlp."

    class _Orch:
        def client(self, name):
            return _Source()

        async def check_connection(self):
            return False

    monkeypatch.setattr(ct, "download_orchestrator", _Orch(), raising=False)
    monkeypatch.setattr(ct.config_manager, "get",
                        lambda key, default=None: 'youtube'
                        if key == 'download_source.mode' else default)
    monkeypatch.setattr(ct, "run_async", lambda coro: False)

    ok, message = ct.run_service_test("soulseek", {})

    assert ok is False
    assert "not available" in message          # the generic half still there
    assert "Update yt-dlp" in message          # …now with what to DO about it


def test_a_source_that_cannot_explain_itself_still_returns_the_generic_text(monkeypatch):
    """Only YouTube grew `last_failure_reason`; the other ten sources must not
    break on a status check."""
    import core.connection_test as ct

    class _Orch:
        def client(self, name):
            return object()          # no last_failure_reason attribute

        async def check_connection(self):
            return False

    monkeypatch.setattr(ct, "download_orchestrator", _Orch(), raising=False)
    monkeypatch.setattr(ct.config_manager, "get",
                        lambda key, default=None: 'soundcloud'
                        if key == 'download_source.mode' else default)
    monkeypatch.setattr(ct, "run_async", lambda coro: False)

    ok, message = ct.run_service_test("soulseek", {})

    assert ok is False
    assert message == "SoundCloud download source not available."


# ---------------------------------------------------------------------------
# #1233 — the Settings probe never actually asked
# ---------------------------------------------------------------------------

_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def test_the_settings_probe_is_not_hardcoded_true():
    """The whole of #1126's work — the cookie-authenticated probe, the
    classifier, the reason on the status line — was unreachable from the
    button most people press. ``HYBRID_SOURCE_PROBE.youtube`` was
    ``() => Promise.resolve(true)``, so the dot was green while YouTube was
    refusing every download. Same reporter came back with #1233."""
    js = _read("webui/static/settings.js")
    probe = js.split("const HYBRID_SOURCE_PROBE = {", 1)[1].split("};", 1)[0]
    line = next(ln for ln in probe.splitlines() if ln.strip().startswith("youtube:"))
    assert "Promise.resolve(true)" not in line, "the YouTube probe still cannot fail"
    assert "_ssTestConn('youtube')" in line


def test_the_server_knows_how_to_test_youtube():
    # Pointing the probe at a service run_service_test does not handle would
    # turn a permanently-green dot into a permanently-red one.
    src = _read("core/connection_test.py")
    assert 'elif service == "youtube":' in src
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    assert "check_connection()" in branch
    assert "last_failure_reason()" in branch


def test_a_failing_probe_reports_the_reason_it_was_given():
    """The point of the branch. A bare "not available" is what sent this
    reporter deleting his config twice."""
    from core.connection_test import run_service_test

    class _Yt:
        def is_available(self):
            return True

        async def check_connection(self):
            return False

        def last_failure_reason(self):
            return "YouTube is asking us to prove we are not a bot."

    import core.youtube_client as yt_mod
    original = yt_mod.YouTubeClient
    yt_mod.YouTubeClient = _Yt
    try:
        ok, message = run_service_test("youtube", {})
    finally:
        yt_mod.YouTubeClient = original

    assert ok is False
    assert "not a bot" in message


def test_a_working_probe_says_so():
    from core.connection_test import run_service_test

    class _Yt:
        def is_available(self):
            return True

        async def check_connection(self):
            return True

        def last_failure_reason(self):
            return None

    import core.youtube_client as yt_mod
    original = yt_mod.YouTubeClient
    yt_mod.YouTubeClient = _Yt
    try:
        ok, message = run_service_test("youtube", {})
    finally:
        yt_mod.YouTubeClient = original

    assert ok is True
    assert "ready" in message.lower()


def test_a_missing_ytdlp_is_named_as_such():
    # Not the same failure as a block, and not fixable by re-exporting cookies.
    from core.connection_test import run_service_test

    class _Yt:
        def is_available(self):
            return False

    import core.youtube_client as yt_mod
    original = yt_mod.YouTubeClient
    yt_mod.YouTubeClient = _Yt
    try:
        ok, message = run_service_test("youtube", {})
    finally:
        yt_mod.YouTubeClient = original

    assert ok is False
    assert "yt-dlp" in message


def test_the_summary_toast_carries_a_reason_not_just_a_count():
    """"Tested sources 1✓, connections 3✓ / 1✗" is the screenshot on #1233.
    It says something failed and nothing about what, even when the source
    handed back a sentence explaining itself."""
    js = _read("webui/static/settings.js")
    assert "_ssLastTestMessage" in js
    conn = js.split("function _ssTestConn(", 1)[1].split("\n}", 1)[0]
    assert "j.message || j.error" in conn, "the reason is still discarded on failure"
    toast = js.split("const parts = [`sources", 1)[1].split("showToast(", 1)[1]
    assert "detail" in toast


# ---------------------------------------------------------------------------
# #1233 — the advice has to depend on whether cookies exist
# ---------------------------------------------------------------------------

def test_a_server_with_no_cookies_is_still_told_to_update_ytdlp():
    """Unchanged for every existing caller. `has_cookies` left unset keeps the
    exact wording the video side and the failure summary already rely on."""
    assert "yt-dlp" in (human_reason(BOT_GATE) or "")
    assert "yt-dlp" in (human_reason(BOT_GATE, has_cookies=False) or "")


def test_a_server_WITH_cookies_is_told_the_export_went_stale():
    """This is fabian42069's actual situation, twice over.

    He pasted cookies, was told to update yt-dlp, re-exported instead, got a
    couple of working days and came back. "Worked for a few days then stopped
    and I changed nothing" is not an old yt-dlp — it is Google rotating the
    session-refresh token behind an export taken from a browser he stayed
    signed in to.
    """
    reason = human_reason(BOT_GATE, has_cookies=True) or ""
    assert "yt-dlp" not in reason, "still sending him to the lever that did nothing"
    low = reason.lower()
    assert "stale" in low or "re-export" in low
    # The bit that actually makes the next export last.
    assert "private" in low or "incognito" in low
    assert "without signing out" in low
    # And the honest fallback, because it might genuinely be the IP.
    assert "ip" in low


def test_the_client_tells_the_classifier_whether_cookies_exist():
    """A pure function cannot know, so the client has to say. Without this the
    cookie-aware branch is unreachable in the only place it matters."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/youtube_client.py").read_text(encoding="utf-8", errors="ignore")
    record = src.split("def _record_failure(", 1)[1].split("\n    def ", 1)[0]
    assert "has_cookies=has_cookies" in record
    assert "_resolve_cookie_opts()" in record


def test_an_age_gate_is_untouched_by_the_cookie_context():
    # Different failure, different fix — cookies being present says nothing here.
    age = "ERROR: Sign in to confirm your age. This video may be inappropriate for some users."
    assert human_reason(age) == human_reason(age, has_cookies=True)
