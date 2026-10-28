"""MetaSync SIN charts — pulled from the MetaSync node once a day, offered as
playlists, never synced or downloaded on their own (core/metasync_charts.py,
api/metasync_charts.py, the metasync_refresh_charts automation).

The pinned contract:

* storing the node's charts keeps a hidden, pre-identified template per
  chart (profile 0), and touches no wishlist and no media server;
* no real profile's playlist list shows a template;
* opening a chart gives the profile its own mirror, copied from the
  template with every match already in it;
* the next day's charts update that copy in place, keeping its matches;
* a chart that leaves the network loses its template, while a profile's
  own copy stays theirs;
* the daily pull skips quietly without MetaSync, reports a rejected key or
  an old node plainly, and sends the local key to the node.
"""

from __future__ import annotations

import json

import pytest

from core import metasync_charts as mc
from database.music_database import MusicDatabase

REC = "11111111-1111-4111-8111-%012d"


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "charts.db"))


def track(n, name=None):
    return {
        "id": REC % n,
        "name": name or f"Song {n}",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album", "images": [{"url": f"https://caa.example/{n}.jpg"}]},
        "duration_ms": 200000,
    }


def handoff(*charts_):
    return {"week": "2026-W43", "playlists": [
        {"key": key, "name": name, "tracks": tracks} for key, name, tracks in charts_
    ]}


class Identify:
    def __init__(self):
        self.ids = []

    def __call__(self, playlist_id):
        self.ids.append(playlist_id)


def _mark_matched(db, playlist_id):
    """What the background identify writes: discovery data on each track."""
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE mirrored_playlist_tracks SET extra_data = ? WHERE playlist_id = ?",
            (json.dumps({"discovered": True, "provider": "spotify"}), playlist_id),
        )
        conn.commit()


def test_a_handoff_offers_charts_and_wishlists_nothing(db):
    identify = Identify()
    result = mc.store_charts(db, handoff(("trending", "Trending in SoulSync", [track(1), track(2)])), identify)
    assert result == {"stored": 1, "removed": 0}
    template = mc._mirror_id(db, "trending", mc.TEMPLATE_PROFILE)
    assert identify.ids == [template]  # identified in the background
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM wishlist_tracks").fetchone()[0] == 0
    # no real profile sees the hidden template in its playlist list
    assert db.get_mirrored_playlists(profile_id=1) == []
    charts = mc.list_charts(db, 1)
    assert [(c["key"], c["track_count"], c["playlist_id"]) for c in charts] == [("trending", 2, None)]


def test_opening_gives_the_profile_its_own_already_matched_copy(db):
    identify = Identify()
    mc.store_charts(db, handoff(("trending", "Trending in SoulSync", [track(1), track(2)])), identify)
    template = mc._mirror_id(db, "trending", mc.TEMPLATE_PROFILE)
    _mark_matched(db, template)
    assert mc.list_charts(db, 1)[0]["ready"] is True

    playlist_id = mc.open_chart(db, "trending", 1, identify)
    assert playlist_id and playlist_id != template
    mine = db.get_mirrored_playlist(playlist_id, profile_id=1)
    assert mine["source"] == "metasync"
    rows = db.get_mirrored_playlist_tracks(playlist_id)
    assert all(json.loads(r["extra_data"])["discovered"] for r in rows)  # matches came along
    assert mc.open_chart(db, "trending", 1, identify) == playlist_id  # opening again reuses it
    assert mc.list_charts(db, 1)[0]["playlist_id"] == playlist_id
    assert [p["id"] for p in db.get_mirrored_playlists(profile_id=1)] == [playlist_id]


def test_next_weeks_handoff_updates_the_copy_and_keeps_its_matches(db):
    identify = Identify()
    mc.store_charts(db, handoff(("trending", "Trending · from ListenBrainz", [track(1), track(2)])), identify)
    playlist_id = mc.open_chart(db, "trending", 1, identify)
    _mark_matched(db, playlist_id)
    mc.store_charts(db, handoff(("trending", "Trending in SoulSync", [track(2), track(3)])), identify)
    rows = {r["source_track_id"]: r for r in db.get_mirrored_playlist_tracks(playlist_id)}
    assert set(rows) == {REC % 2, REC % 3}
    assert rows[REC % 2]["extra_data"]  # kept its match
    assert not rows[REC % 3]["extra_data"]  # new, queued for identify
    assert db.get_mirrored_playlist(playlist_id)["name"] == "Trending in SoulSync"
    assert playlist_id in identify.ids


def test_a_chart_that_leaves_drops_its_template_but_not_your_copy(db):
    identify = Identify()
    mc.store_charts(db, handoff(("trending", "T", [track(1)]), ("most-wanted", "M", [track(2)])), identify)
    mine = mc.open_chart(db, "most-wanted", 1, identify)
    result = mc.store_charts(db, handoff(("trending", "T", [track(1)])), identify)
    assert result == {"stored": 1, "removed": 1}
    assert mc._mirror_id(db, "most-wanted", mc.TEMPLATE_PROFILE) is None
    assert db.get_mirrored_playlist(mine, profile_id=1) is not None
    assert [c["key"] for c in mc.list_charts(db, 1)] == ["trending"]
    assert mc.open_chart(db, "most-wanted", 2, identify) is None


def test_validation_rejects_anything_not_chart_shaped(db):
    for bad in (
        None,
        {"playlists": "nope"},
        {"playlists": [{"key": "Bad Key", "name": "x", "tracks": []}]},
        {"playlists": [{"key": "a", "name": "", "tracks": []}]},
        {"playlists": [{"key": "a", "name": "x", "tracks": []}, {"key": "a", "name": "y", "tracks": []}]},
        {"playlists": [{"key": "a", "name": "x", "tracks": [{}] * 501}]},
    ):
        with pytest.raises(mc.ChartsError):
            mc.validate(bad)
    # an empty chart is simply not offered
    assert mc.validate({"playlists": [{"key": "a", "name": "x", "tracks": []}]}) == []


def test_chart_tracks_become_mirror_tracks():
    assert mc.chart_track_to_mirror(track(7)) == {
        "track_name": "Song 7",
        "artist_name": "Artist",
        "album_name": "Album",
        "duration_ms": 200000,
        "image_url": "https://caa.example/7.jpg",
        "source_track_id": REC % 7,
    }


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _config(values):
    return lambda key, default=None: values.get(key, default)


def test_the_daily_pull(db, monkeypatch):
    identify = Identify()
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return _Resp(200, handoff(("trending", "Trending in SoulSync", [track(1)])))

    monkeypatch.setattr(mc.requests, "get", fake_get)
    connected = _config({"metasync.node_url": "http://metasync:8790/", "metasync.local_api_key": "mn_local_x"})
    result = mc.refresh_from_node(db, connected, identify)
    assert result == {"status": "completed", "stored": 1, "removed": 0}
    assert calls == [("http://metasync:8790/local/charts", {"Authorization": "Bearer mn_local_x", "Accept": "application/json"})]
    assert [c["key"] for c in mc.list_charts(db, 1)] == ["trending"]


def test_the_daily_pull_without_metasync_is_a_quiet_skip(db, monkeypatch):
    monkeypatch.setattr(mc.requests, "get", lambda *a, **k: pytest.fail("must not call out"))
    assert mc.refresh_from_node(db, _config({}), Identify())["status"] == "skipped"


@pytest.mark.parametrize("status,needle", [(401, "rejected"), (404, "too old"), (500, "HTTP 500")])
def test_the_daily_pull_says_what_went_wrong(db, monkeypatch, status, needle):
    monkeypatch.setattr(mc.requests, "get", lambda *a, **k: _Resp(status, {}))
    connected = _config({"metasync.node_url": "http://m", "metasync.local_api_key": "k"})
    result = mc.refresh_from_node(db, connected, Identify())
    assert result["status"] == "error" and needle in result["error"]


def test_the_automation_is_registered_and_scheduled_daily():
    from core.automation.blocks import ACTIONS
    from core.automation_engine import SYSTEM_AUTOMATIONS
    spec = next(s for s in SYSTEM_AUTOMATIONS if s["action_type"] == "metasync_refresh_charts")
    assert spec["trigger_config"] == {"interval": 24, "unit": "hours"}
    assert any(a["type"] == "metasync_refresh_charts" for a in ACTIONS)


def test_a_generated_cover_on_your_copy_shows_on_the_chart_card(db):
    """reported oct 25: generate cover art on a metasync chart saved fine, but
    the dashboard / discover chart card kept drawing the network's chart art,
    because list_charts never looked at the profile's own copy."""
    mc.store_charts(db, handoff(("trending", "Trending", [track(1), track(2)])), Identify())
    playlist_id = mc.open_chart(db, "trending", 1, Identify())
    before = mc.list_charts(db, 1)[0]["image_url"]
    assert db.set_mirrored_playlist_custom_cover(playlist_id, "/api/user-playlists/9/cover?v=1")
    assert mc.list_charts(db, 1)[0]["image_url"] == "/api/user-playlists/9/cover?v=1"
    # someone else's view of the chart, and the next day's handoff, are untouched
    assert mc.list_charts(db, 2)[0]["image_url"] == before
    mc.store_charts(db, handoff(("trending", "Trending", [track(1), track(3)])), Identify())
    assert mc.list_charts(db, 1)[0]["image_url"] == "/api/user-playlists/9/cover?v=1"
