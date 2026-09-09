"""Chromium browsers cannot be read on Windows — say so in the picker.

Chrome 127+ seals its cookie store with App-Bound Encryption. yt-dlp cannot
decrypt it (yt-dlp issue 10927), and no setting on either side changes that.
Boulder hit it, and the only signal was a red light after the fact.

Two things this pins.

The first is scope. It is the SERVER's operating system that decides, not the
browser the settings page happens to be open in: SoulSync on Linux, read by an
admin sitting at a Windows laptop, can read that Linux box's Chrome perfectly
well. And Firefox is unaffected everywhere — different storage, no DPAPI.

The second is that the options are MARKED, not disabled. A <select> whose
selected option is disabled reports value === '', so the page's auto-save would
quietly write an empty cookie source over the user's stored setting. Warning
everybody costs nothing; silently rewriting one person's config is a bug.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def js():
    return _read("webui/static/settings.js")


def test_the_server_reports_its_own_os():
    """Not the user agent. The cookie store lives on the machine yt-dlp runs
    on, which is the server."""
    src = _read("web_server.py")
    assert "'windows': os.name == 'nt'" in src


def test_the_chromium_family_is_covered(js):
    listed = js.split("const _ABE_BROWSERS = [", 1)[1].split("]", 1)[0]
    for browser in ("chrome", "edge", "brave", "opera", "vivaldi", "chromium"):
        assert f"'{browser}'" in listed, browser


def test_firefox_is_not_marked(js):
    """It uses its own cookie storage and is unaffected. Marking it would send
    people away from the one browser mode that still works."""
    listed = js.split("const _ABE_BROWSERS = [", 1)[1].split("]", 1)[0]
    assert "firefox" not in listed
    assert "safari" not in listed


def test_the_options_are_marked_not_disabled(js):
    """The data-loss trap: a disabled selected option makes select.value ''.
    The 2s auto-save would then write an empty cookie source over their
    setting — breaking the config of the very users being warned."""
    fn = js.split("function markUnsupportedCookieBrowsers(", 1)[1].split("\nwindow.", 1)[0]
    assert "opt.disabled" not in fn
    assert "textContent" in fn
    assert "not supported on Windows" in fn


def test_marking_is_reversible(js):
    """The label is rewritten on every load, so a config moved from a Windows
    host to a Linux one must lose the marking rather than keep it forever."""
    fn = js.split("function markUnsupportedCookieBrowsers(", 1)[1].split("\nwindow.", 1)[0]
    assert "baseLabel" in fn


def test_nothing_is_marked_when_the_server_is_not_windows(js):
    fn = js.split("function markUnsupportedCookieBrowsers(", 1)[1].split("\nwindow.", 1)[0]
    assert "isWindowsServer &&" in fn


def test_selecting_one_warns_immediately(js):
    # At the moment of choosing, not when a download fails days later.
    assert "function updateCookieBrowserWarning(" in js
    assert "updateCookieBrowserWarning);" in js       # bound to the change event
    fn = js.split("function updateCookieBrowserWarning(", 1)[1].split("\nwindow.", 1)[0]
    assert "App-Bound Encryption" in fn
    assert "Paste cookies.txt" in fn


def test_the_warning_has_somewhere_to_render():
    index = _read("webui/index.html")
    assert 'id="youtube-cookie-abe-warning"' in index
    assert 'role="status"' in index.split('id="youtube-cookie-abe-warning"', 1)[1][:80]
    css = _read("webui/static/style.css")
    assert ".setting-warning {" in css
    assert ".setting-warning[hidden]" in css


def test_the_picker_is_marked_on_load(js):
    # A user who never touches the dropdown still needs to see it.
    assert "markUnsupportedCookieBrowsers(!!(settings._environment" in js


def test_the_help_text_points_at_something_that_works():
    """It used to say browser mode works "when that browser is on the same
    machine as SoulSync", which is true and, on Windows, useless: Chrome is on
    the same machine and still cannot be read. Naming the two modes that DO
    work is the difference between a fact and an instruction."""
    index = _read("webui/index.html")
    # up to the paste field: the warning div now sits between the select and
    # the help text, so a 3-div window stops short of it
    block = index.split('id="youtube-cookies-browser"', 1)[1].split('id="youtube-cookies-paste-group"', 1)[0]
    low = block.lower()
    assert "firefox" in low
    assert "paste cookies.txt" in low
    # and it should not imply cookies are required at all
    assert "public videos download fine" in low
