"""Torrent seeding lifecycle (arr-parity P5).

The importer copies torrent files into the library, so grabs seeded forever
with nothing ever letting go. The sweep releases a completed grab from the
client once the seed ratio/time goals are met — strictly opt-in (both goals
default 0 = off = old behavior), clock-fallback when a client doesn't report
seeding time, and the delete only ever touches the client's own copy.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.video.seeding as seeding
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


@pytest.fixture(autouse=True)
def _private_sweep_guard(monkeypatch):
    """Isolate the module-global already-running guard per test. A background
    automation thread leaked by an earlier test can be mid-sweep when a test
    here calls sweep(), which used to surface the guard's skip return in
    place of the real pass (the CI KeyError-'seeding' flake). A fresh flag
    and lock mean a stranger's in-flight sweep is invisible."""
    monkeypatch.setattr(seeding, "_running", False)
    monkeypatch.setattr(seeding, "_lock", threading.Lock())


def _torrent_row(db, ref="hash1", title="Heat"):
    did = db.add_video_download({"kind": "movie", "source": "torrent", "title": title,
                                 "status": "downloading", "target_dir": "/m",
                                 "client_ref": ref})
    db.update_video_download(did, status="completed",
                             completed_at=datetime.now(timezone.utc).isoformat())
    return did


# ---------------------------------------------------------------------------
# goals_met (pure)
# ---------------------------------------------------------------------------

def test_goals_met_matrix():
    cfg = {"seed_ratio_goal": 2.0, "seed_time_goal_hours": 48}
    dl = {"completed_at": datetime.now(timezone.utc).isoformat()}
    assert seeding.goals_met(SimpleNamespace(ratio=2.5, seeding_time=0), dl, cfg)
    assert seeding.goals_met(SimpleNamespace(ratio=0.1, seeding_time=49 * 3600), dl, cfg)
    assert seeding.goals_met(SimpleNamespace(ratio=0.1, seeding_time=1), dl, cfg) is None
    # both goals off → never released
    assert seeding.goals_met(SimpleNamespace(ratio=99, seeding_time=999999), dl,
                             {"seed_ratio_goal": 0, "seed_time_goal_hours": 0}) is None


def test_clock_fallback_when_client_reports_no_seed_time():
    cfg = {"seed_ratio_goal": 0, "seed_time_goal_hours": 24}
    old = {"completed_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()}
    fresh = {"completed_at": datetime.now(timezone.utc).isoformat()}
    st = SimpleNamespace(ratio=None, seeding_time=None)
    assert seeding.goals_met(st, old, cfg)
    assert seeding.goals_met(st, fresh, cfg) is None


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------

def test_sweep_is_off_without_goals(db):
    _torrent_row(db)
    out = seeding.sweep()
    assert out["status"] == "skipped" and out["reason"] == "no_goals_set"


def test_sweep_releases_met_keeps_seeding_and_clears_forgotten(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"seed_ratio_goal": 1.0})
    met = _torrent_row(db, ref="met")
    keep = _torrent_row(db, ref="keep")
    gone = _torrent_row(db, ref="gone")
    statuses = {"met": SimpleNamespace(ratio=1.5, seeding_time=None),
                "keep": SimpleNamespace(ratio=0.2, seeding_time=None),
                "gone": None}
    removed = []
    monkeypatch.setattr(seeding, "_status", lambda ref: (statuses[ref], True))
    monkeypatch.setattr(seeding, "_remove", lambda ref, delete_files: removed.append((ref, delete_files)) or True)
    out = seeding.sweep()
    assert out == {"status": "completed", "checked": 3, "released": 2, "seeding": 1}
    assert removed == [("met", True)]      # forgotten torrents aren't 'removed', just marked
    rows = {r["client_ref"]: r for r in db.get_all_video_downloads()} if hasattr(db, "get_all_video_downloads") else {}
    assert db.torrents_awaiting_seed_release() and \
        [r["client_ref"] for r in db.torrents_awaiting_seed_release()] == ["keep"]
    del met, keep, gone, rows


def test_failed_removal_retries_next_sweep(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"seed_ratio_goal": 1.0})
    _torrent_row(db, ref="met")
    monkeypatch.setattr(seeding, "_status",
                        lambda ref: (SimpleNamespace(ratio=2.0, seeding_time=None), True))
    monkeypatch.setattr(seeding, "_remove", lambda ref, delete_files: False)
    out = seeding.sweep()
    assert out["released"] == 0 and out["seeding"] == 1
    assert len(db.torrents_awaiting_seed_release()) == 1   # still managed


def test_a_client_that_does_not_answer_never_releases_anything(db, monkeypatch):
    """A down qBittorrent must not look like "the torrent is gone".

    Releasing is terminal: torrents_awaiting_seed_release filters released rows
    out for good, so one sweep during a client restart would abandon seed
    management for every row it touched, permanently and silently. The status
    helper this used to call collapsed a refused connection and a genuinely
    unknown torrent into the same None.
    """
    from core.video.download_config import save
    save(db, {"seed_ratio_goal": 1.0})
    _torrent_row(db, ref="a")
    _torrent_row(db, ref="b")
    removed = []
    monkeypatch.setattr(seeding, "_status", lambda ref: (None, False))
    monkeypatch.setattr(seeding, "_remove",
                        lambda ref, delete_files: removed.append(ref) or True)

    out = seeding.sweep()

    assert out["released"] == 0, "a silent client released rows"
    assert out["seeding"] == 2
    assert removed == [], "nothing may be removed from a client that isn't answering"
    # still managed, so the next sweep gets another go
    assert sorted(r["client_ref"] for r in db.torrents_awaiting_seed_release()) == ["a", "b"]


def test_a_torrent_the_client_genuinely_forgot_is_still_released(db, monkeypatch):
    """The other side of the same coin: an ANSWERING client saying it has no
    such torrent means there is nothing left to manage. That path has to keep
    working, or the fix above would just wedge every forgotten row forever."""
    from core.video.download_config import save
    save(db, {"seed_ratio_goal": 1.0})
    _torrent_row(db, ref="gone")
    monkeypatch.setattr(seeding, "_status", lambda ref: (None, True))
    out = seeding.sweep()
    assert out["released"] == 1
    assert db.torrents_awaiting_seed_release() == []


def test_already_running_skip_keeps_the_one_return_shape(monkeypatch):
    """Every sweep() return carries the same key set — the keyless
    already_running skip used to KeyError callers that index directly."""
    monkeypatch.setattr(seeding, "_running", True)
    out = seeding.sweep()
    assert out == {"status": "skipped", "reason": "already_running",
                   "checked": 0, "released": 0, "seeding": 0}


# ---------------------------------------------------------------------------
# config + adapter + wiring
# ---------------------------------------------------------------------------

def test_download_config_normalizes_and_defaults_off(db):
    from core.video.download_config import load, save
    assert load(db)["seed_ratio_goal"] == 0.0
    assert load(db)["seed_time_goal_hours"] == 0
    assert load(db)["seed_remove_data"] is True
    save(db, {"seed_ratio_goal": "2.5", "seed_time_goal_hours": "72", "seed_remove_data": False})
    cfg = load(db)
    assert cfg["seed_ratio_goal"] == 2.5 and cfg["seed_time_goal_hours"] == 72
    assert cfg["seed_remove_data"] is False
    save(db, {"seed_ratio_goal": -3, "seed_time_goal_hours": "junk"})
    assert load(db)["seed_ratio_goal"] == 0.0 and load(db)["seed_time_goal_hours"] == 0


def test_qbittorrent_status_carries_ratio_and_seed_time():
    from core.torrent_clients.qbittorrent import QBittorrentAdapter
    st = QBittorrentAdapter._parse_status(QBittorrentAdapter.__new__(QBittorrentAdapter),
                                          {"hash": "h", "name": "n", "state": "uploading",
                                           "progress": 1.0, "ratio": 1.7, "seeding_time": 3600})
    assert st.ratio == 1.7 and st.seeding_time == 3600


def test_automation_wiring_exists():
    import core.automation.blocks as blocks_mod
    import core.automation.handlers.registration as reg
    import core.automation_engine as eng_mod
    assert '"type": "video_seeding_sweep"' in open(blocks_mod.__file__, encoding="utf-8").read()
    assert "'video_seeding_sweep'" in open(reg.__file__, encoding="utf-8").read()
    assert "'action_type': 'video_seeding_sweep'" in open(eng_mod.__file__, encoding="utf-8").read()


def test_settings_ui_has_the_goal_fields():
    assert 'id="video-seed-ratio"' in _INDEX and 'id="video-seed-hours"' in _INDEX
    assert 'id="video-seed-remove-data"' in _INDEX
    assert "seed_ratio_goal" in _SETTINGS_JS and "seed_time_goal_hours" in _SETTINGS_JS


# ---------------------------------------------------------------------------
# seed_mode toggle (client vs soulsync)
# ---------------------------------------------------------------------------

def test_seed_mode_config_defaults_and_normalizes(db):
    from core.video.download_config import load, save
    assert load(db)["seed_mode"] == "soulsync"
    save(db, {"seed_mode": "client"})
    assert load(db)["seed_mode"] == "client"
    save(db, {"seed_mode": "CLIENT"})
    assert load(db)["seed_mode"] == "client"
    save(db, {"seed_mode": "junk"})
    assert load(db)["seed_mode"] == "soulsync"


def test_client_mode_pushes_limit_but_keeps_managing_until_goal_is_met(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"seed_time_goal_hours": 408, "seed_mode": "client"})
    _torrent_row(db, ref="abc", title="Heat")
    pushes = []
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: object())
    monkeypatch.setattr("core.torrent_clients.share_limits.push_seed_goal",
                        lambda a, ref, r, h: pushes.append((ref, r, h)) or True)
    monkeypatch.setattr(seeding, "_status",
                        lambda ref: (SimpleNamespace(ratio=0.1, seeding_time=1000), True))
    out = seeding.sweep()
    assert out == {"status": "completed", "checked": 1, "released": 0, "seeding": 1}
    assert pushes == [("abc", 0.0, 408)]
    assert len(db.torrents_awaiting_seed_release()) == 1


def test_client_mode_removes_after_pushed_goal_is_met(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"seed_time_goal_hours": 24, "seed_mode": "client", "seed_remove_data": True})
    _torrent_row(db, ref="abc", title="Heat")
    pushes = []
    removed = []
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: object())
    monkeypatch.setattr("core.torrent_clients.share_limits.push_seed_goal",
                        lambda a, ref, r, h: pushes.append((ref, r, h)) or True)
    monkeypatch.setattr(seeding, "_status",
                        lambda ref: (SimpleNamespace(ratio=0.1, seeding_time=48 * 3600), True))
    monkeypatch.setattr(seeding, "_remove", lambda ref, delete_files: removed.append((ref, delete_files)) or True)
    out = seeding.sweep()
    assert out["released"] == 1 and out["seeding"] == 0
    assert pushes == [("abc", 0.0, 24)]
    assert removed == [("abc", True)]
    assert db.torrents_awaiting_seed_release() == []

def test_client_mode_push_failure_falls_back_to_soulsync(db, monkeypatch):
    """A failed/unsupported client push must NOT leave the grab unmanaged (e.g. a
    non-qBit client) — SoulSync takes over (poll + remove per goals)."""
    from core.video.download_config import save
    save(db, {"seed_time_goal_hours": 408, "seed_mode": "client"})
    _torrent_row(db, ref="abc")
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: object())
    monkeypatch.setattr("core.torrent_clients.share_limits.push_seed_goal",
                        lambda a, ref, r, h: False)   # client can't take share limits
    import core.video.client_download as cd
    # SS fallback polls; goal not met yet → keeps seeding, stays awaiting
    monkeypatch.setattr(cd, "_get_status", lambda src, ref: SimpleNamespace(ratio=0.1, seeding_time=1000))
    out = seeding.sweep()
    assert out["released"] == 0 and out["seeding"] == 1
    assert len(db.torrents_awaiting_seed_release()) == 1


def test_client_mode_fallback_removes_when_goal_met(db, monkeypatch):
    """Fallback isn't just a no-op: when the goal IS met it removes, like SS mode."""
    from core.video.download_config import save
    save(db, {"seed_time_goal_hours": 24, "seed_mode": "client"})
    _torrent_row(db, ref="abc")
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: object())
    monkeypatch.setattr("core.torrent_clients.share_limits.push_seed_goal",
                        lambda a, ref, r, h: False)
    monkeypatch.setattr(seeding, "_status",
                        lambda ref: (SimpleNamespace(ratio=5.0, seeding_time=48 * 3600), True))
    removed = []
    monkeypatch.setattr(seeding, "_remove", lambda ref, delete_files: removed.append(ref) or True)
    out = seeding.sweep()
    assert out["released"] == 1 and removed == ["abc"]


def test_seed_mode_ui_present():
    assert 'id="video-seed-mode"' in _INDEX
    assert "seed_mode" in _SETTINGS_JS
