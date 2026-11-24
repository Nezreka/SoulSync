"""slskd logged out of Soulseek: downloads wait for the login, spend no
retries, resume with the same candidate, and move on after the deadline."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core import soulseek_session as ss
from core.downloads import monitor as dm
from core.downloads import task_worker as tw
from core.runtime_state import download_tasks, tasks_lock


class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


# ── the shared state and its events ───────────────────────────────────────

def test_one_event_per_change_and_none_changes_nothing():
    sent = []
    ss.set_event_emitter(lambda e, d: sent.append((e, d)))
    assert ss.note(True, now=100) is None              # unknown -> in: no news
    assert ss.note(False, now=200) == "soulseek_logged_out"
    assert ss.note(False, now=260) is None             # still out: once per logout
    assert ss.note(None, now=300) is None              # slskd silent: still out
    assert ss.is_logged_out()
    assert ss.note(True, now=800) == "soulseek_logged_in"
    assert sent == [("soulseek_logged_out", {"reason": "not_logged_in"}),
                    ("soulseek_logged_in", {"was_out_minutes": 10.0})]
    assert ss.snapshot()["logged_out"] is False


def test_a_logout_seen_before_the_engine_was_wired_is_still_announced():
    ss.note(False)
    sent = []
    ss.set_event_emitter(lambda e, d: sent.append(e))
    assert sent == ["soulseek_logged_out"]


def test_the_triggers_exist_for_the_builder_and_belong_to_the_install():
    from core.automation.blocks import TRIGGERS, block_category
    from core.automation.owners import INSTALL_EVENTS
    triggers = {t["type"]: t for t in TRIGGERS}
    assert triggers["soulseek_logged_out"]["variables"] == ["reason"]
    assert triggers["soulseek_logged_in"]["variables"] == ["was_out_minutes"]
    assert {"soulseek_logged_out", "soulseek_logged_in"} <= INSTALL_EVENTS
    assert block_category("soulseek_logged_out") in {"Downloads", "Other"}


# ── waiting, the deadline, the plan ───────────────────────────────────────

def test_a_task_waits_then_gives_up_at_the_deadline():
    ss.note(False)
    cfg = _Cfg(**{"soulseek.login_wait_minutes": 45})
    task = {}
    assert ss.should_wait(task, now=1000, config_manager=cfg) is True
    assert task["waiting_for_login"] is True
    assert ss.should_wait(task, now=1000 + 44 * 60, config_manager=cfg) is True
    assert ss.should_wait(task, now=1000 + 45 * 60, config_manager=cfg) is False
    assert task["_soulseek_login_gave_up"] is True and "waiting_for_login" not in task
    ss.note(True)
    assert ss.should_wait(task, config_manager=cfg) is False
    assert "_soulseek_login_gave_up" not in task          # the next logout is a new wait


def test_zero_minutes_never_waits():
    ss.note(False)
    assert ss.should_wait({}, config_manager=_Cfg(**{"soulseek.login_wait_minutes": 0})) is False


class _Orch:
    def __init__(self, mode, chain=()):
        self.mode = mode
        self.hybrid_order = list(chain)

    def _resolve_source_chain(self):
        return list(self.hybrid_order)


@pytest.mark.parametrize("mode,chain,exclude,plan", [
    ("soulseek", (), [], "wait"),
    ("hybrid", ("soulseek", "youtube"), [], "wait"),
    ("hybrid", ("youtube", "soulseek"), [], "skip"),
    ("hybrid", ("soulseek", "youtube"), ["soulseek"], "search"),
    ("hybrid", ("tidal", "youtube"), [], "search"),
    ("tidal", (), [], "search"),
])
def test_the_search_plan_while_logged_out(mode, chain, exclude, plan):
    ss.note(False)
    assert ss.search_plan(_Orch(mode, chain), exclude) == plan


def test_logged_in_always_searches():
    ss.note(True)
    assert ss.search_plan(_Orch("soulseek"), []) == "search"


# ── slskd's answer, shared with chat ─────────────────────────────────────

class _Client:
    def __init__(self, responses):
        self.base_url = "http://slskd"
        self._responses = responses

        self.asked = []

    async def _make_request(self, method, endpoint, **kwargs):
        self.asked.append(endpoint)
        return self._responses.get(endpoint)


def _login_state(responses):
    from core.soulseek_client import SoulseekClient
    client = _Client(responses)
    return asyncio.run(SoulseekClient.get_login_state(client))


def test_login_state_reads_application_then_server():
    assert _login_state({"server": {"isConnected": True, "isLoggedIn": True}}) is True
    assert _login_state({"server": {"isConnected": True, "isLoggedIn": False}}) is False
    assert _login_state({"application": {"server": {"isConnected": True, "isLoggedIn": True}}}) is True
    assert _login_state({}) is None      # slskd said nothing: unknown, never "logged out"


# slskd 0.26.2 answers /server with 403 to non-admin api keys (None here):
# the application state, open to every role, is asked first, so a poll that
# gets its answer there never asks /server and never logs that 403.
_APP_LOGGED_IN = {"application": {"server": {"isConnected": True, "isLoggedIn": True},
                                  "user": {"username": "BoulderBadgeDad"}}}


def test_the_login_poll_never_asks_the_admin_only_server_controller():
    from core.soulseek_client import SoulseekClient
    client = _Client(_APP_LOGGED_IN)
    assert asyncio.run(SoulseekClient.get_login_state(client)) is True
    assert client.asked == ["application"]


def test_the_connection_check_reads_the_same_state():
    from core.soulseek_client import SoulseekClient
    client = _Client({"application": {"server": {"isConnected": True, "isLoggedIn": False}}})
    client.get_login_state = lambda: SoulseekClient.get_login_state(client)
    assert asyncio.run(SoulseekClient.check_connection(client)) is False
    assert "server" not in client.asked

    # no endpoint carries the state (older slskd): reachable API is enough
    old = _Client({"session": {"token": "x"}})
    old.get_login_state = lambda: SoulseekClient.get_login_state(old)
    assert asyncio.run(SoulseekClient.check_connection(old)) is True


def test_the_username_comes_from_the_application_state():
    from core.soulseek_client import SoulseekClient
    client = _Client(_APP_LOGGED_IN)
    assert asyncio.run(SoulseekClient.get_soulseek_username(client)) == "BoulderBadgeDad"
    assert client.asked == ["application"]
    # builds that keep it elsewhere still resolve it
    old = _Client({"server": {"username": "BoulderBadgeDad"}})
    assert asyncio.run(SoulseekClient.get_soulseek_username(old)) == "BoulderBadgeDad"


def test_chat_feeds_the_shared_state():
    from core.soulseek_client import SoulseekClient
    client = _Client({"server": {"isConnected": True, "isLoggedIn": False}})
    client.get_login_state = lambda: SoulseekClient.get_login_state(client)
    state = asyncio.run(SoulseekClient.get_chat_connection_state(client))
    assert state["code"] == "slskd_disconnected"
    assert ss.is_logged_out()


def test_the_poller_probe_records_the_answer():
    class _C:
        base_url = "http://slskd"

        async def get_login_state(self):
            return False

    assert ss.probe(_C(), asyncio.run) is False
    assert ss.is_logged_out()


# ── the task worker waits instead of searching ───────────────────────────

@pytest.fixture
def task():
    with tasks_lock:
        download_tasks["t-login"] = {"status": "searching"}
    yield download_tasks["t-login"]
    with tasks_lock:
        download_tasks.pop("t-login", None)


def test_the_worker_waits_then_searches_once_logged_back_in(task, monkeypatch):
    monkeypatch.setattr(tw, "LOGIN_WAIT_POLL_SECONDS", 0.05)
    ss.note(False)
    deps = type("D", (), {"download_orchestrator": _Orch("soulseek")})()
    seen = []

    def _back():
        deadline = time.time() + 5
        while time.time() < deadline and not task.get("waiting_for_login"):
            time.sleep(0.01)
        seen.append(dict(task))
        ss.note(True)

    threading.Thread(target=_back, daemon=True).start()
    assert tw._wait_for_soulseek_login("t-login", deps, []) == "search"
    assert seen and seen[0]["waiting_for_login"] is True     # it waited, visibly
    assert "waiting_for_login" not in task


def test_a_cancelled_task_stops_waiting(task, monkeypatch):
    monkeypatch.setattr(tw, "LOGIN_WAIT_POLL_SECONDS", 0.05)
    ss.note(False)
    deps = type("D", (), {"download_orchestrator": _Orch("soulseek")})()
    threading.Timer(0.2, lambda: task.update(status="cancelled")).start()
    assert tw._wait_for_soulseek_login("t-login", deps, []) == "gone"


def test_past_the_deadline_a_hybrid_task_moves_to_its_next_source(task, monkeypatch):
    ss.note(False)
    monkeypatch.setattr(ss, "wait_seconds", lambda cfg=None: 0)
    deps = type("D", (), {"download_orchestrator": _Orch("hybrid", ("soulseek", "youtube"))})()
    assert tw._wait_for_soulseek_login("t-login", deps, []) == "skip"


def test_soulseek_later_in_the_chain_is_left_out_not_waited_for(task):
    ss.note(False)
    deps = type("D", (), {"download_orchestrator": _Orch("hybrid", ("youtube", "soulseek"))})()
    assert tw._wait_for_soulseek_login("t-login", deps, []) == "skip"


# ── the monitor holds transfers, then resumes the same one ───────────────

@pytest.fixture
def monitor(monkeypatch):
    monkeypatch.setattr(dm, "_make_context_key", lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm, "_orphaned_download_keys", set(), raising=False)
    return dm.WebUIDownloadMonitor()


def _slsk_task():
    return {"track_info": {"name": "Song"}, "username": "peer1", "filename": "a/song.flac",
            "status": "downloading", "download_id": "dl-1", "batch_id": "b1",
            "picked_candidate": {"size": 1234},
            "status_change_time": time.time() - 1000, "used_sources": set()}


ERRORED = {"peer1::a/song.flac": {"state": "Completed, Errored", "percentComplete": 0}}


def test_an_errored_transfer_while_logged_out_spends_nothing(monitor):
    ss.note(False)
    task, ops = _slsk_task(), []
    assert monitor._should_retry_task("t1", task, ERRORED, time.time(), ops) is False
    assert ops == []
    assert task.get("error_retry_count", 0) == 0
    assert task["used_sources"] == set()
    assert task["status"] == "downloading" and task["waiting_for_login"] is True


def test_back_in_the_same_peer_is_asked_again(monitor):
    ss.note(False)
    task, ops = _slsk_task(), []
    monitor._should_retry_task("t1", task, ERRORED, time.time(), ops)
    ss.note(True)
    assert monitor._should_retry_task("t1", task, ERRORED, time.time(), ops) is False
    assert ops == [("resume_soulseek_download", "t1", "dl-1", "peer1", "a/song.flac", 1234)]
    assert task["status"] == "queued"
    assert task["used_sources"] == set()                    # the candidate is still good
    assert task.get("error_retry_count", 0) == 0


def test_logged_in_an_errored_transfer_retries_as_before(monitor):
    ss.note(True)
    task, ops = _slsk_task(), []
    monitor._should_retry_task("t1", task, ERRORED, time.time(), ops)
    assert task["error_retry_count"] == 1
    assert "peer1_a/song.flac" in task["used_sources"]
    assert not any(op[0] == "resume_soulseek_download" for op in ops)


def test_resume_enqueues_the_same_file(monkeypatch):
    calls = []

    class _O:
        async def cancel_download(self, *a, **k):
            calls.append(("cancel", a))
            return True

        async def download(self, username, filename, size):
            calls.append(("download", username, filename, size))
            return "dl-2"

    monkeypatch.setattr(dm, "download_orchestrator", _O(), raising=False)
    monkeypatch.setattr(dm, "run_async", asyncio.run)
    with tasks_lock:
        download_tasks["t-res"] = {"status": "queued", "batch_id": "b1"}
    try:
        dm._resume_soulseek_download("t-res", "dl-1", "peer1", "a/song.flac", 1234)
        assert ("download", "peer1", "a/song.flac", 1234) in calls
        assert download_tasks["t-res"]["download_id"] == "dl-2"
    finally:
        with tasks_lock:
            download_tasks.pop("t-res", None)


# ── the video side holds its Soulseek downloads too ──────────────────────

class _VideoDb:
    def __init__(self):
        self.updates = []

    def update_video_download(self, dl_id, **fields):
        self.updates.append((dl_id, fields))


@pytest.fixture
def vmon(monkeypatch):
    from core.video import download_monitor as vm
    vm._login_hold.clear()
    monkeypatch.setattr(ss, "wait_seconds", lambda cfg=None: 45 * 60)
    started = []
    monkeypatch.setattr(vm, "start_download", lambda u, f, s=0: started.append((u, f, s)) or {"ok": True})
    yield vm, started
    vm._login_hold.clear()


VDL = {"id": 7, "username": "peer1", "filename": "Show/ep1.mkv", "size_bytes": 99, "source": "soulseek"}
VFAILED = [{"username": "peer1", "filename": "Show/ep1.mkv", "state": "Completed, Errored"}]


def test_video_row_is_held_while_logged_out_and_asked_again_after(vmon):
    vm, started = vmon
    db = _VideoDb()
    ss.note(False)
    assert vm._hold_for_soulseek_login(db, VDL, VFAILED) is True
    assert db.updates and "progress_at" in db.updates[-1][1]     # stall clock held
    assert started == []
    ss.note(True)
    assert vm._hold_for_soulseek_login(db, VDL, VFAILED) is True
    assert started == [("peer1", "Show/ep1.mkv", 99)]
    assert db.updates[-1][1]["status"] == "queued"
    # and only once: the next tick is an ordinary one
    assert vm._hold_for_soulseek_login(db, VDL, VFAILED) is False


def test_video_row_logged_in_is_never_held(vmon):
    vm, started = vmon
    ss.note(True)
    assert vm._hold_for_soulseek_login(_VideoDb(), VDL, VFAILED) is False
    assert started == []


def test_video_row_past_the_deadline_goes_the_normal_way(vmon, monkeypatch):
    vm, _started = vmon
    ss.note(False)
    monkeypatch.setattr(ss, "wait_seconds", lambda cfg=None: 0)
    assert vm._hold_for_soulseek_login(_VideoDb(), VDL, VFAILED) is False
