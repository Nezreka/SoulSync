import pytest

import core.runtime_state as runtime_state


def test_mark_task_completed_requires_tasks_lock():
    original_tasks = dict(runtime_state.download_tasks)
    runtime_state.download_tasks.clear()
    runtime_state.download_tasks["task-1"] = {"status": "running", "stream_processed": False}

    try:
        with pytest.raises(RuntimeError, match="tasks_lock"):
            runtime_state.mark_task_completed("task-1")
    finally:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(original_tasks)


def test_mark_task_completed_succeeds_when_lock_held():
    original_tasks = dict(runtime_state.download_tasks)
    runtime_state.download_tasks.clear()
    runtime_state.download_tasks["task-1"] = {"status": "running", "stream_processed": False}

    try:
        with runtime_state.tasks_lock:
            assert runtime_state.mark_task_completed("task-1", {"name": "Song One"}) is True
        assert runtime_state.download_tasks["task-1"]["status"] == "completed"
        assert runtime_state.download_tasks["task-1"]["stream_processed"] is True
        assert runtime_state.download_tasks["task-1"]["track_info"] == {"name": "Song One"}
    finally:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(original_tasks)


@pytest.fixture
def _isolated_tasks():
    original_tasks = dict(runtime_state.download_tasks)
    runtime_state.download_tasks.clear()
    yield runtime_state.download_tasks
    runtime_state.download_tasks.clear()
    runtime_state.download_tasks.update(original_tasks)


@pytest.mark.parametrize("start_status", ["downloading", "queued"])
def test_claim_for_post_processing_wins_from_active_status(_isolated_tasks, start_status):
    _isolated_tasks["t1"] = {"status": start_status}
    assert runtime_state.claim_for_post_processing("t1") is True
    assert _isolated_tasks["t1"]["status"] == "post_processing"


@pytest.mark.parametrize("start_status", ["post_processing", "searching", "completed", "failed", "cancelled"])
def test_claim_for_post_processing_loses_when_already_owned(_isolated_tasks, start_status):
    """A task already being post-processed by the monitor (post_processing),
    requeued by a quarantine retry (searching), or already terminal must NOT be
    re-claimed — that is the double-processing race that produced the bogus
    'missing file or source information' failure."""
    _isolated_tasks["t1"] = {"status": start_status}
    assert runtime_state.claim_for_post_processing("t1") is False
    assert _isolated_tasks["t1"]["status"] == start_status  # untouched


def test_claim_for_post_processing_missing_task_returns_false(_isolated_tasks):
    assert runtime_state.claim_for_post_processing("absent") is False
