"""Torrent seeding lifecycle (arr-parity P5).

The importer copies torrent files into the library, so grabs seeded forever
with nothing ever letting go. The sweep releases a completed grab from the
client once the seed ratio/time goals are met — strictly opt-in (both goals
default 0 = off = old behavior), clock-fallback when a client doesn't report
seeding time, and the delete only ever touches the client's own copy.
"""

from __future__ import annotations

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
    import core.video.client_download as cd
    monkeypatch.setattr(cd, "_get_status", lambda src, ref: statuses[ref])
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
    import core.video.client_download as cd
    monkeypatch.setattr(cd, "_get_status", lambda src, ref: SimpleNamespace(ratio=2.0, seeding_time=None))
    monkeypatch.setattr(seeding, "_remove", lambda ref, delete_files: False)
    out = seeding.sweep()
    assert out["released"] == 0 and out["seeding"] == 1
    assert len(db.torrents_awaiting_seed_release()) == 1   # still managed


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


def test_client_mode_pushes_limit_and_releases(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"seed_time_goal_hours": 408, "seed_mode": "client"})
    _torrent_row(db, ref="abc", title="Heat")
    pushes = []
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: object())
    monkeypatch.setattr("core.torrent_clients.share_limits.push_seed_goal",
                        lambda a, ref, r, h: pushes.append((ref, r, h)) or True)
    # In client mode the sweep must NOT poll the client for status.
    import core.video.client_download as cd
    monkeypatch.setattr(cd, "_get_status",
                        lambda src, ref: (_ for _ in ()).throw(AssertionError("polled in client mode")))
    out = seeding.sweep()
    assert out == {"status": "completed", "checked": 1, "released": 1, "seeding": 0}
    assert pushes == [("abc", 0.0, 408)]
    assert db.torrents_awaiting_seed_release() == []   # released → handed to client


def test_client_mode_push_failure_retries_next_sweep(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"seed_time_goal_hours": 408, "seed_mode": "client"})
    _torrent_row(db, ref="abc")
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: object())
    monkeypatch.setattr("core.torrent_clients.share_limits.push_seed_goal",
                        lambda a, ref, r, h: False)
    out = seeding.sweep()
    assert out["released"] == 0 and out["seeding"] == 1
    assert len(db.torrents_awaiting_seed_release()) == 1   # still managed, retry next sweep


def test_seed_mode_ui_present():
    assert 'id="video-seed-mode"' in _INDEX
    assert "seed_mode" in _SETTINGS_JS
