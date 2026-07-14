"""YouTube Ghost Files job — the ownership ledger vs the disk.

The ledger (video_download_history, youtube+completed) says "owned"; the user
deletes files server-side; badges and counts go stale. The job path-checks the
un-pruned ledger and flags ghosts. Two fixes, both DB-only: Mark Deleted stamps
pruned_at (badge clears, dedup keeps excluding it — no re-download storm) and
Forget deletes the row (re-download eligible). Safety: an unreachable YouTube
root or a mass-missing sweep aborts with an error instead of tombstoning the
library. Companion split: UI ownership annotation must EXCLUDE pruned rows
while the scan dedup keeps including them.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_REPAIR_JS = (_ROOT / "webui" / "static" / "video" / "video-repair.js").read_text(encoding="utf-8")
_YOUTUBE_API = (_ROOT / "api" / "video" / "youtube.py").read_text(encoding="utf-8")

_ids = itertools.count(1)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


def _seed_grab(db, tmp_path, vid, *, title=None, channel_id="UC1", on_disk=True):
    """One completed YouTube grab in the ledger, its file optionally on disk."""
    path = tmp_path / "yt" / f"{vid}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    if on_disk:
        path.write_bytes(b"video")
    hid = db.record_download_history({
        "id": next(_ids), "kind": "youtube", "source": "youtube",
        "status": "completed", "title": title or f"Video {vid}",
        "media_id": vid, "dest_path": str(path),
        "search_ctx": json.dumps({"channel": "Chan", "channel_id": channel_id,
                                  "published_at": "2026-03-01"}),
        "completed_at": "2026-07-01 00:00:00"})
    assert hid
    return hid, str(path)


def _pending(db):
    return db.repair_get_findings(status="pending")["items"]


# ── scan ─────────────────────────────────────────────────────────────────────
def test_scan_flags_only_the_missing_files(db, worker, tmp_path):
    _seed_grab(db, tmp_path, "keep1")
    _seed_grab(db, tmp_path, "keep2")
    hid, path = _seed_grab(db, tmp_path, "ghost1", title="Gone Video", on_disk=False)
    db.cache_channel_meta("UC1", {"title": "Gamers Nexus"})

    worker._run_job("youtube_ghosts", forced=True)
    items = _pending(db)
    assert len(items) == 1
    f = items[0]
    assert f["finding_type"] == "youtube_ghost" and f["severity"] == "warning"
    assert f["entity_id"] == f"yt:ghost1:{hid}"
    assert "Gone Video" in f["title"] and "missing" in f["title"]
    d = f["details"]
    assert d["history_id"] == hid and d["dest_path"] == path
    assert d["channel"] == "Gamers Nexus" and d["channel_id"] == "UC1"
    assert d["thumb_url"].endswith("/vi/ghost1/hqdefault.jpg")


def test_pruned_and_pathless_rows_are_not_rechecked(db, tmp_path):
    hid, _ = _seed_grab(db, tmp_path, "already", on_disk=False)
    db.mark_download_pruned(hid, "2026-07-01")
    conn = db._get_connection()
    conn.execute("INSERT INTO video_download_history (download_id, kind, media_type, title, "
                 "source, media_id, outcome) VALUES (999991,'youtube','youtube','No Path',"
                 "'youtube','nopath','completed')")
    conn.commit(); conn.close()
    ids = [r["media_id"] for r in db.youtube_ledger_rows()]
    assert "already" not in ids       # pruned = already handled
    assert "nopath" not in ids        # nothing to path-check


def test_rescan_retires_a_finding_when_the_file_comes_back(db, worker, tmp_path):
    _hid, path = _seed_grab(db, tmp_path, "flaky", on_disk=False)
    _seed_grab(db, tmp_path, "keep1")
    worker._run_job("youtube_ghosts", forced=True)
    assert len(_pending(db)) == 1

    Path(path).write_bytes(b"restored")
    worker._run_job("youtube_ghosts", forced=True)
    assert _pending(db) == []


# ── fixes ────────────────────────────────────────────────────────────────────
def test_default_fix_marks_deleted_and_keeps_the_dedup_memory(db, worker, tmp_path):
    hid, _ = _seed_grab(db, tmp_path, "ghost1", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    f = _pending(db)[0]

    res = worker.fix_finding(f["id"])
    assert res["success"] and res["action"] == "marked_deleted"
    # The ledger row survives with a pruned stamp: dedup still excludes it
    # (no re-download storm), but the UI no longer claims ownership.
    assert "ghost1" in db.downloaded_youtube_video_ids()
    assert "ghost1" not in db.owned_youtube_video_ids()
    assert db.repair_get_finding(f["id"])["status"] == "resolved"


def test_forget_fix_deletes_the_row_so_it_can_redownload(db, worker, tmp_path):
    hid, _ = _seed_grab(db, tmp_path, "ghost2", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    f = _pending(db)[0]

    res = worker.fix_finding(f["id"], fix_action="forget")
    assert res["success"] and res["action"] == "forgotten"
    assert "ghost2" not in db.downloaded_youtube_video_ids()
    assert "ghost2" not in db.owned_youtube_video_ids()


def test_forget_redownload_delete_again_flags_again(db, worker, tmp_path):
    """The dedup trap: findings dedup forever on same entity OR same file_path,
    ANY status. A per-VIDEO entity (or passing the path) would silence the
    second ghost after a Forget → re-download → delete-again cycle; the
    per-ledger-row entity must not."""
    _hid, path = _seed_grab(db, tmp_path, "cycle", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    f = _pending(db)[0]
    assert worker.fix_finding(f["id"], fix_action="forget")["success"]

    # re-download lands a NEW ledger row on the SAME path; then it's deleted again
    _seed_grab(db, tmp_path, "cycle", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    fresh = _pending(db)
    assert len(fresh) == 1 and fresh[0]["details"]["media_id"] == "cycle"


def test_fix_refuses_when_the_file_is_back(db, worker, tmp_path):
    _hid, path = _seed_grab(db, tmp_path, "flaky", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    f = _pending(db)[0]
    Path(path).write_bytes(b"restored")

    res = worker.fix_finding(f["id"])
    assert res["success"] is False and "back on disk" in res["error"]
    assert "flaky" in db.owned_youtube_video_ids()   # nothing was stamped


# ── safety guards ────────────────────────────────────────────────────────────
def _by_type(db, t):
    return [f for f in _pending(db) if f["finding_type"] == t]


def test_mass_missing_becomes_one_critical_finding(db, worker, tmp_path):
    """>50% of >=5 checked files gone at once: could be an unmounted share OR a
    deliberate wipe / drive change (Boulder's case) — so it's ONE critical
    finding asking the user, not a silent abort that wedges the job forever."""
    for i in range(6):
        _seed_grab(db, tmp_path, f"v{i}", on_disk=(i < 2))   # 4 of 6 missing
    worker._run_job("youtube_ghosts", forced=True)
    assert worker._states["youtube_ghosts"]["status"] == "finished"
    assert _by_type(db, "youtube_ghost") == []               # nothing auto-flagged
    mass = _by_type(db, "youtube_mass_missing")
    assert len(mass) == 1
    f = mass[0]
    assert f["severity"] == "critical" and "4 of 6" in f["title"]
    d = f["details"]
    assert d["missing_count"] == 4 and d["checked"] == 6
    assert len(d["history_ids"]) == 4 and len(d["sample"]) == 4


def test_approving_mass_finding_flags_individually(db, worker, tmp_path):
    hids = {}
    for i in range(6):
        hid, path = _seed_grab(db, tmp_path, f"v{i}", on_disk=(i < 2))
        hids[f"v{i}"] = (hid, path)
    worker._run_job("youtube_ghosts", forced=True)
    mass = _by_type(db, "youtube_mass_missing")[0]

    # one file returns between scan and approve — the fix re-verifies NOW
    Path(hids["v5"][1]).write_bytes(b"restored")
    res = worker.fix_finding(mass["id"])
    assert res["success"] and res["action"] == "flagged" and "3 missing" in res["message"]

    ghosts = _by_type(db, "youtube_ghost")
    assert {g["details"]["media_id"] for g in ghosts} == {"v2", "v3", "v4"}
    assert db.repair_get_finding(mass["id"])["status"] == "resolved"
    # the individual findings behave normally: approve one → pruned
    res = worker.fix_finding(ghosts[0]["id"])
    assert res["success"] and res["action"] == "marked_deleted"


def test_mass_finding_supersedes_and_dedups(db, worker, tmp_path):
    """A changed missing set retires the stale pending mass finding and raises a
    fresh one; an unchanged set dedups (no daily spam)."""
    seeds = [_seed_grab(db, tmp_path, f"v{i}", on_disk=(i < 2)) for i in range(6)]
    worker._run_job("youtube_ghosts", forced=True)
    first = _by_type(db, "youtube_mass_missing")[0]

    worker._run_job("youtube_ghosts", forced=True)           # same state → dedup
    assert [f["id"] for f in _by_type(db, "youtube_mass_missing")] == [first["id"]]

    Path(seeds[1][1]).unlink()                                # now 5 of 6 missing
    worker._run_job("youtube_ghosts", forced=True)
    mass = _by_type(db, "youtube_mass_missing")
    assert len(mass) == 1 and mass[0]["id"] != first["id"]
    assert mass[0]["details"]["missing_count"] == 5
    assert db.repair_get_finding(first["id"])["status"] == "dismissed"


def test_pending_ghosts_survive_a_mass_event(db, worker, tmp_path):
    """Individual findings raised earlier must not be retired by a later scan
    that trips the mass tier — their rows are still missing."""
    _seed_grab(db, tmp_path, "old", on_disk=False)
    for i in range(4):
        _seed_grab(db, tmp_path, f"v{i}")
    worker._run_job("youtube_ghosts", forced=True)            # 1 of 5 → normal flag
    assert len(_by_type(db, "youtube_ghost")) == 1
    for i in range(4):                                        # wipe the rest
        (tmp_path / "yt" / f"v{i}.mp4").unlink()
    worker._run_job("youtube_ghosts", forced=True)            # 5 of 5 → mass tier
    assert len(_by_type(db, "youtube_ghost")) == 1            # kept, not superseded
    assert len(_by_type(db, "youtube_mass_missing")) == 1


def test_small_libraries_are_exempt_from_the_guard(db, worker, tmp_path):
    """1 missing of 2 is a normal delete, not an outage — must still flag."""
    _seed_grab(db, tmp_path, "have")
    hid, _ = _seed_grab(db, tmp_path, "gone", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    assert [f["entity_id"] for f in _pending(db)] == [f"yt:gone:{hid}"]


def test_unreachable_youtube_root_aborts(db, worker, tmp_path):
    db.set_setting("youtube_path", str(tmp_path / "not-mounted"))
    _seed_grab(db, tmp_path, "gone", on_disk=False)
    worker._run_job("youtube_ghosts", forced=True)
    assert _pending(db) == []
    assert worker._states["youtube_ghosts"]["status"] == "error"
    assert any("unreachable" in ln["text"]
               for ln in worker._states["youtube_ghosts"]["log"])


# ── the ownership annotation split ───────────────────────────────────────────
def test_owned_ids_exclude_pruned_but_dedup_ids_keep_them(db, tmp_path):
    hid, _ = _seed_grab(db, tmp_path, "vid1")
    db.mark_download_pruned(hid, "2026-07-11")
    assert "vid1" in db.downloaded_youtube_video_ids()   # scans must not re-grab
    assert "vid1" not in db.owned_youtube_video_ids()    # UI must not badge it


def test_channel_tab_owned_count_excludes_pruned(db, tmp_path):
    # a channel with downloads lands on the tab even unfollowed
    db.cache_channel_meta("UC1", {"title": "Chan"})
    hid1, _ = _seed_grab(db, tmp_path, "a1", channel_id="UC1")
    _seed_grab(db, tmp_path, "a2", channel_id="UC1")
    assert db.query_channel_library()["items"][0]["owned_count"] == 2
    db.mark_download_pruned(hid1, "2026-07-11")
    assert db.query_channel_library()["items"][0]["owned_count"] == 1


def test_fully_pruned_unfollowed_channel_leaves_the_library_tab(db, tmp_path):
    """Boulder's '0 / 2010 downloaded' ghosts: an UNFOLLOWED channel is only in
    the library because you own files from it — once every download is pruned
    it must drop off the tab. A FOLLOWED channel stays at 0 (the follow itself
    is the membership)."""
    db.cache_channel_meta("UC1", {"title": "Gamers Nexus"})
    hid, _ = _seed_grab(db, tmp_path, "g1", channel_id="UC1")
    assert [c["id"] for c in db.query_channel_library()["items"]] == ["UC1"]
    db.mark_download_pruned(hid, "2026-07-11")
    assert db.query_channel_library()["items"] == []

    # follow it → back on the tab despite 0 owned
    assert db.add_channel_to_watchlist({"youtube_id": "UC1", "title": "Gamers Nexus"})
    items = db.query_channel_library()["items"]
    assert [c["id"] for c in items] == ["UC1"] and items[0]["owned_count"] == 0


def test_channel_page_annotation_uses_the_owned_variant():
    """All UI 'downloaded' badges read owned_youtube_video_ids; the inclusive
    dedup reader must not appear in the endpoint file at all."""
    assert "owned_youtube_video_ids" in _YOUTUBE_API
    assert "downloaded_youtube_video_ids" not in _YOUTUBE_API


# ── frontend contract ────────────────────────────────────────────────────────
def test_repair_ui_knows_the_new_type():
    assert "youtube_ghost: 'YouTube Ghost'" in _REPAIR_JS
    assert "youtube_ghost: 'Mark Deleted'" in _REPAIR_JS
    assert "marked_deleted: 'Marked Deleted'" in _REPAIR_JS
    assert "forgotten: 'Forgotten'" in _REPAIR_JS
    assert "function ghostDetailHTML" in _REPAIR_JS
    assert "youtube_mass_missing: 'Mass Missing'" in _REPAIR_JS
    assert "youtube_mass_missing: 'Flag Individually'" in _REPAIR_JS
    assert "flagged: 'Flagged'" in _REPAIR_JS
    assert "function massMissingDetailHTML" in _REPAIR_JS


def test_repair_ui_sends_the_forget_action():
    assert "data-vjr-fix-action=\"forget\"" in _REPAIR_JS
    handler = _REPAIR_JS.split("data-vjr-fix-action]')")[1].split("data-vjr-fix]")[0]
    assert "fix_action" in handler                      # posts the chosen action
    assert "'/fix'" in handler                          # same endpoint as approve
