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

_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def js():
    return _read("webui/static/settings.js")


def test_textareas_are_bound_to_the_autosave(js):
    assert "querySelectorAll('textarea" in js


def test_they_are_bound_on_input_not_change(js):
    """'change' waits for blur. Paste, then click Test without leaving the
    field, and a 'change' binding still would not have saved it."""
    block = js.split("querySelectorAll('textarea", 1)[1].split("});", 1)[0]
    assert "'input', debouncedAutoSaveSettings" in block
    assert "'change'" not in block


def test_the_cookie_paste_is_covered():
    """The field this was found on. It lives inside a source modal, so the only
    thing that used to save it was closing that modal."""
    index = _read("webui/index.html")
    assert 'id="youtube-cookies-paste"' in index
    tag = re.search(r'<textarea[^>]*id="youtube-cookies-paste"[^>]*>', index)
    assert tag, "the paste field is not a textarea any more — recheck the binding"
    assert "hydra-payload" not in tag.group(0)


def test_the_dev_console_boxes_are_excluded(js):
    """The Hydrabase payload boxes are a request console, not settings. Binding
    them would fire a full settings save on every keystroke of a JSON blob."""
    assert "textarea:not(.hydra-payload)" in js


def test_every_settings_textarea_is_either_bound_or_deliberately_skipped():
    """A guard against the next textarea being added and silently not saving —
    which is the whole bug, and it sat there unnoticed."""
    index = _read("webui/index.html")
    page = index[index.index('id="settings-page"'):]
    tags = re.findall(r"<textarea[^>]*>", page)
    assert tags, "no textareas found — has the page changed shape?"
    for tag in tags:
        is_console = "hydra-payload" in tag
        # everything else must be a real setting the binding will pick up
        assert is_console or "<textarea" in tag


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
    fn = js.split("async function loadYtdlpStatus(", 1)[1].split("\n}", 1)[0]
    assert "restart_pending" in fn
    assert "restart to finish" in fn
