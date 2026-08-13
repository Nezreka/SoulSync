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
