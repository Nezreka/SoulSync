"""Jobs that checked stop but not pause ran straight through Pause."""
from types import SimpleNamespace

from core.repair_jobs.base import stop_or_pause


def test_a_running_job_waits_on_pause_and_carries_on():
    held = []
    context = SimpleNamespace(check_stop=lambda: False,
                              wait_if_paused=lambda: held.append(True) or False)
    assert stop_or_pause(context) is False
    assert held == [True]


def test_a_stop_while_paused_stops():
    context = SimpleNamespace(check_stop=lambda: False, wait_if_paused=lambda: True)
    assert stop_or_pause(context) is True


def test_a_context_without_pause_only_stops():
    assert stop_or_pause(SimpleNamespace(check_stop=lambda: True)) is True
    assert stop_or_pause(SimpleNamespace(check_stop=lambda: False)) is False
