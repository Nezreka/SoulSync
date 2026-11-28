"""tests for Live TV DVR rules, conflict detection, sidecars, time-shift, and scheduled refresh."""

from __future__ import annotations

import time
from pathlib import Path

from core.player.live import dvr, refresh, relay, store
from database.player_database import PlayerDatabase


def test_dvr_rules_conflict_detection_and_sidecars(tmp_path):
    db = PlayerDatabase(str(tmp_path / "player.db"))
    src = store.add_source(db, kind="hdhomerun", name="OTA", url="http://192.168.1.50", tuner_count=1)
    store.upsert_channels(
        db,
        src["id"],
        [
            {"guide_number": "5.1", "name": "NBC", "stream_url": "http://192.168.1.50:5004/auto/v5.1", "tvg_id": "5.1"},
            {"guide_number": "7.1", "name": "ABC", "stream_url": "http://192.168.1.50:5004/auto/v7.1", "tvg_id": "7.1"},
        ],
    )
    chans = {c["guide_number"]: c for c in store.list_channels(db)}
    now = time.time()
    store.replace_programs(
        db,
        src["id"],
        [
            {
                "epg_channel_id": "5.1",
                "start_ts": now + 600,
                "end_ts": now + 4200,
                "title": "Sunday Night Football",
                "subtitle": "Chiefs at Bills",
                "episode_num": "S2026E12",
                "description": "Live primetime game",
                "is_new": 1,
            },
            {
                "epg_channel_id": "7.1",
                "start_ts": now + 900,
                "end_ts": now + 4500,
                "title": "Evening Movie",
                "subtitle": "",
                "is_new": 1,
            },
        ],
    )

    # rule schedules SNF on 5.1
    dvr.add_rule(db, name="Football", title_match="Football", new_only=True, target_dir=str(tmp_path / "tv"))
    ev = dvr.evaluate_rules(db, now=now)
    assert ev["scheduled"] == 1
    assert ev["conflicts"] == 0

    # overlapping recording on 7.1 with only 1 tuner -> marked conflict
    rec2 = dvr.schedule_recording(
        db,
        channel_id=chans["7.1"]["id"],
        title="Evening Movie",
        start_ts=now + 900,
        end_ts=now + 4500,
    )
    assert rec2["status"] == "conflict"
    assert "1 tuners" in (rec2["error"] or "")

    # execute first recording with fake ffmpeg runner -> writes .ts + .nfo
    snf_rec = next(r for r in dvr.list_recordings(db) if r["title"] == "Sunday Night Football")

    def fake_runner(cmd, out_path):
        Path(out_path).write_bytes(b"TSDATA" * 100)
        return True

    done = dvr.record_now(db, snf_rec["id"], target_dir=str(tmp_path / "tv"), runner=fake_runner)
    assert done["status"] == "completed"
    assert Path(done["file_path"]).is_file()
    assert Path(done["nfo_path"]).is_file()
    assert "<showtitle>Sunday Night Football</showtitle>" in Path(done["nfo_path"]).read_text(encoding="utf-8")

    # commercial .edl sidecar writer produces standard type-3 entries
    edl = dvr.write_commercial_edl(done["file_path"], [(120.5, 240.0), (900.0, 1020.0)])
    assert edl and Path(edl).is_file()
    assert "120.50\t240.00\t3" in Path(edl).read_text(encoding="utf-8")


def test_timeshift_buffer_and_scheduled_refresh(tmp_path):
    db = PlayerDatabase(str(tmp_path / "player.db"))
    m3u_file = tmp_path / "live.m3u"
    m3u_file.write_text("#EXTM3U\n#EXTINF:-1 tvg-id=\"news\",News 24\nhttp://example.test/news.m3u8\n", encoding="utf-8")
    src = store.add_source(db, kind="m3u", name="News", url=str(m3u_file))

    res = refresh.refresh_due_sources(db, interval_hours=6.0)
    assert res["refreshed"] == 1
    # running again immediately skips because it's not due yet
    res2 = refresh.refresh_due_sources(db, interval_hours=6.0)
    assert res2["refreshed"] == 0

    ch = store.list_channels(db)[0]
    sup = relay.LiveRelaySupervisor(cache_root=str(tmp_path / "hls"), spawner=lambda _c, _d: None)
    sess = sup.join(ch, "viewer-1")
    assert sess["timeshift_max_seconds"] == relay.TIMESHIFT_MINUTES * 60
    ts = sup.timeshift_status(ch["id"])
    assert ts is not None and ts["viewers"] == 1
    sup.stop_all()
