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

        def last_failure_raw(self):
            return "ERROR: Sign in to confirm you're not a bot."

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


def test_a_server_WITH_cookies_gets_advice_that_fits_the_evidence():
    """This test used to assert the stale-cookie-export theory: re-export from a
    private window, close it without signing out. That theory was mine, it was
    unproven, and Boulder's install disproved it — he had never had a working
    cookie file at all, and his eventual 403s came from a 92-day-old yt-dlp plus
    cookies YouTube wanted a PO token for.

    So the advice names what has actually been observed to fix this, in order of
    how often it does: update yt-dlp (and restart, which the update needs), then
    turn cookies off, then the IP for server installs.
    """
    reason = human_reason(BOT_GATE, has_cookies=True) or ""
    low = reason.lower()
    assert "yt-dlp" in low
    assert "restart" in low
    assert "po token" in low
    # the guess that got dropped
    assert "private" not in low and "incognito" not in low

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


# ---------------------------------------------------------------------------
# Reading the cookie SOURCE can fail, and that is not "unclassifiable"
# ---------------------------------------------------------------------------

CHROME_MISSING = ('ERROR: could not find chrome cookies database in '
                  '"/home/broque/.config/google-chrome"')


def test_a_browser_cookie_store_we_cannot_read_is_classified():
    """Selecting Chrome while SoulSync runs under WSL (or in Docker, or on a
    headless box) means yt-dlp looks for a Linux Chrome profile that is not
    there. It classified as TRANSIENT, so the settings test said "the probe
    failed without a reason yt-dlp could classify — check app.log" about a
    completely explained and entirely fixable problem."""
    from core.youtube_errors import COOKIES, classify

    assert classify(CHROME_MISSING) == COOKIES


def test_the_advice_names_the_browser_it_could_not_read():
    reason = human_reason(CHROME_MISSING) or ""
    low = reason.lower()
    assert "chrome" in low
    # the fix that works everywhere
    assert "paste cookies.txt" in low
    # and NOT the advice for a bot gate, which is a different failure
    assert "not a bot" not in low


def test_the_advice_does_not_pick_a_cause_it_cannot_know():
    """The first version asserted "SoulSync runs under WSL and your browser is a
    Windows install". That was a guess dressed as a diagnosis, and it was wrong
    for the first person who read it — he was on Windows with Chrome open in
    front of him. It has to offer the candidates, not choose one."""
    low = (human_reason(CHROME_MISSING) or "").lower()
    for cause in ("open", "127+", "container"):
        assert cause in low, f"stopped offering {cause!r} as a possible cause"
    # a bare assertion of one cause reads as fact; the hedge is the point
    assert "usually one of" in low


def test_the_raw_error_survives_next_to_the_explanation():
    """An explanation that turns out to be wrong is only debuggable if the thing
    it was explaining is still on screen. Replacing the specific error with a
    general one is exactly how "check app.log" happened."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/connection_test.py").read_text(encoding="utf-8", errors="ignore")
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    assert "yt-dlp said:" in branch
    assert "last_failure_raw" in branch


def test_the_browser_advice_does_not_leak_into_a_bot_gate():
    """The bot gate also mentions --cookies-from-browser. It must keep its own
    BLOCKED reading, or #1126's conclusion gets undone."""
    from core.youtube_errors import BLOCKED, classify

    assert classify(BOT_GATE) == BLOCKED


@pytest.mark.parametrize("error", [
    "ERROR: ffmpeg not found",
    "No such file or directory: /tmp/out.mp3",
    "ERROR: [youtube] abc: Video unavailable",
    "HTTP Error 429: Too Many Requests",
])
def test_unrelated_failures_are_not_read_as_cookie_problems(error):
    """The first pass at this added a `no such file or directory` pattern, which
    would have swallowed a missing ffmpeg and a bad output path as cookie
    problems — the same over-broad matching this module exists to avoid."""
    from core.youtube_errors import COOKIES, classify

    assert classify(error) != COOKIES


def test_an_unclassifiable_failure_shows_the_error_not_a_log_reference():
    """"Check app.log for the raw error" is a poor trade for one line of screen
    space when the string is already in a variable."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/connection_test.py").read_text(encoding="utf-8", errors="ignore")
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    # strip comments: the note explaining why the log reference is gone quotes it
    code = "\n".join(ln for ln in branch.splitlines() if not ln.strip().startswith("#"))
    assert "last_failure_raw" in code
    assert "check app.log" not in code


def test_the_client_keeps_the_raw_error():
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/youtube_client.py").read_text(encoding="utf-8", errors="ignore")
    assert "def last_failure_raw" in src
    assert "self.last_error_raw" in src


# ---------------------------------------------------------------------------
# App-Bound Encryption — the actual cause, from Boulder's own machine
# ---------------------------------------------------------------------------

DPAPI = ("ERROR: ERROR: Failed to decrypt with DPAPI. "
         "See https://github.com/yt-dlp/yt-dlp/issues/10927 for more info")


def test_dpapi_is_named_precisely_not_hedged():
    """Three candidate causes was the right answer while the cause was unknown.
    It stopped being the right answer the moment the raw line came back saying
    DPAPI: that IS Chromium's App-Bound Encryption, it cannot be configured
    around, and closing the browser does not help."""
    reason = human_reason(DPAPI, cookie_source="chrome") or ""
    low = reason.lower()
    assert "app-bound encryption" in low
    assert "10927" in reason
    assert "closing the browser will not help" in low
    assert "usually one of" not in low       # the hedge must be gone here
    assert "paste cookies.txt" in low


def test_the_browser_is_named_from_what_the_user_picked():
    """The DPAPI error contains no browser name, so sniffing the error text
    produced "the browser's cookies" for somebody who had plainly selected
    Chrome. The configured value is the reliable source."""
    assert "Chrome's" in (human_reason(DPAPI, cookie_source="chrome") or "")
    assert "Firefox's" in (human_reason(DPAPI, cookie_source="firefox") or "")


def test_the_possessive_is_not_mangled():
    # .title() on "chrome's" gives "Chrome'S".
    for src in ("chrome", "edge", "brave"):
        assert "'S " not in (human_reason(DPAPI, cookie_source=src) or "")


def test_a_generic_cookie_store_failure_keeps_the_hedge():
    """Only DPAPI is certain. A missing profile really could be any of three
    things, and picking one is what got this wrong the first time."""
    low = (human_reason(CHROME_MISSING, cookie_source="chrome") or "").lower()
    assert "usually one of" in low
    assert "app-bound" not in low


def test_a_doubled_error_prefix_is_stripped():
    """yt-dlp emitted "ERROR: ERROR: Failed to decrypt..." and a single
    replace left one of them in the message."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/connection_test.py").read_text(encoding="utf-8", errors="ignore")
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    assert 'while raw.startswith("ERROR: ")' in branch
    assert 'raw.replace("ERROR: ", "", 1)' not in branch


# ---------------------------------------------------------------------------
# The retry that dropped cookies and then undid itself
# ---------------------------------------------------------------------------

def test_the_cookie_dropping_retry_keeps_its_format():
    """Proved on Boulder's install with one video and one yt-dlp:

        cookies                    -> fails
        no cookies, bestaudio/best -> DOWNLOADS
        no cookies, 'best'         -> fails on format

    The third attempt dropped the cookies (the fix) and switched the format to
    'best' in the same breath (which defeats it), so the recovery never landed
    and every download burned its whole retry budget."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/youtube_client.py").read_text(encoding="utf-8", errors="ignore")
    chain = src.split("elif attempt >= 2:", 1)[1].split("break", 1)[0]
    cookie_path = chain.split("if extra:", 1)[1].split("else:", 1)[0]
    assert "cookiefile" in cookie_path, "the retry no longer drops cookies"
    assert "download_opts['format'] = 'best'" not in cookie_path, \
        "the cookie-dropping retry changes the format again"


def test_the_format_fallback_survives_when_there_are_no_cookies():
    """With nothing to drop it is still the format fallback it always was."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/youtube_client.py").read_text(encoding="utf-8", errors="ignore")
    chain = src.split("elif attempt >= 2:", 1)[1].split("break", 1)[0]
    no_cookie_path = chain.split("else:", 1)[1]
    assert "download_opts['format'] = 'best'" in no_cookie_path


def test_the_403_advice_still_names_ytdlp_when_cookies_exist():
    """I removed this line this morning, reasoning that somebody who already has
    cookies has been sent to the wrong lever. Then Boulder's own 403s turned out
    to be a 92-day-old yt-dlp AND cookies YouTube wanted a PO token for — and the
    advice that would have fixed it was the one I had just taken away."""
    reason = human_reason("ERROR: unable to download video data: HTTP Error 403: Forbidden",
                          has_cookies=True) or ""
    low = reason.lower()
    assert "yt-dlp" in low
    assert "restart" in low          # the update does nothing until one
    assert "po token" in low         # and cookies themselves can be the cause
    assert "none" in low


def test_cookies_without_a_js_runtime_are_called_out():
    """The combination that actively makes downloads worse, and the settings
    page implies the opposite.

    Signed-in requests need a PO token; solving one needs a JS runtime; a
    signed-out request usually needs none. So on a box with no Deno, following
    the "add cookies to fix the bot gate" advice turns working downloads into
    403s. That is Docker and headless servers — and it is what made me wrongly
    conclude, from a WSL venv with no Deno, that cookies break downloads
    everywhere. They do not: Boulder's Windows box has Deno and his cookies work.
    """
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/youtube_client.py").read_text(encoding="utf-8", errors="ignore")
    fn = src.split("def _warn_if_no_js_runtime(", 1)[1].split("\ndef ", 1)[0]
    assert "_resolve_cookie_opts()" in fn, "the cookie state is not checked here"
    assert "PO token" in fn
    assert "back to None" in fn


def test_the_retry_comment_does_not_overclaim():
    """It said the signed-out retry "is the one that works", full stop. That was
    measured in an environment with no JS runtime and does not generalise."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "core/youtube_client.py").read_text(encoding="utf-8", errors="ignore")
    chain = src.split("elif attempt >= 2:", 1)[1].split("break", 1)[0]
    assert "not a cure" in chain
