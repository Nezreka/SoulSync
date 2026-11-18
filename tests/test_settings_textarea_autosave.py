"""Textareas on the settings page never auto-saved (found live).

The page binds its debounced auto-save to
``input[type=text|url|password|number|range]`` on 'input', and to
``input[type=checkbox], select`` on 'change'. ``textarea`` is in neither list.

Boulder pasted a cookies.txt, pressed Test, and the test ran against the config
as it stood BEFORE the paste — the value only ever reached the server if
something else happened to trigger a save. He read that as "pasting doesn't
work", which is a fair reading of what he was shown.

'input' and not 'change' matters here: 'change' on a textarea does not fire
until blur, and pasting then clicking a button inside the same panel never
blurs it.
"""

import re
from pathlib import Path

import pytest

from tests.support.settings_page import index_with_settings_page

_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def js():
    return _read("webui/src/features/settings/shell.ts")


def _react_textareas():
    """every textarea the Settings tabs render (each is React since
    SETTINGS_PORT.md: the page binds no field of its own, step V4b)"""
    tabs = _ROOT / "webui" / "src" / "features" / "settings" / "tabs"
    out = []
    for f in sorted(tabs.glob("*.tsx")):
        if f.name.endswith(".test.tsx"):
            continue
        out += [(f.name, m.group(0)) for m in re.finditer(r"<textarea\s[\s\S]*?/>", f.read_text(encoding="utf-8"))]
    return out


def test_textareas_are_bound_to_the_autosave(js):
    # the page's own binding is gone with settings.js: each textarea is React's
    assert "querySelectorAll('textarea" not in js
    assert _react_textareas(), "no React textarea found — has the parse broken?"


def test_they_are_bound_on_input_not_change(js):
    """'change' waits for blur. Paste, then click Test without leaving the
    field, and a 'change' binding still would not have saved it. React's
    onChange fires on every input."""
    for name, tag in _react_textareas():
        assert "onChange=" in tag or "{...draft" in tag or "{...field" in tag, f"{name}: {tag[:120]}"


def test_the_cookie_paste_is_covered():
    """The field this was found on. It lives inside a source modal, so the only
    thing that used to save it was closing that modal. The YouTube panel is
    React now (SETTINGS_PORT.md step 7g): a React textarea's onChange fires on
    every input, like the 'input' binding, and writes the key to the settings
    store, which autosaves it."""
    panel = _read("webui/src/features/settings/tabs/source-panels-more.tsx")
    tag = re.search(r'<textarea\s[^>]*?id="youtube-cookies-paste"[\s\S]*?/>', panel)
    assert tag, "the paste field is not a textarea any more — recheck the binding"
    assert "set('cookies_paste', event.target.value)" in tag.group(0)
    assert "hydra-payload" not in tag.group(0)
    assert 'id="youtube-cookies-paste"' not in _read("webui/index.html")


def test_the_dev_console_boxes_are_excluded(js):
    """The Hydrabase payload boxes are a request console, not settings. Binding
    them would fire a full settings save on every keystroke of a JSON blob. The
    page binds no field at all now (shell.ts initializeSettings)."""
    init = js.split("export function initializeSettings()", 1)[1].split("\n}", 1)[0]
    assert "addEventListener('input'" not in init and "addEventListener('change'" not in init


def test_every_settings_textarea_is_either_bound_or_deliberately_skipped():
    """A guard against the next textarea being added and silently not saving —
    which is the whole bug, and it sat there unnoticed."""
    index = index_with_settings_page()
    start = index.index('id="settings-page"')
    page = index[start:index.index('data-stg-react="logs"', start)]
    # none is left in the page's markup: each is a React tab's, bound above
    assert re.findall(r"<textarea[^>]*>", page) == []


# ---------------------------------------------------------------------------
# The yt-dlp panel and the update button contradicting each other
# ---------------------------------------------------------------------------

def test_the_status_reports_disk_as_well_as_loaded():
    """They differ for the whole window between updating and restarting. Saying
    only the loaded one made a SUCCESSFUL update read as "still behind" while
    the button said "already on the newest build" — both true, and together they
    read as "the update did not work"."""
    src = _read("web_server.py")
    route = src.split("def ytdlp_status(", 1)[1].split("@app.route", 1)[0]
    assert "version_on_disk()" in route
    assert "'on_disk'" in route
    assert "restart_pending" in route


def test_the_two_readings_come_from_different_places():
    """One must read the imported module, the other pip's metadata. Reading
    both the same way makes them agree and hides the state entirely."""
    src = _read("core/ytdlp_update.py")
    loaded = src.split("def installed_version(", 1)[1].split("\ndef ", 1)[0]
    disk = src.split("def version_on_disk(", 1)[1].split("\ndef ", 1)[0]
    assert "import yt_dlp" in loaded
    assert "importlib.metadata" in disk


@pytest.mark.parametrize("loaded,on_disk,expected", [
    # the real reason to pad: the SAME wheel is spelled two ways
    ("2026.08.30.232658", "2026.8.30.232658.dev0", False),
    ("2026.8.30.232658", "2026.08.30.232658", False),
    # a genuine pending restart
    ("2026.06.09", "2026.8.30.232658.dev0", True),
    ("2026.06.09", "2026.8.19", True),
    # nothing to say
    (None, "2026.8.19", False),
    ("2026.8.19", None, False),
])
def test_restart_pending_survives_the_two_spellings(loaded, on_disk, expected):
    """yt_dlp.version.__version__ is '2026.08.30.232658'; pip's metadata for the
    identical wheel is '2026.8.30.232658.dev0'. Comparing the raw tuples makes
    the second look newer forever, parking a permanent "restart to finish" on
    every nightly install. Found by running it against a real install."""
    from core.ytdlp_update import restart_pending

    assert restart_pending(loaded, on_disk) is expected


def test_is_behind_has_the_same_padding_fix():
    # Same two spellings, same false positive: a current nightly read as behind.
    from core.ytdlp_update import is_behind

    assert is_behind("2026.08.30.232658", "2026.8.30.232658.dev0") is False
    assert is_behind("2026.06.09", "2026.8.19") is True


def test_the_panel_shows_the_pending_state(js):
    # the yt-dlp tile is in the React YouTube panel now (step 7g)
    panel = _read("webui/src/features/settings/tabs/source-panels-more.tsx")
    fn = panel.split("async function loadYtdlp(", 1)[1].split("\n  }\n", 1)[0]
    assert "restart_pending" in fn
    assert "restart to finish" in fn
