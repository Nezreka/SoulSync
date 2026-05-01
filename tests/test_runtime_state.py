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
