"""Music torrent seeding lifecycle (mirror of the video P5 sweep).

The importer copies torrent files into the library, so music grabs seeded
forever with nothing letting go. This sweep releases a completed grab from the
client once the seed ratio/time goals are met — strictly opt-in (both goals
default 0 = off = old behavior), clock-fallback when a client doesn't report
seeding time, the delete only ever touches the client's own copy, and a
transient client error never triggers an erroneous release.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.downloads.seeding as seeding
from database.music_database import MusicDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / "music_library.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: d)
    return d


class _FakeAdapter:
    """Async adapter stub. statuses maps ref -> a SimpleNamespace, None (client
    forgot it), or the string 'RAISE' (transient error)."""

    def __init__(self, statuses, remove_ok=True):
        self.statuses = statuses
        self.remove_ok = remove_ok
        self.removed = []

    async def get_status(self, ref):
        s = self.statuses[ref]
        if s == "RAISE":
            raise RuntimeError("client unreachable")
        return s

    async def remove(self, ref, delete_files=False):
        self.removed.append((ref, delete_files))
        return self.remove_ok


def _use_adapter(monkeypatch, adapter):
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: adapter)


def _goals(monkeypatch, ratio=0, hours=0, remove_data=True):
    monkeypatch.setattr(seeding, "_load_cfg", lambda: {
        "seed_ratio_goal": ratio, "seed_time_goal_hours": hours,
        "seed_remove_data": remove_data,
    })


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


def test_config_coercion_defaults_off_on_junk():
    assert seeding._coerce_ratio("2.5") == 2.5
    assert seeding._coerce_ratio(-3) == 0.0
    assert seeding._coerce_ratio("junk") == 0.0
    assert seeding._coerce_ratio(None) == 0.0
    assert seeding._coerce_hours("72") == 72
    assert seeding._coerce_hours("junk") == 0
    assert seeding._coerce_hours(-5) == 0


def test_clock_fallback_when_client_reports_no_seed_time():
    cfg = {"seed_ratio_goal": 0, "seed_time_goal_hours": 24}
    old = {"completed_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()}
    fresh = {"completed_at": datetime.now(timezone.utc).isoformat()}
    st = SimpleNamespace(ratio=None, seeding_time=None)
    assert seeding.goals_met(st, old, cfg)
    assert seeding.goals_met(st, fresh, cfg) is None


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------

def test_record_is_idempotent_and_release_round_trips(db):
    db.record_torrent_seed_grab("h1", "Album A", "soulsync")
    db.record_torrent_seed_grab("h2", "Album B", "soulsync")
    db.record_torrent_seed_grab("h1", "dup", "soulsync")            # ignored
    assert db.record_torrent_seed_grab("", "empty") is None          # no-op, no row
    rows = db.torrents_awaiting_seed_release()
    assert sorted(r["torrent_hash"] for r in rows) == ["h1", "h2"]
    rid = next(r["id"] for r in rows if r["torrent_hash"] == "h1")
    db.mark_torrent_seed_released(rid)
    assert [r["torrent_hash"] for r in db.torrents_awaiting_seed_release()] == ["h2"]


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------

def test_sweep_is_off_without_goals(db, monkeypatch):
    db.record_torrent_seed_grab("h1", "A")
    _goals(monkeypatch, ratio=0, hours=0)
    out = seeding.sweep()
    assert out["status"] == "skipped" and out["reason"] == "no_goals_set"


def test_sweep_skips_when_no_torrent_client(db, monkeypatch):
    db.record_torrent_seed_grab("h1", "A")
    _goals(monkeypatch, ratio=1.0)
    _use_adapter(monkeypatch, None)
    out = seeding.sweep()
    assert out["status"] == "skipped" and out["reason"] == "no_torrent_client"


def test_sweep_releases_met_keeps_seeding_and_clears_forgotten(db, monkeypatch):
    _goals(monkeypatch, ratio=1.0)
    for ref in ("met", "keep", "gone"):
        db.record_torrent_seed_grab(ref, ref)
    adapter = _FakeAdapter({
        "met": SimpleNamespace(ratio=1.5, seeding_time=None),
        "keep": SimpleNamespace(ratio=0.2, seeding_time=None),
        "gone": None,   # client forgot it
    })
    _use_adapter(monkeypatch, adapter)
    out = seeding.sweep()
    assert out == {"status": "completed", "checked": 3, "released": 2, "seeding": 1}
    assert adapter.removed == [("met", True)]   # forgotten torrents aren't 'removed', just marked
    assert [r["torrent_hash"] for r in db.torrents_awaiting_seed_release()] == ["keep"]


def test_transient_status_error_never_releases(db, monkeypatch):
    """Music-side safety: a client error (exception) is retried next sweep, not
    mistaken for 'client forgot it'."""
    _goals(monkeypatch, ratio=1.0)
    db.record_torrent_seed_grab("flaky", "A")
    _use_adapter(monkeypatch, _FakeAdapter({"flaky": "RAISE"}))
    out = seeding.sweep()
    assert out["released"] == 0 and out["seeding"] == 1
    assert [r["torrent_hash"] for r in db.torrents_awaiting_seed_release()] == ["flaky"]


def test_failed_removal_retries_next_sweep(db, monkeypatch):
    _goals(monkeypatch, ratio=1.0)
    db.record_torrent_seed_grab("met", "A")
    _use_adapter(monkeypatch, _FakeAdapter(
        {"met": SimpleNamespace(ratio=2.0, seeding_time=None)}, remove_ok=False))
    out = seeding.sweep()
    assert out["released"] == 0 and out["seeding"] == 1
    assert len(db.torrents_awaiting_seed_release()) == 1   # still managed


def test_seed_remove_data_flag_flows_to_adapter(db, monkeypatch):
    _goals(monkeypatch, ratio=1.0, remove_data=False)
    db.record_torrent_seed_grab("met", "A")
    adapter = _FakeAdapter({"met": SimpleNamespace(ratio=2.0, seeding_time=None)})
    _use_adapter(monkeypatch, adapter)
    seeding.sweep()
    assert adapter.removed == [("met", False)]


# ---------------------------------------------------------------------------
# config + automation + UI wiring
# ---------------------------------------------------------------------------

def test_config_defaults_present_and_off():
    import config.settings as cs
    src = Path(cs.__file__).read_text(encoding="utf-8")
    assert '"seed_ratio_goal": 0' in src
    assert '"seed_time_goal_hours": 0' in src
    assert '"seed_remove_data": True' in src


def test_automation_wiring_exists():
    import core.automation.blocks as blocks_mod
    import core.automation.handlers.registration as reg
    import core.automation_engine as eng_mod
    assert '"type": "seeding_sweep"' in Path(blocks_mod.__file__).read_text(encoding="utf-8")
    assert "'seeding_sweep'" in Path(reg.__file__).read_text(encoding="utf-8")
    assert "'action_type': 'seeding_sweep'" in Path(eng_mod.__file__).read_text(encoding="utf-8")


def test_settings_ui_has_the_goal_fields():
    assert 'id="music-seed-ratio"' in _INDEX and 'id="music-seed-hours"' in _INDEX
    assert 'id="music-seed-remove-data"' in _INDEX
    assert "seed_ratio_goal" in _SETTINGS_JS and "seed_time_goal_hours" in _SETTINGS_JS
