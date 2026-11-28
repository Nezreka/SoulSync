"""Live TV Phase 1 tests: HDHomeRun lineup & DRM hiding, M3U parsing,
XMLTV + gzip parsing, EPG window & fallback placeholders, shared HLS tuner
sharing & idle reaping, and /api/live/* endpoints."""

from __future__ import annotations

import gzip
import sqlite3
import time

import pytest
from flask import Flask

from core.player.live import hdhomerun, m3u, refresh, relay, store, xmltv


class _TempPlayerDB:
    def __init__(self, path: str):
        self.database_path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


class _FakeResp:
    def __init__(self, *, text="", content=b"", json_data=None, status_code=200):
        self.text = text
        self.content = content or text.encode("utf-8")
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_m3u_parser_extracts_attributes_and_vlc_headers():
    sample = """#EXTM3U
#EXTINF:-1 tvg-id="cbs.us" tvg-name="CBS HD" tvg-logo="https://ex/cbs.png" tvg-chno="2.1" group-title="Locals",CBS 2 HD
#EXTVLCOPT:http-user-agent=MyPlayer/1.0
#EXTVLCOPT:http-referrer=https://example.com
http://192.168.1.50:5004/auto/v2.1
#EXTINF:-1 tvg-id="pbs.us",PBS News
https://streams.example/pbs.m3u8
"""
    chans = m3u.parse_m3u(sample)
    assert len(chans) == 2
    assert chans[0]["guide_number"] == "2.1"
    assert chans[0]["name"] == "CBS 2 HD"
    assert chans[0]["tvg_id"] == "cbs.us"
    assert chans[0]["logo_url"] == "https://ex/cbs.png"
    assert chans[0]["group_title"] == "Locals"
    assert chans[0]["headers"] == {"User-Agent": "MyPlayer/1.0", "Referer": "https://example.com"}
    assert chans[1]["name"] == "PBS News"
    assert chans[1]["headers"] == {}


def test_xmltv_parser_handles_gzip_and_xmltv_ns():
    now = int(time.time())
    t1 = time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(now - 600))
    t2 = time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(now + 3000))
    xml_doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="5.1">
    <display-name>NBC 5</display-name>
    <icon src="https://ex/nbc.png"/>
  </channel>
  <programme start="{t1}" stop="{t2}" channel="5.1">
    <title>Sunday Night Football</title>
    <sub-title>Chiefs at Bills</sub-title>
    <desc>Live NFL coverage.</desc>
    <category>Sports</category>
    <episode-num system="xmltv_ns">2.4.</episode-num>
    <new/>
    <live/>
  </programme>
</tv>""".encode("utf-8")

    chans, progs = xmltv.parse_xmltv(gzip.compress(xml_doc))
    assert len(chans) == 1 and chans[0]["id"] == "5.1"
    assert len(progs) == 1
    p = progs[0]
    assert p["epg_channel_id"] == "5.1"
    assert p["title"] == "Sunday Night Football"
    assert p["subtitle"] == "Chiefs at Bills"
    assert p["episode_num"] == "S03E05"
    assert p["is_new"] is True and p["is_live"] is True


def test_hdhomerun_hides_drm_channels_and_translates_805():
    def fake_get(url, **_kw):
        if url.endswith("/lineup.json"):
            return _FakeResp(json_data=[
                {"GuideNumber": "5.1", "GuideName": "NBC", "URL": "http://hdhr/auto/v5.1"},
                {"GuideNumber": "5.2", "GuideName": "Protected", "URL": "http://hdhr/auto/v5.2", "DRM": 1},
                {"GuideNumber": "7.1", "GuideName": "ABC", "URL": "http://hdhr/auto/v7.1", "Tags": "favorite,drm"},
            ])
        raise AssertionError(url)

    chans = hdhomerun.fetch_lineup("http://192.168.1.50", http_get=fake_get)
    assert [c["is_drm"] for c in chans] == [False, True, True]
    assert "currently in use" in hdhomerun.friendly_tuner_error(503, {"X-HDHomeRun-Error": "805 All Tuners In Use"})


def test_store_preserves_overrides_hides_drm_and_builds_epg_window(tmp_path):
    db = _TempPlayerDB(str(tmp_path / "player.db"))
    src = store.add_source(db, kind="hdhomerun", name="Attic", url="http://192.168.1.50", tuner_count=2)
    stats = store.upsert_channels(db, src["id"], [
        {"guide_number": "5.1", "name": "NBC", "stream_url": "http://hdhr/auto/v5.1", "tvg_id": "5.1"},
        {"guide_number": "5.2", "name": "Encrypted", "stream_url": "http://hdhr/auto/v5.2", "is_drm": True},
        {"guide_number": "9.1", "name": "PBS", "stream_url": "http://hdhr/auto/v9.1"},
    ])
    assert stats["added"] == 3 and stats["drm_hidden"] == 1

    visible = store.list_channels(db)
    assert [c["guide_number"] for c in visible] == ["5.1", "9.1"]

    # Admin customizes NBC -> rename + custom EPG ID survives refresh
    nbc = visible[0]
    store.update_channel(db, nbc["id"], {"custom_name": "Local NBC", "epg_id": "nbc.custom"})
    store.upsert_channels(db, src["id"], [
        {"guide_number": "5.1", "name": "NBC", "stream_url": "http://hdhr/auto/v5.1", "tvg_id": "5.1"},
        {"guide_number": "9.1", "name": "PBS", "stream_url": "http://hdhr/auto/v9.1"},
    ])
    refreshed = store.get_channel(db, nbc["id"])
    assert refreshed["display_name"] == "Local NBC"
    assert refreshed["effective_epg_id"] == "nbc.custom"

    # EPG window: NBC gets real program, PBS gets clean 1-hour placeholders
    now = time.time()
    store.replace_programs(db, src["id"], [{
        "epg_channel_id": "nbc.custom",
        "start_ts": now - 600,
        "end_ts": now + 3000,
        "title": "Evening News",
    }])
    win = store.programs_window(db, now - 300, now + 3600)
    assert win[nbc["id"]][0]["title"] == "Evening News"
    pbs = visible[1]
    assert win[pbs["id"]][0]["placeholder"] is True


def test_relay_shares_tuner_enforces_limit_and_reaps_idle(tmp_path):
    spawned = []

    def fake_spawn(ch, work_dir):
        p = _FakeProc()
        spawned.append((ch["id"], p))
        return p

    sup = relay.LiveRelaySupervisor(cache_root=str(tmp_path / "live"), idle_seconds=30.0, spawner=fake_spawn)
    ch1 = {"id": "c1", "source_id": "hdhr1", "display_name": "NBC 5.1", "stream_url": "http://x/1", "tuner_count": 1}
    ch2 = {"id": "c2", "source_id": "hdhr1", "display_name": "CBS 2.1", "stream_url": "http://x/2", "tuner_count": 1}

    # Two viewers on the same channel share ONE tuner and ONE ffmpeg process
    s1 = sup.join(ch1, "viewer-a")
    s2 = sup.join(ch1, "viewer-b")
    assert s1["viewers"] == 1 and s2["viewers"] == 2
    assert len(spawned) == 1

    # A second channel on the same 1-tuner box gets a clear TunerBusyError naming the holder
    with pytest.raises(relay.TunerBusyError) as exc:
        sup.join(ch2, "viewer-c")
    assert "NBC 5.1" in str(exc.value)
    assert exc.value.holders[0]["channel_id"] == "c1"

    # When both viewers leave and idle window passes, ffmpeg stops and tuner frees up
    sup.leave("c1", "viewer-a")
    sup.leave("c1", "viewer-b")
    assert sup.reap_idle(now=time.time() + 10) == []
    assert sup.reap_idle(now=time.time() + 60) == ["c1"]
    assert spawned[0][1].terminated is True

    # Now ch2 tunes cleanly
    s3 = sup.join(ch2, "viewer-c")
    assert s3["channel_id"] == "c2" and len(spawned) == 2


def test_live_api_routes_end_to_end(tmp_path, monkeypatch):
    import api.live as live_api
    db = _TempPlayerDB(str(tmp_path / "player.db"))
    monkeypatch.setattr(live_api, "_db", lambda: db)
    sup = relay.LiveRelaySupervisor(cache_root=str(tmp_path / "live"), spawner=lambda ch, d: _FakeProc())
    monkeypatch.setattr(relay, "get_supervisor", lambda: sup)

    m3u_file = tmp_path / "channels.m3u"
    m3u_file.write_text("#EXTM3U\n#EXTINF:-1 tvg-id=\"kexp\" tvg-chno=\"10\",KEXP Live\nhttps://ex/kexp.m3u8\n", encoding="utf-8")

    app = Flask(__name__)
    app.register_blueprint(live_api.create_live_blueprint())

    @app.before_request
    def _admin():
        from flask import g
        g.profile_id = 1
        g.is_admin = True
        g.can_download = True

    c = app.test_client()
    r = c.post("/api/live/sources", json={"kind": "m3u", "name": "My M3U", "url": str(m3u_file)})
    assert r.status_code == 201
    sid = r.get_json()["source"]["id"]

    chans = c.get("/api/live/channels").get_json()["channels"]
    assert len(chans) == 1 and chans[0]["display_name"] == "KEXP Live"
    cid = chans[0]["id"]

    tuned = c.post(f"/api/live/tune/{cid}").get_json()
    assert tuned["success"] is True
    assert tuned["session"]["playlist_url"] == f"/api/live/stream/{cid}/stream.m3u8"

    progs = c.get("/api/live/programs").get_json()
    assert progs["success"] is True and cid in progs["programs"]

    assert c.post(f"/api/live/leave/{cid}").status_code == 200
    assert c.delete(f"/api/live/sources/{sid}").status_code == 200
    sup.stop_all()
