"""server activity live view: plex and jellyfin merged into one payload with the
pulse, the bandwidth series and the recorder fed from the same poll; stop and
message on both servers; geo caching; settings; the avatar proxy.
"""

from __future__ import annotations

import time
from xml.etree import ElementTree as ET

import core.server_activity as sa
from core.server_activity import geo, live, store
from tests.sa_helpers import FakePlex, FakeResponse, sa_env  # noqa: F401 - fixture


def _resources(at, host=14.2):
    mc = ET.Element("MediaContainer")
    ET.SubElement(mc, "StatisticsResources", at=str(int(at - 30)), hostCpuUtilization="99", processCpuUtilization="9",
                  timespan="6")
    ET.SubElement(mc, "StatisticsResources", at=str(int(at)), hostCpuUtilization=str(host),
                  processCpuUtilization="6.1", timespan="6")
    return mc


def test_both_servers_merge_into_one_pulse(sa_env):
    sa_env.use_plex(FakePlex(resources=_resources(time.time())))
    sa_env.use_jellyfin()
    sa_env.settings["upload_mbps"] = 40
    out = live.get_activity(max_age=0)
    assert out["ok"] is True
    assert [s["server"] for s in out["servers"]] == ["plex", "jellyfin", "soulsync"]
    assert out["servers"][0]["name"] == "Basement" and out["servers"][0]["plex_pass"] is True
    sm = out["summary"]
    assert sm["streams"] == 7 and sm["watching"] == 4 and sm["listening"] == 3
    assert sm["transcodes"] == 3 and sm["transcoders"] == {"running": 3, "hw": 2, "sw": 1}
    assert sm["remote_bandwidth_kbps"] == 11800 + 70 + 3200
    assert sm["total_bandwidth_kbps"] == 11800 + 2900 + 70 + 8000 + 9640 + 3200 + 64
    assert sm["upload_cap_kbps"] == 40000
    assert sm["cpu"]["host_pct"] == 14.2 and sm["cpu"]["process_pct"] == 6.1
    assert out["bandwidth"]["points"][-1]["total_kbps"] == sm["total_bandwidth_kbps"]
    kinds = {s["title"]: s["kind"] for s in out["sessions"]}
    assert kinds["Project Hail Mary"] == "audiobook" and kinds["The Selfish Gene"] == "audiobook"
    book = next(s for s in out["sessions"] if s["title"] == "Project Hail Mary")
    assert book["book"]["progress"]["chapter"]["count"] == 32
    for s in out["sessions"]:
        assert "_link" not in s and "_user_thumb" not in s and "_session_id" not in s
        assert s["started_at"] is not None and s["play_key"]
        assert s["stream"]["method"] in ("Direct Play", "Direct Stream", "Transcode")   # the old drawer's fields


def test_polling_stays_one_call_per_server(sa_env):
    """no n+1: the library item and the book are looked up once, then cached."""
    plex = sa_env.use_plex(FakePlex(resources=_resources(time.time())))
    live.get_activity(max_age=0)
    first = [c[0] for c in plex.calls]
    assert first.count("/status/sessions") == 1
    plex.calls.clear()
    live.get_activity(max_age=0)
    assert [c[0] for c in plex.calls] == ["/status/sessions"]


def test_a_burst_of_new_streams_spends_a_bounded_lookup_budget(sa_env):
    from core.server_activity import plex as px
    root = ET.Element("MediaContainer")
    for i in range(20):
        ET.SubElement(root, "Video", type="movie", ratingKey=str(100 + i), sessionKey=str(i), title=f"m{i}")
    plex = sa_env.use_plex(FakePlex(root=root))
    out = live.get_activity(max_age=0)
    assert out["summary"]["streams"] == 20
    meta_calls = [c for c in plex.calls if c[0].startswith("/library/metadata/")]
    assert len(meta_calls) == px.SOURCE_FETCH_BUDGET


def test_shared_snapshot_serves_every_drawer(sa_env):
    plex = sa_env.use_plex()
    live.get_activity(max_age=0)
    plex.calls.clear()
    for _ in range(5):
        live.get_activity()
    assert plex.calls == []


def test_plays_are_recorded_from_the_poll(sa_env):
    sa_env.use_plex()
    live.get_activity(max_age=0)
    live.writer.flush()
    rows = store.query("SELECT title, kind, method, user, remote FROM server_plays ORDER BY title")
    assert {r["title"] for r in rows} == {"Dune: Part Two", "Everything In Its Right Place",
                                          "Project Hail Mary", "Crimson Sky"}
    dune = next(r for r in rows if r["title"] == "Dune: Part Two")
    assert dune["method"] == "transcode" and dune["user"] == "maya" and dune["remote"] == 1
    assert store.query("SELECT streams FROM server_activity_minutes")[0]["streams"] == 4


def test_an_unreachable_server_closes_no_plays(sa_env):
    plex = sa_env.use_plex()
    live.get_activity(max_age=0)
    plex.fail = True
    out = live.get_activity(max_age=0)
    assert out["ok"] is False and out["reason"] == "unreachable"
    assert len(live.recorder.open) == 4


def test_no_media_server_still_has_the_player(sa_env):
    """soulsync's own player is always a server: no plex or jellyfin is an
    empty view, not an error"""
    out = live.get_activity(max_age=0)
    assert out["ok"] is True and out["sessions"] == []
    assert out["servers"] == [{"server": "soulsync", "name": "SoulSync", "version": None,
                               "reachable": True, "plex_pass": None}]


def test_remote_viewers_are_looked_up_and_home_ones_never(sa_env):
    sa_env.use_plex()
    live.get_activity(max_age=0)
    assert sorted(sa_env.geo_scheduled) == ["73.41.208.17", "81.2.69.160"]


def test_geo_lookup_off_sends_nothing(sa_env):
    sa_env.use_plex()
    sa_env.settings["geo_lookup"] = False
    live.get_activity(max_age=0)
    assert sa_env.geo_scheduled == []


# ── geo ─────────────────────────────────────────────────────────────────────

GEO_XML = ('<?xml version="1.0"?><location code="US" continent_code="NA" country="United States" city="Chicago" '
           'european_union_member="0" in_privacy_restricted_country="0" postal_code="60601" '
           'subdivisions="Illinois" time_zone="America/Chicago" coordinates="41.85, -87.65"/>')


def test_geo_parses_plex_tv_and_shortens_the_label():
    g = geo.parse(GEO_XML)
    assert g["label"] == "Chicago, Illinois, US" and g["short"] == "Chicago, IL"
    assert g["lat"] == 41.85 and g["timezone"] == "America/Chicago"
    uk = geo.parse('{"location": {"city": "Leeds", "subdivisions": "England", "country": "United Kingdom", '
                   '"code": "GB"}}')
    assert uk["short"] == "Leeds, UK"
    assert geo.parse("") is None and geo.parse("<html/>") is None


def test_geo_is_asked_once_then_cached_in_memory_and_db(sa_env, monkeypatch):
    sa_env.use_plex()
    asked = []
    monkeypatch.setattr(geo, "_http_get", lambda ip, token: asked.append((ip, token)) or GEO_XML)
    now = 1_000_000.0
    assert geo.fetch_now("73.41.208.17", now)["city"] == "Chicago"
    assert asked == [("73.41.208.17", "secret-plex-token")]
    assert geo.lookup("73.41.208.17", now + 10)["city"] == "Chicago"
    geo.reset()                                   # a restart: the db still has it
    assert geo.lookup("73.41.208.17", now + 20)["city"] == "Chicago"
    assert len(asked) == 1 and sa_env.geo_scheduled == []
    # a month on it is asked again (the old answer shows meanwhile)
    assert geo.lookup("73.41.208.17", now + geo.OK_TTL + 1)["city"] == "Chicago"
    assert sa_env.geo_scheduled == ["73.41.208.17"]


def test_geo_never_sends_a_home_address(sa_env, monkeypatch):
    sa_env.use_plex()
    monkeypatch.setattr(geo, "_http_get", lambda ip, token: (_ for _ in ()).throw(AssertionError(ip)))
    for ip in ("192.168.1.5", "10.0.0.2", "127.0.0.1", "fe80::1", "172.16.4.4"):
        assert geo.lookup(ip) is None and geo.fetch_now(ip) is None
    assert sa_env.geo_scheduled == []


def test_a_failed_lookup_is_remembered_for_a_day(sa_env, monkeypatch):
    sa_env.use_plex()
    monkeypatch.setattr(geo, "_http_get", lambda ip, token: None)
    assert geo.fetch_now("8.8.8.8", 0.0) is None
    assert geo.lookup("8.8.8.8", 100.0) is None and sa_env.geo_scheduled == []
    geo.lookup("8.8.8.8", geo.MISS_TTL + 1)
    assert sa_env.geo_scheduled == ["8.8.8.8"]


# ── controls ────────────────────────────────────────────────────────────────

def test_plex_stop_sends_the_session_id_and_the_message(sa_env):
    plex = sa_env.use_plex()
    live.get_activity(max_age=0)
    res = sa.stop_session("27", "Back in 5")
    assert res == {"ok": True}
    call = [c for c in plex.calls if c[0] == "/status/sessions/terminate"]
    assert call == [("/status/sessions/terminate", {"sessionId": "b9c8d7e6f5", "reason": "Back in 5"})]


def test_plex_stop_without_plex_pass_says_why(sa_env):
    sa_env.use_plex(FakePlex(plex_pass=False))
    live.get_activity(max_age=0)
    res = sa.stop_session("plex:27", "bye")
    assert res["ok"] is False and res["code"] == "plex_pass" and "Plex Pass" in res["error"]


def test_plex_stop_of_a_gone_stream(sa_env):
    sa_env.use_plex()
    assert sa.stop_session("404", "x")["ok"] is False


def test_jellyfin_stop_shows_the_message_then_stops(sa_env):
    sa_env.use_jellyfin()
    res = sa.stop_session("jellyfin:7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a", "Please watch at home tonight.")
    assert res == {"ok": True, "message_shown": True}
    posts = [r for r in sa_env.requests if r[0] == "POST"]
    assert posts[0][1] == "http://jf.test:8096/Sessions/7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a/Message"
    assert posts[0][2]["Text"] == "Please watch at home tonight." and posts[0][2]["TimeoutMs"] > 0
    assert posts[1][1] == "http://jf.test:8096/Sessions/7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a/Playing/Stop"


def test_jellyfin_message_without_stopping(sa_env):
    sa_env.use_jellyfin()
    res = sa.message_session("jellyfin:7a7a", "Server restarts in 10 minutes", header="Heads up")
    assert res == {"ok": True}
    (post,) = [r for r in sa_env.requests if r[0] == "POST"]
    assert post[1].endswith("/Sessions/7a7a/Message")
    assert post[2] == {"Text": "Server restarts in 10 minutes", "TimeoutMs": 15000, "Header": "Heads up"}


def test_jellyfin_ids_that_walk_out_are_refused(sa_env):
    sa_env.use_jellyfin()
    for bad in ("jellyfin:../Users", "jellyfin:a/b", "jellyfin:"):
        assert sa.stop_session(bad, "x")["ok"] is False
        assert sa.message_session(bad, "x")["ok"] is False
    assert [r for r in sa_env.requests if r[0] == "POST"] == []


def test_plex_has_no_message_only_call(sa_env):
    sa_env.use_plex()
    assert sa.message_session("27", "hi")["ok"] is False


def test_jellyfin_refusal_is_reported(sa_env):
    sa_env.use_jellyfin(extra=lambda m, url, body: FakeResponse(403, None))
    res = sa.stop_session("jellyfin:7a7a", "")
    assert res["ok"] is False and "403" in res["error"]


# ── settings ────────────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def test_settings_defaults_on_an_install_without_the_keys(monkeypatch):
    from core.server_activity import settings as st
    monkeypatch.setattr(st, "_cfg", lambda: _Cfg())
    out = st.get_settings()
    assert out["upload_mbps"] is None and out["geo_lookup"] is True and out["retention_days"] == 0
    assert out["stop_presets"] == st.DEFAULT_STOP_PRESETS


def test_settings_validate_and_save(monkeypatch):
    import pytest
    from core.server_activity import settings as st
    cfg = _Cfg()
    monkeypatch.setattr(st, "_cfg", lambda: cfg)
    out = st.save_settings({"upload_mbps": "40", "retention_days": 365, "geo_lookup": False,
                            "stop_presets": ["  a  ", "a", "", "b"]})
    assert out["upload_mbps"] == 40.0 and out["retention_days"] == 365 and out["geo_lookup"] is False
    assert out["stop_presets"] == ["a", "b"]
    assert st.save_settings({"upload_mbps": ""})["upload_mbps"] is None        # blank hides the bar
    for bad in ({"upload_mbps": "fast"}, {"retention_days": -1}, {"stop_presets": "x"}, {"nope": 1}):
        with pytest.raises(ValueError):
            st.save_settings(bad)


def test_the_default_config_seeds_the_section():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "settings.py").read_text(encoding="utf-8")
    assert '"server_activity": {' in src
    ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(encoding="utf-8")
    assert "'mcp', 'server_activity']" in ws


# ── avatars ─────────────────────────────────────────────────────────────────

def test_avatar_proxy_only_fetches_what_the_server_named(sa_env, monkeypatch):
    from core.server_activity import images
    sa_env.use_plex()
    live.get_activity(max_age=0)
    import requests
    fetched = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: fetched.append(url) or
                        FakeResponse(200, None, ctype="image/png", content=b"png"))
    assert images.fetch_avatar("plex:7341") == (b"png", "image/png")
    assert fetched == ["https://plex.tv/users/a1b2c3d4e5f6/avatar?c=1711111111"]
    assert images.fetch_avatar("plex:../../x") is None
    assert images.fetch_avatar("https://evil.example/x.png") is None
    assert not images.is_plex_avatar_url("https://evil.example/plex.tv/a.png")
    assert not images.is_plex_avatar_url("http://plex.tv/users/a/avatar")
    assert images.is_plex_avatar_url("https://assets.plex.tv/a.png")


def test_jellyfin_avatar_uses_the_header_not_the_url(sa_env, monkeypatch):
    from core.server_activity import images
    sa_env.use_jellyfin()
    import requests
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        seen.append((url, params, headers))
        return FakeResponse(200, None, ctype="image/jpeg", content=b"jpg")
    monkeypatch.setattr(requests, "get", fake_get)
    assert images.fetch_avatar("jellyfin:0c1d") == (b"jpg", "image/jpeg")
    url, params, headers = seen[0]
    assert url == "http://jf.test:8096/UserImage" and params == {"userId": "0c1d"}
    assert "secret-jf-key" not in url and headers["X-Emby-Token"] == "secret-jf-key"


# ── the sampler ─────────────────────────────────────────────────────────────

def test_the_sampler_records_while_nobody_watches_and_idles_without_a_server(sa_env):
    emitted = []
    assert live.sampler_tick(False, emitted.append) is None      # no server: no poll at all
    plex = sa_env.use_plex()
    data = live.sampler_tick(False, emitted.append)
    assert data["summary"]["streams"] == 4 and emitted == []      # recorded, nobody to push to
    assert len(live.recorder.open) == 4
    live._snapshot.update(at=0.0)
    live.sampler_tick(True, emitted.append)
    assert len(emitted) == 1 and plex.calls


def test_the_sampler_loop_stops_and_flushes(sa_env):
    sa_env.use_plex()
    ticks = []
    live.run_sampler_forever(sleep=lambda s: ticks.append(s), should_stop=lambda: len(ticks) >= 25,
                             has_subscribers=lambda: False, emit=lambda d: None)
    assert store.query("SELECT COUNT(*) AS n FROM server_plays")[0]["n"] == 4
