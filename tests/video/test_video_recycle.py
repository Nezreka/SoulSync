"""Video recycle bin — deletes move to trash instead of unlinking.

Every media-file delete routes through core.video.recycle.discard: the
importer's upgrade-replace, YouTube retention, dismissed imports (and the
future watched-cleanup / duplicate deletes). Trash = <library root>/ss_recycle
(the video sibling of music's ss_quarantine), timestamped entries, purged
after recycle_keep_days. Failure discipline: a failed trash move leaves the
file IN PLACE (ok False); a file under no known root hard-deletes (refusing
would wedge retention and fill the disk).
"""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path

import pytest

from core.video import recycle
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("movies_path", str(tmp_path / "Movies"))
    d.set_setting("youtube_path", str(tmp_path / "YouTube"))
    return d


def _settings(**kw):
    from core.video import organization
    return organization.normalize({**organization.default_settings(), **kw})


def _mkfile(p: Path, content=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# ── discard ──────────────────────────────────────────────────────────────────
def test_discard_moves_into_the_library_trash(db, tmp_path):
    f = _mkfile(tmp_path / "Movies" / "Heat (1995)" / "Heat (1995) 1080p.mkv")
    res = recycle.discard(str(f), _settings(), db, reason="test")
    assert res["ok"] and res["recycled"]
    assert not f.exists()
    trash = Path(res["trash_path"])
    assert trash.parent == tmp_path / "Movies" / "ss_recycle"
    assert trash.name.endswith("_Heat (1995) 1080p.mkv")      # timestamped entry


def test_discard_uses_the_override_folder_when_set(db, tmp_path):
    f = _mkfile(tmp_path / "Movies" / "a.mkv")
    override = tmp_path / "Trash"
    res = recycle.discard(str(f), _settings(recycle_path=str(override)), db)
    assert res["ok"] and Path(res["trash_path"]).parent == override


def test_discard_with_recycling_off_hard_deletes(db, tmp_path):
    f = _mkfile(tmp_path / "Movies" / "a.mkv")
    res = recycle.discard(str(f), _settings(recycle_deletes=False), db)
    assert res["ok"] and not res["recycled"] and not f.exists()
    assert not (tmp_path / "Movies" / "ss_recycle").exists()


def test_file_outside_every_root_hard_deletes(db, tmp_path):
    """Refusing to delete would wedge retention semantics — documented fallback."""
    f = _mkfile(tmp_path / "elsewhere" / "a.mkv")
    res = recycle.discard(str(f), _settings(), db)
    assert res["ok"] and not res["recycled"] and not f.exists()


def test_already_gone_counts_as_done(db, tmp_path):
    res = recycle.discard(str(tmp_path / "Movies" / "nope.mkv"), _settings(), db)
    assert res["ok"] and not res["recycled"]


def test_name_collision_gets_a_suffix(db, tmp_path, monkeypatch):
    import core.video.recycle as rec
    monkeypatch.setattr(rec.time, "time", lambda: 1751500000.0)   # freeze the stamp
    a = _mkfile(tmp_path / "Movies" / "A" / "same.mkv")
    b = _mkfile(tmp_path / "Movies" / "B" / "same.mkv")
    r1 = recycle.discard(str(a), _settings(), db)
    r2 = recycle.discard(str(b), _settings(), db)
    assert r1["ok"] and r2["ok"]
    assert r1["trash_path"] != r2["trash_path"]
    assert "_(2)_" in os.path.basename(r2["trash_path"])


# ── purge ────────────────────────────────────────────────────────────────────
def _stamped(days_ago: float) -> str:
    """The name discard() would have written ``days_ago`` days ago."""
    when = datetime.datetime.now() - datetime.timedelta(days=days_ago)
    return when.strftime("%Y%m%d_%H%M%S")


def test_purge_removes_only_expired_entries(db, tmp_path):
    trash = tmp_path / "Movies" / "ss_recycle"
    old = _mkfile(trash / (_stamped(10) + "_old.mkv"), b"x" * 2048)
    fresh = _mkfile(trash / (_stamped(1) + "_fresh.mkv"))
    removed, freed = recycle.purge_old_detailed(_settings(recycle_keep_days=7), db)
    assert removed == 1 and freed == 2048          # bytes reclaimed, not just a count
    assert not old.exists() and fresh.exists()


def test_age_comes_from_the_name_stamp_not_mtime(db, tmp_path):
    """The bug this guards: shutil.move keeps the ORIGINAL file's mtime, so a
    years-old library file landed in the bin already 'expired' and the next
    purge deleted it instantly. Recycling something old must still buy you the
    full keep window."""
    trash = tmp_path / "Movies" / "ss_recycle"
    f = _mkfile(trash / (_stamped(1) + "_ancient.mkv"))
    ancient = time.time() - 900 * 86400          # recycled today, made in 2024
    os.utime(f, (ancient, ancient))
    assert recycle.purge_old(_settings(recycle_keep_days=7), db) == 0
    assert f.exists()


def test_purge_ignores_files_it_did_not_write(db, tmp_path):
    """recycle_path can point at a folder holding other things. Only stamped
    entries are ours; everything else is left alone however old it is."""
    trash = tmp_path / "Trash"
    stranger = _mkfile(trash / "someones_movie.mkv")
    old = time.time() - 400 * 86400
    os.utime(stranger, (old, old))
    ours = _mkfile(trash / (_stamped(30) + "_ours.mkv"))
    removed = recycle.purge_old(_settings(recycle_keep_days=7, recycle_path=str(trash)), db)
    assert removed == 1
    assert stranger.exists() and not ours.exists()


def test_end_to_end_recycled_file_expires_on_its_own_clock(db, tmp_path, monkeypatch):
    """discard -> purge with a real move, no utime tricks. A 500-day-old movie
    recycled today keeps its full 7 days, then goes on day 8."""
    f = _mkfile(tmp_path / "Movies" / "Heat (1995)" / "Heat.mkv")
    made = time.time() - 500 * 86400
    os.utime(f, (made, made))
    trash_path = recycle.discard(str(f), _settings(), db)["trash_path"]
    assert os.path.exists(trash_path)                       # survived its own discard purge
    assert recycle.purge_old(_settings(recycle_keep_days=7), db) == 0
    assert os.path.exists(trash_path)

    real = time.time
    monkeypatch.setattr(recycle.time, "time", lambda: real() + 8 * 86400)
    assert recycle.purge_old(_settings(recycle_keep_days=7), db) == 1
    assert not os.path.exists(trash_path)


def test_discard_triggers_an_opportunistic_purge(db, tmp_path):
    trash = tmp_path / "Movies" / "ss_recycle"
    old = _mkfile(trash / (_stamped(30) + "_old.mkv"))
    f = _mkfile(tmp_path / "Movies" / "new.mkv")
    assert recycle.discard(str(f), _settings(), db)["ok"]
    assert not old.exists()                       # expired entry swept on the way


# ── the scheduled sweep (the opportunistic one is not enough) ────────────────
def test_a_daily_automation_owns_the_purge():
    """Without a schedule the bin is only swept when the NEXT file is deleted,
    so a quiet library never expires anything. That was the reported bug."""
    from core.automation_engine import SYSTEM_AUTOMATIONS
    entry = next((a for a in SYSTEM_AUTOMATIONS
                  if a.get("action_type") == "video_purge_recycle_bin"), None)
    assert entry is not None, "no scheduled recycle purge"
    assert entry.get("owned_by") == "video"
    assert entry.get("trigger_type") in ("daily_time", "schedule")


def test_purge_handler_reports_what_it_removed():
    from core.automation.handlers.video_purge_recycle import auto_video_purge_recycle_bin

    class _Deps:
        def __init__(self):
            self.lines = []

        def update_progress(self, _id, **kw):
            self.lines.append(kw)

    deps = _Deps()
    res = auto_video_purge_recycle_bin({}, deps, purge=lambda: (3, 5 * 1024 ** 3))
    assert res["status"] == "completed" and res["removed"] == 3
    assert res["freed_bytes"] == 5 * 1024 ** 3
    # a disk-reclaim job has to say how much disk it reclaimed, like the
    # YouTube retention job does
    assert any("5.0 GB" in str(k.get("log_line", "")) for k in deps.lines)


def test_purge_handler_survives_a_broken_purge():
    from core.automation.handlers.video_purge_recycle import auto_video_purge_recycle_bin

    class _Deps:
        def update_progress(self, _id, **kw):
            pass

    def _boom():
        raise OSError("network share gone")

    res = auto_video_purge_recycle_bin({}, _Deps(), purge=_boom)
    assert res["status"] == "error"


# ── the wired seams ──────────────────────────────────────────────────────────
def test_upgrade_replace_routes_through_recycle(db, tmp_path):
    """run_import's upgrade path must call the injected recycle instead of
    fs.remove — the old library copy lands in trash, not oblivion."""
    from core.video.importer import run_import

    lib = tmp_path / "Movies" / "Heat (1995)"
    old_copy = _mkfile(lib / "Heat (1995) 720p.mkv", b"old")
    src = _mkfile(tmp_path / "dl" / "Heat.1995.1080p.BluRay.x264.mkv", b"new" * 100)

    class _FS:
        def list_dir(self, d):
            try:
                return os.listdir(d)
            except OSError:
                return []
        def makedirs(self, d):
            os.makedirs(d, exist_ok=True)
        def copy(self, a, b):
            Path(b).write_bytes(Path(a).read_bytes())
        def move(self, a, b):
            os.replace(a, b)
        def remove(self, p):
            os.remove(p)

    dl = {"kind": "movie", "title": "Heat", "year": 1995, "source": "slskd",
          "release_title": "Heat.1995.1080p.BluRay.x264", "size_bytes": 300,
          "search_ctx": json.dumps({"scope": "movie", "title": "Heat", "year": 1995}),
          "target_dir": str(tmp_path / "Movies")}
    settings = _settings()
    patch = run_import(dl, str(src), fs=_FS(), prober=None, settings=settings,
                       library_dir=str(lib), recycle=recycle.discarder(db, settings))
    assert patch["status"] == "completed", patch
    assert not old_copy.exists()                              # replaced…
    trash = tmp_path / "Movies" / "ss_recycle"
    assert any(n.endswith("_Heat (1995) 720p.mkv") for n in os.listdir(trash))   # …into trash


def test_retention_delete_routes_through_recycle(db, tmp_path, monkeypatch):
    import api.video as videoapi
    from core.automation.handlers.video_clean_youtube import _default_delete_files
    videoapi._video_db = db
    try:
        f = _mkfile(tmp_path / "YouTube" / "Chan" / "Season 2026" / "v.mp4", b"vid")
        _mkfile(f.parent / "v.nfo")
        ok, freed = _default_delete_files({"dest_path": str(f)})
        assert ok and freed == 3
        assert not f.exists()
        assert not (f.parent / "v.nfo").exists()              # sidecars removed outright
        trash = tmp_path / "YouTube" / "ss_recycle"
        assert any(n.endswith("_v.mp4") for n in os.listdir(trash))
    finally:
        videoapi._video_db = None


# ── settings plumbing + UI contracts ─────────────────────────────────────────
def test_settings_normalize_recycle_keys():
    from core.video.organization import normalize
    d = normalize({"recycle_deletes": 0, "recycle_keep_days": "999", "recycle_path": "  /t "})
    assert d["recycle_deletes"] is False
    assert d["recycle_keep_days"] == 365                      # clamped
    assert d["recycle_path"] == "/t"
    assert normalize({})["recycle_deletes"] is True           # safe default: ON


def test_settings_ui_has_the_recycle_fields():
    for frag in ("vo-recycle'", "vo-recycle-days", "vo-recycle-path",
                 "recycle_deletes", "recycle_keep_days", "recycle_path"):
        assert frag in _SETTINGS_JS, frag
    for frag in ('id="vo-recycle"', 'id="vo-recycle-days"', 'id="vo-recycle-path"'):
        assert frag in _INDEX, frag
