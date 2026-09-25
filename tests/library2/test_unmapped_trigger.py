"""Post-import trigger for the unmapped-artist reconcile (status.md §28).

The user asked for the reconcile job to run automatically once an import
finishes. Two things have to hold for that to be safe: several imported files
in a row must collapse into ONE run (a 30-track album import fires the hook 30
times), and the run must carry the per-artist cooldown from issues.md §16
Finding 2 so a permanently unresolvable name is not re-asked at every trigger.
"""

import threading

import core.library2.unmapped_trigger as UT


class _Config:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)


def _reset():
    UT.reset_for_tests()


def _wait(done, timeout=5.0):
    assert done.wait(timeout), "reconcile runner was never invoked"


def test_trigger_runs_the_reconcile_with_the_configured_cooldown():
    _reset()
    done, seen = threading.Event(), {}

    def runner(*, cooldown_hours):
        seen["cooldown_hours"] = cooldown_hours
        done.set()
        return {"scanned": 0}

    armed = UT.schedule_unmapped_artist_reconcile(
        _Config({"library_v2.unmapped_reconcile.debounce_seconds": 0,
                 "library_v2.unmapped_reconcile.cooldown_hours": 48}),
        runner=runner,
    )

    assert armed is True
    _wait(done)
    assert seen["cooldown_hours"] == 48


def test_default_cooldown_is_a_week():
    _reset()
    done, seen = threading.Event(), {}

    def runner(*, cooldown_hours):
        seen["cooldown_hours"] = cooldown_hours
        done.set()
        return {}

    UT.schedule_unmapped_artist_reconcile(
        _Config({"library_v2.unmapped_reconcile.debounce_seconds": 0}), runner=runner,
    )

    _wait(done)
    assert seen["cooldown_hours"] == UT.DEFAULT_COOLDOWN_HOURS


def test_a_burst_of_imports_collapses_into_one_run():
    _reset()
    done = threading.Event()
    runs = []

    def runner(*, cooldown_hours):
        runs.append(cooldown_hours)
        done.set()
        return {}

    # A 30-track album import calls the hook once per file inside a second.
    config = _Config({"library_v2.unmapped_reconcile.debounce_seconds": 0.3})
    armed = [UT.schedule_unmapped_artist_reconcile(config, runner=runner)
             for _ in range(25)]

    _wait(done)
    UT.wait_for_idle(5.0)

    assert runs == [UT.DEFAULT_COOLDOWN_HOURS], "one run per burst, not one per file"
    assert armed.count(True) == 1


def test_a_trigger_arriving_mid_run_is_re_armed_not_dropped():
    _reset()
    inside_first, release_first = threading.Event(), threading.Event()
    second = threading.Event()
    runs = []

    def runner(*, cooldown_hours):
        runs.append(cooldown_hours)
        if len(runs) == 1:
            inside_first.set()
            release_first.wait(5.0)
        else:
            second.set()
        return {}

    config = _Config({"library_v2.unmapped_reconcile.debounce_seconds": 0.05})
    UT.schedule_unmapped_artist_reconcile(config, runner=runner)
    assert inside_first.wait(5.0)

    # This import's artists are NOT in the in-flight run's candidate list.
    UT.schedule_unmapped_artist_reconcile(config, runner=runner)
    release_first.set()

    assert second.wait(5.0), "the mid-run trigger was dropped"
    UT.wait_for_idle(5.0)
    assert len(runs) == 2


def test_trigger_can_be_switched_off():
    _reset()
    calls = []

    armed = UT.schedule_unmapped_artist_reconcile(
        _Config({"library_v2.unmapped_reconcile.auto_after_import": False,
                 "library_v2.unmapped_reconcile.debounce_seconds": 0}),
        runner=lambda *, cooldown_hours: calls.append(cooldown_hours),
    )

    assert armed is False
    assert calls == []


def test_a_failing_run_does_not_wedge_the_trigger():
    _reset()
    first, second = threading.Event(), threading.Event()
    attempts = []

    def runner(*, cooldown_hours):
        attempts.append(cooldown_hours)
        (first if len(attempts) == 1 else second).set()
        raise RuntimeError("provider stack is down")

    config = _Config({"library_v2.unmapped_reconcile.debounce_seconds": 0})
    UT.schedule_unmapped_artist_reconcile(config, runner=runner)
    _wait(first)
    UT.wait_for_idle(5.0)

    UT.schedule_unmapped_artist_reconcile(config, runner=runner)
    _wait(second)

    assert len(attempts) == 2


def test_scheduling_never_raises_into_the_import_pipeline():
    _reset()

    class _Exploding:
        def get(self, key, default=None):
            raise RuntimeError("config backend unavailable")

    # The hook sits in the post-import side effects; it must never be able to
    # fail an import that already landed on disk.
    assert UT.schedule_unmapped_artist_reconcile(_Exploding()) is False
