"""A stall clock that survives a restart, and notices "finished but unfindable".

Both defects were read off the live install.

**The clock was wiped by every restart.** Six torrents sat at the same percentage
for 199 minutes against a 30-minute timeout and were never failed — SoulSync had
restarted inside the window, and the clock, being a module dict keyed off
``time.monotonic()``, restarted with it. The perverse consequence: the longer a
download had been stuck, the more restarts it had survived, and the *less* likely
it was to ever be caught. Storing the clock on the row fixes it, and wall-clock is
also the measure a person uses looking at the page — "this hasn't moved since 4pm".

**Finishing with no findable file was not tracked at all.** When a torrent reports
complete and the importer can't locate its file, the patch carries progress and NO
status. The old branch matched only ``queued``/``downloading``, so that row fell
through every guard and spun at 100% forever. On a library spread over eleven mount
roots, an unresolvable save path is exactly how that happens — so it gets its own
message, because telling someone "no progress" when the bytes are already on disk
sends them hunting seeders that were never the problem.
"""

from __future__ import annotations

from core.video.stall import (
    DEFAULT_TIMEOUT_SECONDS,
    MOVED,
    SEEDED,
    STALLED,
    WAITING,
    classify,
    is_terminal,
    reason,
    tracks_stall,
)

_T = 1800          # the monitor's 30 minutes
_NOON = 1_775_000_000.0


def _at(seconds_ago):
    """A stored timestamp N seconds before _NOON, in SQLite's format."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(_NOON - seconds_ago, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── movement ─────────────────────────────────────────────────────────────────

def test_forward_progress_resets_the_clock():
    assert classify(10.0, 12.5, _at(9999), _NOON, timeout_seconds=_T)[0] == MOVED


def test_a_backwards_percentage_is_not_movement():
    """A recheck or a resumed torrent re-verifying briefly reports LESS. Reading
    that as progress hands a dead download a fresh half hour, every time it
    happens — which is how something stays queued forever while looking busy."""
    assert classify(50.0, 12.0, _at(_T + 60), _NOON, timeout_seconds=_T)[0] == STALLED


def test_standing_still_inside_the_window_just_waits():
    v, idle = classify(50.0, 50.0, _at(600), _NOON, timeout_seconds=_T)
    assert v == WAITING and 590 <= idle <= 610


def test_standing_still_past_the_window_is_stalled():
    v, idle = classify(50.0, 50.0, _at(_T + 120), _NOON, timeout_seconds=_T)
    assert v == STALLED and idle > _T


def test_the_boundary_is_not_off_by_one():
    assert classify(1, 1, _at(_T - 1), _NOON, timeout_seconds=_T)[0] == WAITING
    assert classify(1, 1, _at(_T + 1), _NOON, timeout_seconds=_T)[0] == STALLED


# ── the restart that started all this ────────────────────────────────────────

def test_the_clock_is_wall_clock_so_a_restart_cannot_reset_it():
    """The exact live case: stuck 199 minutes, timeout 30, and the process only
    came up 29 minutes ago. Under the old uptime-based clock this was invisible.
    Nothing here reads process uptime, so a fresh start changes nothing."""
    v, idle = classify(47.8, 47.8, _at(199 * 60), _NOON, timeout_seconds=_T)
    assert v == STALLED
    assert idle > 199 * 60 - 5


def test_a_row_with_no_clock_yet_starts_one_rather_than_failing():
    """Every existing row is like this the first time the new code sees it. Reading
    a missing timestamp as 'infinitely stalled' would fail the entire queue on the
    first tick after an upgrade."""
    for empty in (None, "", "   "):
        assert classify(0, 0, empty, _NOON, timeout_seconds=_T)[0] == SEEDED


def test_an_unreadable_timestamp_is_treated_as_no_clock_not_as_forever():
    for bad in ("not a date", "13/08/2026", "yesterday", object()):
        assert classify(0, 0, bad, _NOON, timeout_seconds=_T)[0] == SEEDED, bad


def test_an_epoch_number_is_accepted_too():
    assert classify(1, 1, _NOON - (_T + 60), _NOON, timeout_seconds=_T)[0] == STALLED


def test_junk_progress_never_raises():
    for bad in (None, "", "abc", object()):
        assert classify(bad, bad, _at(10), _NOON, timeout_seconds=_T)[0] in (
            MOVED, WAITING, STALLED, SEEDED)


def test_a_zero_or_silly_timeout_cannot_fail_everything_instantly():
    assert classify(1, 1, _at(0), _NOON, timeout_seconds=0)[0] == WAITING


def test_the_default_timeout_matches_the_monitor():
    assert DEFAULT_TIMEOUT_SECONDS == 1800


# ── what the row is told ─────────────────────────────────────────────────────

def test_an_ordinary_stall_reports_the_minutes():
    assert reason(STALLED, 199 * 60) == "Stalled — no progress for 199 min"


def test_finishing_with_no_findable_file_says_that_instead():
    """These are different problems. One is nobody seeding; the other is a file
    that IS on disk somewhere SoulSync can't reach — a path question. Saying 'no
    progress' for the second sends people hunting seeders."""
    msg = reason(STALLED, 40 * 60, at_completion=True)
    assert "never appeared" in msg and "save path" in msg
    assert "no progress" not in msg.lower()


def test_the_reason_survives_junk_input():
    for bad in (None, "", -5, "x"):
        assert isinstance(reason(STALLED, bad), str)


def test_terminal_states_are_recognised():
    for s in ("completed", "failed", "cancelled", "import_failed"):
        assert is_terminal(s) is True
    for s in ("downloading", "queued", "importing", "searching", None, ""):
        assert is_terminal(s) is False


def test_an_import_in_flight_is_never_stall_killed():
    """An import sits at 100% for as long as the copy takes, and a multi-GB file
    over SMB can pass the 30-minute timeout easily. Failing it for 'no progress'
    would destroy a download that was working perfectly — the exact regression
    that widening the branch beyond queued/downloading could have introduced."""
    assert tracks_stall("importing") is False


def test_a_searching_row_is_left_to_its_own_thread():
    assert tracks_stall("searching") is False


def test_the_states_that_are_watched():
    for s in ("downloading", "queued", None, ""):
        assert tracks_stall(s) is True, s
    for s in ("completed", "failed", "cancelled", "import_failed"):
        assert tracks_stall(s) is False, s


def test_the_no_status_patch_is_watched():
    """The completed-but-unplaceable case carries progress and no status at all."""
    assert tracks_stall(None) is True


# ── the monitor actually uses it ─────────────────────────────────────────────

def _monitor_src() -> str:
    import inspect
    from pathlib import Path
    from core.video import download_monitor
    return Path(inspect.getfile(download_monitor)).read_text(encoding="utf-8")


def test_the_in_memory_clock_is_gone_for_good():
    """Leaving it in place alongside the row-stored one would quietly restore the
    restart bug for whichever path still read it."""
    src = _monitor_src()
    # Name the DICT operations, not the bare substring — 'tracks_stall' contains
    # '_stall' and a loose match would pass or fail for the wrong reason.
    for dead in ("_stall[", "_stall.get(", "_stall.pop(", "_stall: dict", "_stall ="):
        assert dead not in src, dead
    # monotonic is fine elsewhere in this file (a search deadline); what must not
    # come back is a stall decision measured against process uptime.
    tick = src[src.index("def _tick("):]
    assert "monotonic" not in tick, "uptime is what made the stall clock unreliable"


def test_the_monitor_stores_the_clock_on_the_row():
    assert 'upd["progress_at"] = _now()' in _monitor_src()


def test_the_completion_case_is_no_longer_skipped():
    """The old branch matched only queued/downloading; a completed-but-unplaceable
    patch has NO status and fell through everything."""
    src = _monitor_src()
    assert "stall.tracks_stall" in src
    assert "at_completion" in src


def test_the_column_rides_the_migration_list():
    """Live installs upgrade in place — a column added only to CREATE TABLE would
    be missing on every existing database."""
    from pathlib import Path
    db = Path("database/video_database.py").read_text(encoding="utf-8")
    start = db.index("_COLUMN_MIGRATIONS = [")
    end = db.index("]", db.index('("shows", "tvdb_match_status"'))
    assert '("video_downloads", "progress_at", "TEXT")' in db[start:end]
