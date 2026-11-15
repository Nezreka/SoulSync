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


# the YouTube source panel is React now (SETTINGS_PORT.md step 7g): the
# picker, its marking and the warning live in source-panels-more.tsx
_PANEL = "webui/src/features/settings/tabs/source-panels-more.tsx"


@pytest.fixture(scope="module")
def js():
    return _read(_PANEL)


def _youtube(js: str) -> str:
    return js.split("export function YoutubePanel(", 1)[1].split("\nexport function", 1)[0]


def _picker(js: str) -> str:
    """the cookie <select> as the panel renders it"""
    return _youtube(js).split('id="youtube-cookies-browser"', 1)[1].split("</select>", 1)[0]


def test_the_server_reports_its_own_os():
    """Not the user agent. The cookie store lives on the machine yt-dlp runs
    on, which is the server."""
    src = _read("web_server.py")
    assert "'windows': os.name == 'nt'" in src


def test_the_chromium_family_is_covered(js):
    listed = js.split("const ABE_BROWSERS = [", 1)[1].split("]", 1)[0]
    for browser in ("chrome", "edge", "brave", "opera", "vivaldi", "chromium"):
        assert f"'{browser}'" in listed, browser


def test_firefox_is_not_marked(js):
    """It uses its own cookie storage and is unaffected. Marking it would send
    people away from the one browser mode that still works."""
    listed = js.split("const ABE_BROWSERS = [", 1)[1].split("]", 1)[0]
    assert "firefox" not in listed
    assert "safari" not in listed


def test_the_options_are_marked_not_disabled(js):
    """The data-loss trap: a disabled selected option makes select.value ''.
    The 2s auto-save would then write an empty cookie source over their
    setting — breaking the config of the very users being warned."""
    picker = _picker(js)
    assert "disabled" not in picker.split("{/*", 1)[0] + picker.split("*/}", 1)[-1]
    assert "<option" in picker
    assert "not supported on Windows" in picker


def test_marking_is_reversible(js):
    """The label is worked out on every render from the saved settings, so a
    config moved from a Windows host to a Linux one loses the marking rather
    than keeping it forever (the old page kept the base label in a data
    attribute for this; React never rewrites the label in place)."""
    picker = _picker(js)
    assert ": label}" in picker or "\n                : label}" in picker
    assert "windows && ABE_BROWSERS.includes(value)" in picker


def test_nothing_is_marked_when_the_server_is_not_windows(js):
    panel = _youtube(js)
    assert "const windows = !!env.windows;" in panel
    assert "windows && ABE_BROWSERS.includes(value)" in _picker(js)
    assert "const abe = windows && ABE_BROWSERS.includes(browser);" in panel


def test_selecting_one_warns_immediately(js):
    # At the moment of choosing, not when a download fails days later: the
    # warning is drawn from the browser on screen, so it changes with the pick.
    panel = _youtube(js)
    warning = panel.split('id="youtube-cookie-abe-warning"', 1)[1].split("</div>", 1)[0]
    assert "hidden={!abe}" in warning
    assert "App-Bound Encryption" in warning
    assert "Paste cookies.txt" in warning


def test_the_warning_has_somewhere_to_render(js):
    index = _read("webui/index.html")
    assert 'data-stg-part="youtube"' in index
    panel = _youtube(js)
    assert 'id="youtube-cookie-abe-warning"' in panel
    assert 'role="status"' in panel.split('id="youtube-cookie-abe-warning"', 1)[1][:80]
    css = _read("webui/static/style.css")
    assert ".setting-warning {" in css
    assert ".setting-warning[hidden]" in css


def test_the_picker_is_marked_on_load(js):
    # A user who never touches the dropdown still needs to see it: the marking
    # reads the server's os from the loaded settings
    assert "settings._environment" in _youtube(js)


def test_the_help_text_points_at_something_that_works(js):
    """It used to say browser mode works "when that browser is on the same
    machine as SoulSync", which is true and, on Windows, useless: Chrome is on
    the same machine and still cannot be read. Naming the two modes that DO
    work is the difference between a fact and an instruction."""
    panel = _youtube(js)
    # up to the paste field
    block = panel.split('id="youtube-cookies-browser"', 1)[1].split('id="youtube-cookies-paste-group"', 1)[0]
    low = block.lower()
    assert "firefox" in low
    assert "paste cookies.txt" in low
    # and it should not imply cookies are required at all
    assert "public videos download fine" in low
