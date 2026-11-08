"""POST /api/downloads/clear-completed scopes and POST /api/downloads/remove.

No body is Clear everything, exactly as before: every terminal live task and
the whole history tail, review queue included. ``scope: "finished"`` keeps
failed rows and the review queue; ``scope: "failed"`` takes failed rows only
and touches no history. Remove takes one failed row and retries nothing.
"""

from __future__ import annotations

import pytest

from core.runtime_state import batch_locks, download_batches, download_tasks


class _FakeDB:
    def __init__(self):
        self.calls = []

    def clear_completed_download_history(self, keep_review=False):
        self.calls.append(keep_review)
        return 5


@pytest.fixture()
def ws(monkeypatch):
    import web_server
    db = _FakeDB()
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    download_tasks.clear()
    download_batches.clear()
    batch_locks.clear()
    for tid, status in (("c", "completed"), ("f", "failed"), ("x", "cancelled"),
                        ("d", "downloading")):
        download_tasks[tid] = {"status": status}
    yield web_server, db
    download_tasks.clear()
    download_batches.clear()
    batch_locks.clear()


def _post(web_server, view, body=None):
    kwargs = {"json": body} if body is not None else {}
    with web_server.app.test_request_context(method="POST", **kwargs):
        rv = view()
    resp, status = (rv if isinstance(rv, tuple) else (rv, 200))
    return status, resp.get_json()


def test_no_body_still_clears_everything(ws):
    web_server, db = ws
    status, body = _post(web_server, web_server.clear_completed_downloads)
    assert status == 200 and body["cleared"] == 3 and body["history_cleared"] == 5
    assert db.calls == [False]
    assert set(download_tasks) == {"d"}


def test_finished_scope_keeps_failed_rows_and_the_review_queue(ws):
    web_server, db = ws
    status, body = _post(web_server, web_server.clear_completed_downloads, {"scope": "finished"})
    assert status == 200 and body["cleared"] == 1
    assert db.calls == [True]
    assert set(download_tasks) == {"f", "x", "d"}


def test_failed_scope_touches_no_history(ws):
    web_server, db = ws
    status, body = _post(web_server, web_server.clear_completed_downloads, {"scope": "failed"})
    assert status == 200 and body == {"success": True, "cleared": 2,
                                       "history_cleared": 0, "total_cleared": 2}
    assert db.calls == []
    assert set(download_tasks) == {"c", "d"}


def test_unknown_scope_is_refused(ws):
    web_server, db = ws
    status, _ = _post(web_server, web_server.clear_completed_downloads, {"scope": "all"})
    assert status == 400
    assert db.calls == [] and len(download_tasks) == 4


def test_remove_takes_one_failed_row(ws):
    web_server, db = ws
    status, body = _post(web_server, web_server.remove_download_from_list, {"task_id": "f"})
    assert status == 200 and body == {"success": True}
    assert set(download_tasks) == {"c", "x", "d"}
    assert db.calls == []


@pytest.mark.parametrize("task_id,code,reason", [
    ("c", 409, "not_removable"),
    ("d", 409, "not_removable"),
    ("gone", 404, "missing"),
])
def test_remove_refusals(ws, task_id, code, reason):
    web_server, _ = ws
    status, body = _post(web_server, web_server.remove_download_from_list, {"task_id": task_id})
    assert status == code and body["reason"] == reason
    assert len(download_tasks) == 4


def test_remove_needs_a_task_id(ws):
    web_server, _ = ws
    assert _post(web_server, web_server.remove_download_from_list, {})[0] == 400
    assert _post(web_server, web_server.remove_download_from_list, {"task_id": 7})[0] == 400
