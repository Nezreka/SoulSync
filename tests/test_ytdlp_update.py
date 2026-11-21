"""One-click yt-dlp update — and the three things it must not lie about.

Why this exists: the live install ran yt-dlp 2026.06.09 into August, and 22 videos
came back ``HTTP Error 403: Forbidden`` across many unrelated channels. One had
already burned all three retry attempts and been skipped permanently. The cause was
a package update the user had no in-app way to run.

The tests below are mostly about honesty rather than mechanics:

  · the update does NOT take effect until a restart, because ``yt_dlp`` is already
    imported and replacing files on disk does not replace a loaded module. A button
    that implies otherwise gets used once, appears to do nothing, and is never
    trusted again.
  · nightly is the default on purpose — stable lags on exactly the extractor fixes
    this exists to deliver — but it stays a choice.
  · pip fails in several ways that need completely different fixes (read-only
    container, distro-managed Python, wrong user, no network). Collapsing them into
    "update failed" sends people hunting the wrong thing.
"""

from __future__ import annotations

import json

import pytest

from core.ytdlp_update import (
    NIGHTLY,
    STABLE,
    installed_version,
    interpret_result,
    is_behind,
    normalize_channel,
    parse_pypi,
    pip_command,
    run_update,
)


# ── the channel ──────────────────────────────────────────────────────────────

def test_nightly_is_the_default():
    """Stable lags on extractor fixes, which is the entire failure this addresses."""
    assert normalize_channel(None) == NIGHTLY
    assert normalize_channel("") == NIGHTLY
    assert normalize_channel("anything unrecognised") == NIGHTLY


def test_stable_is_still_selectable():
    assert normalize_channel("stable") == STABLE
    assert normalize_channel("  STABLE  ") == STABLE


def test_only_nightly_passes_pre():
    """--pre is the whole difference; yt-dlp ships nightlies as pre-releases of the
    same PyPI package."""
    assert "--pre" in pip_command(NIGHTLY, "/py")
    assert "--pre" not in pip_command(STABLE, "/py")


def test_the_command_matches_what_yt_dlp_documents():
    assert pip_command(NIGHTLY, "/usr/bin/python3") == [
        "/usr/bin/python3", "-m", "pip", "install", "-U", "--pre", "yt-dlp[default]"]


def test_the_command_uses_the_interpreter_it_is_given():
    """It must install into the environment SoulSync is RUNNING in, not whatever
    'pip' happens to be on PATH — otherwise the update lands somewhere unused."""
    assert pip_command(STABLE, "/opt/venv/bin/python")[0] == "/opt/venv/bin/python"


# ── reading PyPI ─────────────────────────────────────────────────────────────

_PAYLOAD = json.dumps({
    "info": {"version": "2026.06.09"},
    "releases": {"2026.05.01": [], "2026.06.09": [], "2026.08.11.232712": []},
})


def test_stable_is_the_release_pypi_calls_current():
    assert parse_pypi(_PAYLOAD, STABLE) == "2026.06.09"


def test_nightly_finds_the_prerelease_stable_never_reports():
    """A nightly is not info.version — that is the point of --pre, and why the
    installed 2026.06.09 looked current while two months of fixes existed."""
    assert parse_pypi(_PAYLOAD, NIGHTLY) == "2026.08.11.232712"


def test_date_versions_compare_numerically_not_as_strings():
    """'2026.6.9' vs '2026.06.09' — a string compare gets this backwards."""
    payload = json.dumps({"info": {"version": "2026.6.9"},
                          "releases": {"2026.6.9": [], "2026.10.1": []}})
    assert parse_pypi(payload, NIGHTLY) == "2026.10.1"


def test_nightly_falls_back_to_stable_when_no_newer_build_exists():
    payload = json.dumps({"info": {"version": "2026.08.11"},
                          "releases": {"2026.07.01": [], "2026.08.11": []}})
    assert parse_pypi(payload, NIGHTLY) == "2026.08.11"


def test_a_broken_pypi_reply_yields_nothing_rather_than_raising():
    for bad in ("not json", "", None, "[]", json.dumps({"info": {}}), 42):
        assert parse_pypi(bad, NIGHTLY) in (None, "")


def test_behind_is_only_true_when_it_is_knowably_behind():
    assert is_behind("2026.06.09", "2026.08.11.232712") is True
    assert is_behind("2026.08.11", "2026.06.09") is False
    assert is_behind("2026.06.09", "2026.06.09") is False
    # unknown either side must not accuse the user of being out of date
    assert is_behind(None, "2026.08.11") is False
    assert is_behind("2026.06.09", None) is False


def test_the_installed_version_is_the_one_loaded_in_this_process():
    """The loaded module is what actually downloads, so it is the one whose
    staleness matters — not whatever happens to be on disk."""
    class _V:
        __version__ = "2026.06.09"

    class _Mod:
        version = _V()
    assert installed_version(importer=lambda: _Mod()) == "2026.06.09"


def test_a_missing_yt_dlp_reports_nothing_rather_than_exploding():
    def boom():
        raise ImportError("no yt_dlp here")
    assert installed_version(importer=boom) is None


# ── what the user is told ────────────────────────────────────────────────────

def test_a_successful_update_always_says_a_restart_is_needed():
    """THE thing this must not get wrong. yt_dlp is already imported; the new files
    on disk are not the module doing the work. Without this line the user updates,
    retries, gets the same 403, and concludes the button is broken."""
    r = interpret_result(0, "Successfully installed yt-dlp-2026.08.11", "")
    assert r["ok"] is True and r["changed"] is True
    assert r["restart_required"] is True
    assert "restart" in r["message"].lower()


def test_already_current_does_not_demand_a_pointless_restart():
    r = interpret_result(0, "Requirement already satisfied: yt-dlp[default]", "")
    assert r["ok"] is True and r["changed"] is False and r["restart_required"] is False


@pytest.mark.parametrize("err,expect", [
    ("ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied",
     "permission"),
    ("error: externally-managed-environment", "operating system"),
    ("/usr/bin/python3: No module named pip", "pip isn't available"),
    ("ERROR: Could not find a version that satisfies the requirement yt-dlp", "PyPI"),
    ("OSError: [Errno 30] Read-only file system: '/usr/lib/python3'", "read-only"),
])
def test_each_failure_names_its_own_fix(err, expect):
    """These need completely different actions — run as another user, use the distro
    package manager, rebuild the image, fix the network. 'Update failed' sends
    people hunting the wrong one."""
    r = interpret_result(1, "", err)
    assert r["ok"] is False
    assert expect.lower() in r["message"].lower(), r["message"]


def test_an_unrecognised_failure_still_hands_over_pip_s_own_words():
    r = interpret_result(2, "", "something entirely new went wrong")
    assert r["ok"] is False
    assert "something entirely new went wrong" in r["detail"]


def test_a_silent_failure_does_not_pretend_to_explain():
    r = interpret_result(1, "", "")
    assert r["ok"] is False and r["detail"]


def test_a_failure_never_claims_a_restart_would_help():
    for err in ("Permission denied", "externally-managed-environment", ""):
        assert interpret_result(1, "", err)["restart_required"] is False


def test_junk_return_codes_are_treated_as_failure():
    for code in (None, "x", object()):
        assert interpret_result(code, "", "")["ok"] is False


# ── running it ───────────────────────────────────────────────────────────────

def test_the_update_reports_the_channel_it_used():
    out = run_update(STABLE, python="/py", runner=lambda c, t: (0, "Successfully installed", ""))
    assert out["channel"] == STABLE and out["ok"] is True


def test_a_hanging_pip_is_bounded_not_left_to_wedge_the_server():
    seen = {}

    def runner(cmd, timeout):
        seen["timeout"] = timeout
        return 0, "Successfully installed", ""
    run_update(NIGHTLY, python="/py", runner=runner)
    assert seen["timeout"] and seen["timeout"] <= 600


def test_pip_blowing_up_becomes_a_message_not_a_500():
    def runner(cmd, timeout):
        raise OSError("no such interpreter")
    out = run_update(NIGHTLY, python="/py", runner=runner)
    assert out["ok"] is False and "no such interpreter" in out["message"]


def test_a_timeout_is_reported_rather_than_swallowed():
    import subprocess

    def runner(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    assert run_update(NIGHTLY, python="/py", runner=runner)["ok"] is False


# ── the Settings tile ────────────────────────────────────────────────────────
# The tile lives in the YouTube source panel on the shared Sources tab, with no
# side marker, so it renders on the music side (YouTube-sourced audio) and the
# video side (YouTube downloads) from one definition — it is one package. The
# panel is React now (SETTINGS_PORT.md step 7g): its markup and its script are
# the same file, source-panels-more.tsx.

def _html() -> str:
    # the Settings page is React (SETTINGS_PORT.md step V4c): read in its place
    from tests.support.settings_page import index_with_settings_page
    return index_with_settings_page()


def _panels() -> str:
    from pathlib import Path
    return Path("webui/src/features/settings/tabs/source-panels-more.tsx").read_text(encoding="utf-8")


def _youtube() -> str:
    """the YouTube panel component, markup and handlers together"""
    src = _panels()
    return src.split("export function YoutubePanel(", 1)[1].split("\nexport function", 1)[0]


def _tile() -> str:
    """Just the yt-dlp tile. Slicing a fixed width from its start reads whatever
    markup follows, which turns 'the tile must not say X' into 'no later tile may
    say X' — a false pass or a false failure depending on the day."""
    panel = _youtube()
    start = panel.index("the yt-dlp updater lives with YouTube")
    end = panel.index("</Panel>", start)
    return panel[start:end]


def test_the_tile_renders_on_both_sides():
    """Boulder asked for it on both. A data-video-only marker here would silently
    hide it from the music side, where YouTube is also a download source.

    This used to assert data-stg="advanced". The tile has since moved to live
    inside the YouTube source panel on the Sources tab - "it belongs where
    somebody debugging YouTube will actually look" - so that assertion was
    pinning an address it had already left. The Sources tab is shared by both
    sides, which is what actually delivers the both-sides requirement now. The
    panel is React (step 7g): it renders into a slot inside the Sources tab's
    hidden panel home, and its own Panel is the YouTube container.
    """
    tile = _tile()
    assert "data-video-only" not in tile and "data-music-only" not in tile

    import re

    # (settings-bic) Sources is a pill of Downloads, shared by both sides:
    # the panels are parked in the Downloads page, with no side on them, and
    # the Sources pill has none either
    from pathlib import Path
    tabs = Path("webui/src/features/settings")
    home = (tabs / "tabs/downloads/sources-view.tsx").read_text(encoding="utf-8")
    parked = home[home.index('<div id="source-config-home" hidden>'):]
    parked = parked[:parked.index("</div>")]
    assert '<MoreSourcePanels {...props} part="youtube" />' in parked
    assert "data-video-only" not in home and "data-music-only" not in home
    nav = (tabs / "nav.ts").read_text(encoding="utf-8")
    assert "{ key: 'sources', label: 'Sources' }," in nav
    assert '<Panel id="youtube-settings-container"' in _youtube(), (
        "no longer inside the YouTube panel - which is the whole reason it moved"
    )


def test_the_tile_states_the_restart_requirement_in_the_markup():
    """Not only in the API response — the user reads this BEFORE clicking, which
    is when it changes what they expect to happen."""
    assert "restart SoulSync" in _tile()


def test_nightly_is_the_preselected_channel():
    # React picks an option by the select's value, not a selected attribute
    assert "useState('nightly')" in _youtube()
    tile = _tile()
    assert "value: 'nightly'" in tile
    assert "value: 'stable'" in tile, "stable must remain a choice"


def test_every_element_the_script_touches_exists_in_the_markup():
    """The classic silent break: a renamed id leaves the button doing nothing at
    all, with no error anyone would notice. React renders every element it
    updates, so what has to hold is that each id the tile has is rendered once
    and nothing outside the panel still reaches for one."""
    import re
    tile = _tile()
    ids = re.findall(r'id="(ytdlp-[a-z-]+)"', tile)
    assert {"ytdlp-installed", "ytdlp-latest", "ytdlp-channel", "ytdlp-update-btn",
            "ytdlp-status", "ytdlp-detail"} <= set(ids)
    assert len(ids) == len(set(ids))
    from pathlib import Path
    js = Path("webui/src/features/settings/shell.ts").read_text(encoding="utf-8")
    assert not re.findall(r"getElementById\('(ytdlp-[a-z-]+)'\)", js), "settings.js still drives the tile"


def test_the_handlers_the_markup_calls_are_defined():
    """the old markup called globals by name; the panel calls its own functions,
    so each one the tile names must be defined in the panel"""
    import re
    tile, panel = _tile(), _youtube()
    called = set(re.findall(r"void ([a-zA-Z_$][\w$]*)\(", tile))
    assert called, "the tile should call its handlers"
    for fn in called:
        assert (f"async function {fn}(" in panel) or (f"function {fn}(" in panel), fn


def test_the_version_panel_loads_where_it_lives():
    """It used to load when the Advanced tab opened (the tile's old home), and
    the Sources tab got the same hook when it moved there. Both are gone with
    the vanilla tile: the React panel loads the version on arriving at Sources
    and when the YouTube modal opens."""
    panel = _youtube()
    assert "if (active && opened) void loadYtdlp();" in panel
    assert "useOnOpen(model, 'youtube', () => void loadYtdlp());" in panel
    from pathlib import Path
    js = Path("webui/src/features/settings/shell.ts").read_text(encoding="utf-8")
    assert "loadYtdlpStatus" not in js


def test_a_pypi_outage_does_not_read_as_up_to_date():
    """'Unknown' and 'newest available' are different facts. Showing the latter
    when we could not look would tell the user their 403s are something else."""
    assert "Couldn't check" in _youtube()


def test_the_toast_is_guarded_like_the_rest_of_the_codebase():
    """showToast is a global owned by downloads.js; chat.js guards it the same
    way. This tile renders on the video side too, so an unguarded call would throw
    and abandon the rest of the handler — leaving the button stuck on 'Updating...'
    after an update that actually succeeded. The panel toasts through the
    shared `toast`, which calls window.showToast?.()."""
    panel = _youtube()
    start = panel.index("async function runUpdate(")
    end = panel.index("\n  }\n", start)
    body = panel[start:end]
    assert "toast(" in body, "the handler should still toast"
    assert "showToast(" not in body, "a bare showToast call in the handler"
    from pathlib import Path
    parts = Path("webui/src/features/settings/tabs/source-panels.tsx").read_text(encoding="utf-8")
    assert "export const toast = (message: string, type: string) => window.showToast?.(message, type);" in parts
    # and the button is released whatever happens
    assert "finally {\n      setUpdating(false);" in body


def test_pip_s_own_output_is_kept_available():
    """Each failure mode needs a different action and the output is what says
    which — so it is shown, not swallowed into a generic message."""
    assert "ytdlp-detail" in _tile() and "data.detail" in _youtube()
