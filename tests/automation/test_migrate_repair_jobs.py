"""Tests for the repair-job → automation migration (#1289 item 12)."""

import json
import pytest

from core.automation.migrate_repair_jobs import (
    _SYSTEM_OWNER,
    ensure_repair_job_automations,
    set_system_job_enabled,
)


class FakeDB:
    """Minimal database stub: automations table only."""

    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.metadata = {}

    def get_automations(self, profile_id=1):
        return list(self.rows)

    def create_automation(self, name, trigger_type, trigger_config, action_type,
                          action_config, profile_id=1, notify_type=None,
                          notify_config="{}", then_actions="[]", group_name=None,
                          owned_by=None, is_system=False, enabled=True):
        row = {
            "id": self._next_id, "name": name, "trigger_type": trigger_type,
            "trigger_config": trigger_config, "action_type": action_type,
            "action_config": action_config, "profile_id": profile_id,
            "owned_by": owned_by, "enabled": 1 if enabled else 0,
            "is_system": 1 if is_system else 0, "next_run": None,
        }
        self._next_id += 1
        self.rows.append(row)
        return row["id"]

    def update_automation(self, automation_id, **kwargs):
        for r in self.rows:
            if r["id"] == automation_id:
                r.update(kwargs)
                return True
        return False

    def get_metadata(self, key):
        return self.metadata.get(key)

    def set_metadata(self, key, value):
        self.metadata[key] = value


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeEngine:
    def __init__(self):
        self.scheduled = []

    def schedule_automation(self, automation_id):
        self.scheduled.append(automation_id)

    def cancel_automation(self, automation_id):
        self.scheduled = [a for a in self.scheduled if a != automation_id]


class FakeJob:
    display_name = "Fake Job"
    default_interval_hours = 24
    default_enabled = True


def _patch_jobs(monkeypatch, music=None, video=None):
    import core.automation.migrate_repair_jobs as m
    # The function does lazy `from X import get_all_jobs` inside its body,
    # so patch the source modules' attributes.
    import core.repair_jobs
    import core.video.repair
    monkeypatch.setattr(core.repair_jobs, "get_all_jobs",
                        lambda: dict(music or {}))
    monkeypatch.setattr(core.video.repair, "get_all_jobs",
                        lambda: dict(video or {}))
    # Bust any registry caches the real modules built at import time.
    for mod in ("core.repair_jobs", "core.video.repair"):
        monkeypatch.delattr(mod, "JOB_REGISTRY", raising=False)


def test_creates_system_automation_per_job(monkeypatch):
    _patch_jobs(monkeypatch, music={"fake_job": FakeJob}, video={})
    db, cfg, eng = FakeDB(), FakeConfig(), FakeEngine()

    result = ensure_repair_job_automations(eng, db, cfg)

    assert result == {"created": 1, "skipped": 0}
    assert len(db.rows) == 1
    row = db.rows[0]
    assert row["owned_by"] == _SYSTEM_OWNER
    assert row["is_system"] == 1
    assert row["action_type"] == "run_repair_job"
    assert json.loads(row["action_config"]) == {"job_id": "fake_job"}
    assert json.loads(row["trigger_config"])["interval"] == 24
    assert row["id"] in eng.scheduled
    # Staggered: next_run set in the future.
    assert row["next_run"] is not None


def test_disabled_job_creates_disabled_automation(monkeypatch):
    class DisabledJob(FakeJob):
        default_enabled = False
    _patch_jobs(monkeypatch, music={"disabled": DisabledJob}, video={})
    db, cfg, eng = FakeDB(), FakeConfig(), FakeEngine()

    ensure_repair_job_automations(eng, db, cfg)

    assert len(db.rows) == 1
    assert db.rows[0]["enabled"] == 0


def test_invalid_interval_skipped(monkeypatch):
    _patch_jobs(monkeypatch, music={"fake_job": FakeJob}, video={})
    db = FakeDB()
    cfg = FakeConfig({"repair.jobs.fake_job.interval_hours": "daily"})
    eng = FakeEngine()

    result = ensure_repair_job_automations(eng, db, cfg)

    assert result == {"created": 0, "skipped": 1}
    assert db.rows == []


def test_idempotent_second_run_skips(monkeypatch):
    _patch_jobs(monkeypatch, music={"fake_job": FakeJob}, video={})
    db, cfg, eng = FakeDB(), FakeConfig(), FakeEngine()

    ensure_repair_job_automations(eng, db, cfg)
    result = ensure_repair_job_automations(eng, db, cfg)

    assert result == {"created": 0, "skipped": 1}
    assert len(db.rows) == 1


def test_manual_only_jobs_get_no_automation(monkeypatch):
    class ManualJob(FakeJob):
        default_interval_hours = 0
    _patch_jobs(monkeypatch, music={"manual": ManualJob}, video={})
    db, cfg, eng = FakeDB(), FakeConfig(), FakeEngine()

    result = ensure_repair_job_automations(eng, db, cfg)

    assert result == {"created": 0, "skipped": 1}
    assert db.rows == []


def test_video_jobs_use_video_action(monkeypatch):
    _patch_jobs(monkeypatch, music={}, video={"vfake": FakeJob})
    db, cfg, eng = FakeDB(), FakeConfig(), FakeEngine()

    result = ensure_repair_job_automations(eng, db, cfg)

    assert result["created"] == 1
    assert db.rows[0]["action_type"] == "video_run_repair_job"


def test_config_interval_overrides_default(monkeypatch):
    _patch_jobs(monkeypatch, music={"fake_job": FakeJob}, video={})
    db = FakeDB()
    cfg = FakeConfig({"repair.jobs.fake_job.interval_hours": 6})
    eng = FakeEngine()

    ensure_repair_job_automations(eng, db, cfg)

    assert json.loads(db.rows[0]["trigger_config"])["interval"] == 6


def _system_row(db, job_id, enabled=True):
    return db.create_automation(
        name=f"[System] {job_id}", trigger_type="schedule",
        trigger_config=json.dumps({"interval": 24, "unit": "hours"}),
        action_type="run_repair_job", action_config=json.dumps({"job_id": job_id}),
        owned_by=_SYSTEM_OWNER, is_system=True, enabled=enabled)


def test_toggling_a_job_arms_and_cancels_its_timer():
    db, eng = FakeDB(), FakeEngine()
    aid = _system_row(db, "fake_job", enabled=False)

    assert set_system_job_enabled(db, eng, "fake_job", True)
    assert db.rows[0]["enabled"] == 1 and eng.scheduled == [aid]
    set_system_job_enabled(db, eng, "fake_job", False)
    assert db.rows[0]["enabled"] == 0 and eng.scheduled == []
    assert not set_system_job_enabled(db, eng, "other_job", True)


# ── automations page -> tools toggle (storm's report: the two drifted) ──────

class ToggleDB(FakeDB):
    def get_automation(self, automation_id):
        return next((r for r in self.rows if r["id"] == automation_id), None)

    def toggle_automation(self, automation_id):
        r = self.get_automation(automation_id)
        if not r:
            return False
        r["enabled"] = 0 if r["enabled"] else 1
        return True

    def bulk_set_enabled(self, ids, enabled):
        n = 0
        for r in self.rows:
            if r["id"] in ids:
                r["enabled"] = 1 if enabled else 0
                n += 1
        return n


class SetConfig(FakeConfig):
    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def sinks(monkeypatch):
    import core.automation.migrate_repair_jobs as m
    calls = []
    monkeypatch.setattr(m, "_job_toggle_sinks", {
        "run_repair_job": lambda job_id, en: calls.append(("music", job_id, en)),
        "video_run_repair_job": lambda job_id, en: calls.append(("video", job_id, en)),
    })
    return calls


def test_automations_page_toggle_reaches_the_tools_job(sinks):
    from core.automation import api
    db, eng = ToggleDB(), FakeEngine()
    aid = _system_row(db, "suspect_album_tag", enabled=True)

    assert api.toggle_automation(db, eng, aid)[1] == 200
    assert sinks == [("music", "suspect_album_tag", False)]
    api.toggle_automation(db, eng, aid)
    assert sinks[-1] == ("music", "suspect_album_tag", True)


def test_bulk_toggle_reaches_every_system_job(sinks):
    from core.automation import api
    db, eng = ToggleDB(), FakeEngine()
    a = _system_row(db, "job_a")
    b = db.create_automation(
        name="[System] v", trigger_type="schedule", trigger_config="{}",
        action_type="video_run_repair_job", action_config=json.dumps({"job_id": "vjob"}),
        owned_by=_SYSTEM_OWNER, is_system=True)
    user = db.create_automation(
        name="mine", trigger_type="schedule", trigger_config="{}",
        action_type="run_repair_job", action_config=json.dumps({"job_id": "job_a"}))

    api.bulk_toggle(db, eng, [a, b, user], False)
    # the user's own run_repair_job automation is not the job's toggle
    assert sorted(sinks) == [("music", "job_a", False), ("video", "vjob", False)]


def test_music_sink_writes_config_without_rebridging(monkeypatch):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w._config_manager = SetConfig()
    w._current_job_id = None
    import core.automation.migrate_repair_jobs as m
    bridged = []
    monkeypatch.setattr(m, "set_system_job_enabled", lambda *a, **k: bridged.append(a))
    monkeypatch.setattr(w, "stop_current_job", lambda job_id: None, raising=False)

    w.set_job_enabled("suspect_album_tag", False, bridge=False)

    assert w._config_manager.values["repair.jobs.suspect_album_tag.enabled"] is False
    assert bridged == []


def test_boot_heals_a_music_job_that_drifted_from_its_row(monkeypatch):
    _patch_jobs(monkeypatch, music={"fake_job": FakeJob}, video={})
    db, eng = FakeDB(), FakeEngine()
    _system_row(db, "fake_job", enabled=False)
    cfg = SetConfig({"repair.jobs.fake_job.enabled": True})

    ensure_repair_job_automations(eng, db, cfg)

    assert cfg.values["repair.jobs.fake_job.enabled"] is False


def test_boot_heals_a_video_job_that_drifted_from_its_row(monkeypatch):
    _patch_jobs(monkeypatch, music={}, video={"vfake": FakeJob})
    import core.video.repair
    monkeypatch.setattr(core.video.repair, "get_all_jobs", lambda: {"vfake": FakeJob})

    class VideoDB:
        def __init__(self):
            self.s = {"video_repair.jobs.vfake": json.dumps({"enabled": True, "interval_hours": 12})}
        def get_setting(self, k):
            return self.s.get(k)
        def set_setting(self, k, v):
            self.s[k] = v

    db, eng, vdb = FakeDB(), FakeEngine(), VideoDB()
    db.create_automation(
        name="[System] v", trigger_type="schedule", trigger_config="{}",
        action_type="video_run_repair_job", action_config=json.dumps({"job_id": "vfake"}),
        owned_by=_SYSTEM_OWNER, is_system=True, enabled=False)

    ensure_repair_job_automations(eng, db, SetConfig(), video_db=vdb)

    saved = json.loads(vdb.s["video_repair.jobs.vfake"])
    assert saved == {"enabled": False, "interval_hours": 12}
