"""Video automation parity — the event bus, its publishers, and the builder palette.

Boulder: "video needs to be just as fully featured [as the music builder]...
no trigger or actions locked away". Three layers under test:

1. The generic bridge (core/video/download_events): core/video and database/
   publish typed events; web_server forwards each to the engine's same-named
   event trigger. One forwarder, every event.
2. The publishers: download terminal outcomes (monitor + YouTube), repair
   scans/findings, wishlist/watchlist writes (the DB spine — refresh-upserts
   must NOT fire, or the 6-hourly scans would spam events daily).
3. The palette (core/automation/blocks): every new trigger/action visible on
   the video builder; monthly_time unlocked for both sides; every video action
   block backed by a registered handler (nothing draggable-but-dead).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from core.video import download_events
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_REGISTRATION_SRC = (_ROOT / "core" / "automation" / "handlers" / "registration.py").read_text(encoding="utf-8")
_ENGINE_SRC = (_ROOT / "core" / "automation_engine.py").read_text(encoding="utf-8")
_VAUTO_JS = (_ROOT / "webui" / "static" / "video" / "video-automations.js").read_text(encoding="utf-8")
_MUSIC_JS = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")

_ids = itertools.count(1)


@pytest.fixture()
def events():
    fired = []
    download_events.register_event_forwarder(lambda t, d: fired.append((t, d)))
    try:
        yield fired
    finally:
        download_events._reset_for_tests()


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _types(fired):
    return [t for t, _ in fired]


# ── the bridge ───────────────────────────────────────────────────────────────
def test_publish_reaches_every_forwarder_and_batch_wrapper_routes(events):
    download_events.publish("video_download_completed", {"title": "X"})
    download_events.notify_batch_complete({"completed": 3})
    assert events == [("video_download_completed", {"title": "X"}),
                      ("video_batch_complete", {"completed": 3})]


def test_one_broken_forwarder_never_blocks_the_others(events):
    def boom(t, d):
        raise RuntimeError("subscriber bug")
    download_events.register_event_forwarder(boom)   # registered AFTER the collector
    download_events.publish("video_batch_complete", {})
    assert _types(events) == ["video_batch_complete"]


# ── the palette ──────────────────────────────────────────────────────────────
_NEW_TRIGGERS = [
    "video_download_completed", "video_download_failed", "video_import_failed",
    "video_upgrade_completed", "video_repair_finding_created",
    "video_repair_scan_completed", "video_wishlist_item_added",
    "video_watchlist_added", "video_watchlist_removed",
    "video_collections_synced", "video_overlays_applied",
    "video_database_update_completed",
]


def test_video_palette_has_the_full_trigger_catalog():
    from core.automation.blocks import blocks_for_scope
    vt = {b["type"]: b for b in blocks_for_scope("video")["triggers"]}
    for t in _NEW_TRIGGERS:
        assert t in vt, f"missing trigger block {t}"
    # per-item triggers are condition-filterable (the anti-spam knob)
    for t in ("video_download_completed", "video_repair_finding_created",
              "video_wishlist_item_added"):
        assert vt[t].get("has_conditions") and vt[t].get("condition_fields")
    # and none of them leak onto the music builder
    mt = {b["type"] for b in blocks_for_scope("music")["triggers"]}
    assert not (set(_NEW_TRIGGERS) & mt)


def test_monthly_time_is_unlocked_on_both_sides():
    from core.automation.blocks import blocks_for_scope
    for scope in ("music", "video"):
        blocks = {b["type"]: b for b in blocks_for_scope(scope)["triggers"]}
        assert "monthly_time" in blocks, scope
        keys = [f["key"] for f in blocks["monthly_time"]["config_fields"]]
        assert keys == ["time", "day_of_month"]     # what schedule.py reads
    assert "'monthly_time'" in _ENGINE_SRC          # engine trigger registry


def test_no_video_action_block_is_draggable_but_dead():
    """Every action on the video palette must have a registered handler —
    a block without one saves fine and then silently never runs."""
    from core.automation.blocks import blocks_for_scope
    for b in blocks_for_scope("video")["actions"]:
        if b["type"] == "notify_only":
            continue                                 # engine-internal, no handler
        assert f"'{b['type']}'" in _REGISTRATION_SRC, f"no handler for {b['type']}"


def test_formerly_locked_actions_now_have_blocks():
    from core.automation.blocks import blocks_for_scope
    va = {b["type"] for b in blocks_for_scope("video")["actions"]}
    assert {"video_apply_overlays", "video_sync_collections",
            "video_clean_plex_images", "video_run_repair_job"} <= va


# ── DB spine publishers (wishlist / watchlist) ───────────────────────────────
def test_movie_wish_fires_once_and_refresh_upserts_stay_silent(db, events):
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)    # refresh
    assert _types(events) == ["video_wishlist_item_added"]
    assert events[0][1] == {"kind": "movie", "title": "The Matrix", "count": 1}


def test_episode_wish_counts_only_new_rows(db, events):
    eps = [{"season_number": 1, "episode_number": 1}, {"season_number": 1, "episode_number": 2}]
    db.add_episodes_to_wishlist(100, "Severance", eps)
    db.add_episodes_to_wishlist(100, "Severance", eps + [{"season_number": 1, "episode_number": 3}])
    assert _types(events) == ["video_wishlist_item_added"] * 2
    assert events[0][1]["count"] == 2 and events[1][1]["count"] == 1


def test_youtube_wish_counts_only_new_rows(db, events):
    chan = {"youtube_id": "UC1", "title": "Veritasium"}
    vids = [{"youtube_id": "v1", "title": "A"}, {"youtube_id": "v2", "title": "B"}]
    db.add_videos_to_wishlist(chan, vids)
    db.add_videos_to_wishlist(chan, vids)                     # all refresh → silent
    assert _types(events) == ["video_wishlist_item_added"]
    assert events[0][1] == {"kind": "youtube", "title": "Veritasium", "count": 2}


def test_watchlist_follow_and_unfollow_fire_once(db, events):
    db.add_to_watchlist("show", 100, "Severance")
    db.add_to_watchlist("show", 100, "Severance")             # refresh → silent
    db.remove_from_watchlist("show", 100)
    db.remove_from_watchlist("show", 100)                     # already muted → silent
    assert _types(events) == ["video_watchlist_added", "video_watchlist_removed"]
    assert events[1][1] == {"kind": "show", "title": "Severance"}


def test_mute_of_something_never_followed_is_not_an_unfollow(db, events):
    db.remove_from_watchlist("show", 999)                     # tombstone only
    assert events == []


def test_channel_and_playlist_follow_events(db, events):
    db.add_channel_to_watchlist({"youtube_id": "UC1", "title": "Gamers Nexus"})
    db.add_channel_to_watchlist({"youtube_id": "UC1", "title": "Gamers Nexus"})
    db.remove_channel_from_watchlist("UC1")
    db.add_playlist_to_watchlist({"playlist_id": "PL1", "title": "Mixtape"})
    db.remove_playlist_from_watchlist("PL1")
    assert _types(events) == ["video_watchlist_added", "video_watchlist_removed",
                              "video_watchlist_added", "video_watchlist_removed"]
    assert events[0][1]["kind"] == "channel" and events[2][1]["kind"] == "playlist"


# ── download terminal publishers ─────────────────────────────────────────────
def _dl(**kw):
    base = {"id": next(_ids), "kind": "movie", "title": "Heat", "year": 1995,
            "source": "slskd", "quality_label": "1080p",
            "search_ctx": json.dumps({"season": None, "episode": None})}
    base.update(kw)
    return base


def test_monitor_publishes_completed_and_upgrade(events):
    from core.video.download_monitor import _publish_terminal
    _publish_terminal(_dl(), {"status": "completed", "dest_path": "/lib/Heat.mkv",
                              "quality_label": "2160p", "_upgraded": True})
    assert _types(events) == ["video_download_completed", "video_upgrade_completed"]
    assert events[0][1]["quality"] == "2160p" and events[0][1]["dest_path"] == "/lib/Heat.mkv"


def test_monitor_publishes_import_failed(events):
    from core.video.download_monitor import _publish_terminal
    _publish_terminal(_dl(), {"status": "import_failed", "error": "sample file"})
    assert _types(events) == ["video_import_failed"]
    assert events[0][1]["error"] == "sample file"


def test_final_download_failure_publishes(db, events, monkeypatch):
    from core.video import download_monitor as mon
    dl_id = None
    conn = db._get_connection()
    cur = conn.execute("INSERT INTO video_downloads (kind, title, status, source) "
                       "VALUES ('movie','Heat','downloading','slskd')")
    dl_id = cur.lastrowid
    conn.commit(); conn.close()
    monkeypatch.setattr("core.video.retry.plan_retry",
                        lambda row, max_attempts=3: {"action": "fail", "reason": "out of options"})
    mon._fail_or_retry(db, {"id": dl_id, "kind": "movie", "title": "Heat", "source": "slskd"},
                       "Transfer died")
    assert "video_download_failed" in _types(events)
    failed = dict(events)["video_download_failed"]
    assert failed["error"] == "Transfer died" and failed["title"] == "Heat"


def test_update_video_download_strips_transient_keys(db):
    conn = db._get_connection()
    cur = conn.execute("INSERT INTO video_downloads (kind, title, status) VALUES ('movie','X','downloading')")
    dl_id = cur.lastrowid
    conn.commit(); conn.close()
    # would raise 'no such column: _upgraded' without the strip
    db.update_video_download(dl_id, status="completed", _upgraded=True)
    assert db.get_video_download(dl_id)["status"] == "completed"


def test_youtube_publisher_uses_the_shared_payload_shape(events):
    from core.video.youtube_download import _publish_event
    dl = {"id": 1, "title": "Big Video", "quality_label": "1080p",
          "search_ctx": json.dumps({"channel": "Veritasium", "channel_id": "UC9",
                                    "published_at": "2026-03-01"})}
    _publish_event("video_download_completed", dl, dest_path="/yt/v.mp4")
    t, d = events[0]
    assert t == "video_download_completed"
    assert d["kind"] == "youtube" and d["channel"] == "Veritasium"
    assert d["year"] == "2026" and d["dest_path"] == "/yt/v.mp4"


# ── repair publishers ────────────────────────────────────────────────────────
def test_repair_scan_publishes_finding_and_summary_events(db, events, tmp_path):
    from core.video.repair.worker import VideoRepairWorker
    path = tmp_path / "gone.mp4"
    db.record_download_history({
        "id": next(_ids), "kind": "youtube", "source": "youtube", "status": "completed",
        "title": "Ghost", "media_id": "g1", "dest_path": str(path),
        "search_ctx": json.dumps({"channel_id": "UC1"}), "completed_at": "2026-07-01"})
    VideoRepairWorker(db)._run_job("youtube_ghosts", forced=True)
    kinds = _types(events)
    assert "video_repair_finding_created" in kinds and "video_repair_scan_completed" in kinds
    finding = dict(events)["video_repair_finding_created"]
    assert finding["finding_type"] == "youtube_ghost" and finding["severity"] == "warning"
    summary = dict(events)["video_repair_scan_completed"]
    assert summary["job_id"] == "youtube_ghosts" and summary["findings_created"] == 1
    assert summary["status"] == "finished"


# ── the run-repair action handler ────────────────────────────────────────────
class _FakeWorker:
    def __init__(self):
        self.queued = []

    def get_all_job_info(self):
        return [{"job_id": "broken_files", "enabled": True},
                {"job_id": "youtube_ghosts", "enabled": False}]

    def run_job_now(self, job_id):
        self.queued.append(job_id)
        return True


def test_run_repair_action_all_respects_per_job_toggles(monkeypatch):
    from core.automation.handlers import video_run_repair as h
    w = _FakeWorker()
    monkeypatch.setattr("core.video.repair.worker.get_video_repair_worker", lambda db=None: w)
    res = h.auto_video_run_repair_job({"job_id": "all"}, None)
    assert res["status"] == "completed" and w.queued == ["broken_files"]   # disabled job untouched


def test_run_repair_action_explicit_pick_overrides_the_toggle(monkeypatch):
    from core.automation.handlers import video_run_repair as h
    w = _FakeWorker()
    monkeypatch.setattr("core.video.repair.worker.get_video_repair_worker", lambda db=None: w)
    res = h.auto_video_run_repair_job({"job_id": "youtube_ghosts"}, None)
    assert res["status"] == "completed" and w.queued == ["youtube_ghosts"]
    assert h.auto_video_run_repair_job({"job_id": "nope"}, None)["status"] == "error"


# ── frontend contracts ───────────────────────────────────────────────────────
def test_video_page_renders_user_built_automations():
    """The old page rendered ONLY the System section — a user-built automation
    saved from the video builder vanished on reload."""
    assert "data-vauto-user-section" in _VAUTO_JS
    assert "'My Automations'" in _VAUTO_JS
    assert "a.is_system" in _VAUTO_JS                # sys/user split, like music
    assert "Custom</span>" in _VAUTO_JS or "Custom<" in _VAUTO_JS


def test_new_block_types_have_builder_icons():
    for t in ("monthly_time", "video_download_completed", "video_repair_finding_created",
              "video_run_repair_job", "video_watchlist_added"):
        assert t + ":" in _MUSIC_JS, f"no icon for {t}"


def test_hub_is_side_aware_with_video_content():
    """The Automation Hub renders VIDEO pipelines/recipes/guides/tips on the
    video page (it used to blank four tabs with 'coming soon')."""
    for name in ("VIDEO_HUB_GROUPS", "VIDEO_HUB_RECIPES", "VIDEO_HUB_GUIDES",
                 "VIDEO_HUB_TIPS", "VIDEO_HUB_REFERENCE"):
        assert name in _MUSIC_JS, name
    for getter in ("_hubGroups()", "_hubRecipes()", "_hubGuides()", "_hubTips()",
                   "_hubReference()"):
        assert getter in _MUSIC_JS, getter
    assert "coming soon" not in _VAUTO_JS          # panes no longer emptied
    assert "_HUB_EMPTY" not in _VAUTO_JS
    # deploys from the video page tag ownership; recipes open the video builder
    assert "if (_hubIsVideo()) payload.owned_by = 'video';" in _MUSIC_JS
    assert "_hubIsVideo() ? showVideoAutomationBuilder() : showAutomationBuilder()" in _MUSIC_JS


def test_video_hub_never_redeploys_the_system_processors():
    """The system automations already scan watchlists + drain wishlists on
    schedules — a hub pipeline deploying user copies would double-download.
    Video pipelines must stick to alerts, chains and maintenance."""
    groups = _MUSIC_JS.split("const VIDEO_HUB_GROUPS = [")[1].split("\nconst VIDEO_HUB_RECIPES")[0]
    for banned in ("video_process_movie_wishlist", "video_process_episode_wishlist",
                   "video_process_youtube_wishlist", "video_scan_watchlist_people",
                   "video_scan_watchlist_channels", "video_scan_watchlist_playlists",
                   "video_add_airing_episodes"):
        assert f"action_type: '{banned}'" not in groups, f"pipeline redeploys {banned}"
