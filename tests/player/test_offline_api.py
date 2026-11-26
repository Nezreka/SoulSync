"""player downloads (api/player_offline.py, scrap/DOWNLOADS_CONTRACT.md).

the library lookups (offline_catalog.expand / describe, playable.resolve)
and ffmpeg are stubbed: these hold the routes' own rules. who may, whose
devices, the conversion queue, the cache, wanted and offline sync.
"""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask, g

import api.player_offline as api
from core.player import downloads
from core.player import offline_catalog as C
from core.player import offline_jobs as J
from core.player import offline_store as S
from core.player import offline_wanted as W
from core.player.playable import Refused, Source


@pytest.fixture
def db(tmp_path, monkeypatch):
    from database.player_database import PlayerDatabase
    J._reset_for_tests()
    monkeypatch.setattr(J, "cache_dir", lambda: tmp_path / "offline")
    pdb = PlayerDatabase(str(tmp_path / "player.db"))
    monkeypatch.setattr(api, "player_db", lambda: pdb)
    yield pdb
    J._reset_for_tests()


def _client(db, *, profile_id, admin=False, paired=None):
    app = Flask(__name__)
    app.secret_key = "test"

    @app.before_request
    def _stamp():
        g.profile_id = profile_id
        g.is_admin = admin
        if paired is not None:
            g.device_auth = True
            g.device = {"id": paired, "kind": "video"}

    app.register_blueprint(api.create_blueprint())
    return app.test_client()


# ── a small library ──────────────────────────────────────────────────────

def _ep(n, season=2, show=7, height=2160, size=4_000_000_000, secs=3000):
    return {"item_type": "episode", "item_id": str(100 + n), "title": f"S{season} E{n} · Ep {n}",
            "subtitle": "Severance", "artwork": f"/api/video/poster/episode/{100 + n}?w=500",
            "duration_s": secs, "size_bytes": size,
            "source": {"container": "mkv", "video_codec": "hevc", "height": height, "hdr": "HDR10",
                       "audio_codec": "eac3", "bitrate_kbps": 10000},
            "group": {"type": "season", "id": f"{show}:{season}", "title": "Severance",
                      "subtitle": f"Season {season}", "artwork": None, "show_id": str(show)},
            "_has_file": True}


def _track(n, kbps=1000):
    return {"item_type": "track", "item_id": str(n), "title": f"Song {n}", "subtitle": "Band · Album",
            "artwork": None, "duration_s": 200, "size_bytes": 30_000_000,
            "source": {"container": "flac", "video_codec": None, "height": None, "hdr": None,
                       "audio_codec": "flac", "bitrate_kbps": kbps},
            "group": {"type": "album", "id": "9", "title": "Album", "subtitle": "Band", "artwork": None},
            "_has_file": True}


@pytest.fixture
def library(monkeypatch):
    lib = {
        ("season", "7:2"): [_ep(n) for n in range(1, 7)],
        ("show", "7"): [_ep(n) for n in range(1, 7)],
        ("episode", "103"): [_ep(3)],
        ("playlist", "user:4"): [_track(1), _track(2), _track(3)],
        ("album", "9"): [_track(1), _track(2)],
    }

    def expand(gtype, gid, count=5):
        items = lib.get((gtype, str(gid)))
        if items is None:
            raise Refused(404, "not_found", "nope")
        return {"title": f"{gtype} {gid}", "subtitle": "", "artwork": None}, [dict(i) for i in items]

    def describe(keys):
        out = {}
        for items in lib.values():
            for i in items:
                if (i["item_type"], i["item_id"]) in [tuple(k) for k in keys]:
                    out[C.key(i["item_type"], i["item_id"])] = dict(i)
        return out

    monkeypatch.setattr(C, "expand", expand)
    monkeypatch.setattr(C, "describe", describe)
    watched: set = set()
    monkeypatch.setattr(W, "_video_watched", lambda kind, ids: {i for i in ids if (kind, i) in watched})
    return {"lib": lib, "watched": watched}


def _report(c, device="pixel", items=(), **extra):
    return c.post("/api/player/offline/devices/report", json={
        "device_id": device, "device_name": "Pixel 8", "platform": "android", "free_bytes": 6_000_000_000,
        "limit_bytes": 25_000_000_000, "used_bytes": 18_000_000_000, "wifi_only": True,
        "items": [{"item_type": t, "item_id": i, "quality": "phone", "size_bytes": 700_000_000,
                   "state": s, "progress": 1 if s == "downloaded" else 0.5} for t, i, s in items], **extra})


# ── who may ──────────────────────────────────────────────────────────────

def test_switched_off_member_is_refused_and_an_admin_is_not(db, library):
    downloads.set_permission(db, 2, False, 0)
    downloads.set_permission(db, 1, False, 0)
    member = _client(db, profile_id=2)
    _report(member)
    for r in (member.get("/api/player/offline/plan?type=season&id=7:2"),
              member.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103"}),
              member.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "add",
                                                               "type": "season", "id": "7:2", "quality": "phone"}),
              member.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                                              "quality": "phone", "keep_next": 3})):
        assert r.status_code == 403 and r.get_json()["error"] == "downloads_disabled"
    # wanted adds nothing for them, but still answers
    assert member.get("/api/player/offline/devices/pixel/wanted").get_json()["add"] == []
    admin = _client(db, profile_id=1, admin=True)
    assert admin.get("/api/player/offline/plan?type=season&id=7:2").status_code == 200


def test_signed_out_is_401(db, library):
    c = _client(db, profile_id=None)
    assert c.get("/api/player/offline/devices").status_code == 401
    assert c.get("/api/player/offline/plan?type=season&id=7:2").status_code == 401


def test_reads_never_make_tables(db, library):
    c = _client(db, profile_id=2)
    assert c.get("/api/player/offline/devices").get_json() == {"success": True, "devices": []}
    assert c.get("/api/player/offline/settings").status_code == 200
    assert c.get("/api/player/offline/prepare/nothing").status_code == 404
    assert c.get("/api/player/offline/devices/pixel/wanted").status_code == 404
    with sqlite3.connect(db.database_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {t for t in tables if t.startswith("player_offline") or t.startswith("player_download")}


# ── whose devices ────────────────────────────────────────────────────────

def test_one_profile_never_sees_or_changes_anothers_devices(db, library):
    mine = _client(db, profile_id=1)
    other = _client(db, profile_id=2)
    _report(mine, items=[("episode", "101", "downloaded")])
    rule = mine.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                                         "quality": "phone", "keep_next": 3}).get_json()["rule"]
    assert [d["device_id"] for d in mine.get("/api/player/offline/devices").get_json()["devices"]] == ["pixel"]
    assert other.get("/api/player/offline/devices").get_json()["devices"] == []
    assert other.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "remove",
                                                          "type": "episode", "id": "101"}).status_code == 404
    assert other.get("/api/player/offline/devices/pixel/wanted").status_code == 404
    assert other.delete(f"/api/player/offline/rules/{rule['rule_id']}").status_code == 404
    assert other.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                                         "keep_next": 1}).status_code == 404
    # the same device id on another profile is that profile's own
    _report(other, items=[])
    assert len(mine.get("/api/player/offline/devices").get_json()["devices"][0]["items"]) == 1
    assert mine.delete(f"/api/player/offline/rules/{rule['rule_id']}").get_json()["removed"] is True


def test_a_paired_device_id_cant_be_taken_by_another_paired_device(db, library):
    first = _client(db, profile_id=1, paired=11)
    assert _report(first).status_code == 200
    second = _client(db, profile_id=1, paired=12)
    r = _report(second)
    assert r.status_code == 403 and r.get_json()["error"] == "not_your_device"
    # the browser session (no token) may still report for it
    assert _report(_client(db, profile_id=1)).status_code == 200


def test_report_replaces_the_device_and_keeps_the_old_log_in_step(db, library):
    c = _client(db, profile_id=1)
    _report(c, items=[("episode", "101", "downloaded"), ("episode", "102", "downloading")])
    dev = c.get("/api/player/offline/devices").get_json()["devices"][0]
    assert dev["device_name"] == "Pixel 8" and dev["limit_bytes"] == 25_000_000_000
    by = {i["item_id"]: i for i in dev["items"]}
    assert by["101"]["title"] == "S2 E1 · Ep 1" and by["101"]["group"]["id"] == "7:2"
    assert [r["item_id"] for r in downloads.list_for_profile(db, 1)] == ["101"]
    _report(c, items=[("episode", "102", "downloaded")])
    dev = c.get("/api/player/offline/devices").get_json()["devices"][0]
    assert [i["item_id"] for i in dev["items"]] == ["102"]
    assert [r["item_id"] for r in downloads.list_for_profile(db, 1)] == ["102"]
    assert _report(c, device="../etc").status_code == 400


# ── plan ─────────────────────────────────────────────────────────────────

def test_plan_sizes_and_never_upscales(db, library):
    c = _client(db, profile_id=1)
    _report(c, items=[("episode", "101", "downloaded")])
    out = c.get("/api/player/offline/plan?type=season&id=7:2&device=pixel").get_json()
    assert out["kind"] == "video" and len(out["items"]) == 6
    assert [q["id"] for q in out["qualities"]] == ["original", "high", "phone", "small"]
    orig = out["qualities"][0]
    assert orig["total_bytes"] == 6 * 4_000_000_000 and "4K HDR" in orig["sub"]
    phone = next(q for q in out["qualities"] if q["id"] == "phone")
    assert phone["per_item_bytes"]["episode:101"] == int(3000 * (3_500_000 + 160_000) / 8)
    assert out["items"][0]["on_device"] is True and out["items"][1]["on_device"] is False
    assert out["free_bytes"] == 6_000_000_000
    library["lib"][("season", "7:2")] = [_ep(1, height=720)]
    out = c.get("/api/player/offline/plan?type=season&id=7:2").get_json()
    assert [q["id"] for q in out["qualities"]] == ["original", "phone", "small"]
    # audio: bitrates at or above the source's aren't offered
    library["lib"][("album", "9")] = [_track(1, kbps=256)]
    out = c.get("/api/player/offline/plan?type=album&id=9").get_json()
    assert [q["id"] for q in out["qualities"]] == ["original", "128"]


def test_plan_with_conversions_off_offers_the_original_only(db, library):
    S.put_settings(db, {"allow_conversion_for_members": False})
    member = _client(db, profile_id=2)
    out = member.get("/api/player/offline/plan?type=season&id=7:2").get_json()
    assert [q["id"] for q in out["qualities"]] == ["original"] and out["conversion_off"] is True
    admin = _client(db, profile_id=1, admin=True)
    assert len(admin.get("/api/player/offline/plan?type=season&id=7:2").get_json()["qualities"]) == 4


# ── prepare, the queue, the file ─────────────────────────────────────────

@pytest.fixture
def media(tmp_path, monkeypatch, library):
    """episode 103 is a real little file; ffmpeg writes the output"""
    src = tmp_path / "Severance S02E03.mkv"
    src.write_bytes(b"x" * 10_000)
    seen = {"resolve": [], "cmds": []}

    def resolve(item_type, item_id, part=None):
        seen["resolve"].append((item_type, item_id))
        if not str(item_id).isdigit():
            raise Refused(400, "bad_id", "That isn't a library id.")
        return Source("file", "video/x-matroska", path=str(src), duration=3000.0,
                      extra={"file_id": 5}, private={"video_codec": "hevc", "audio_codec": "eac3",
                                                     "height": 2160, "dynamic_range": None})

    import core.player.playable as P
    monkeypatch.setattr(P, "resolve", resolve)
    monkeypatch.setattr(J, "_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(J, "_text_subs", lambda source: [0])
    from core.player import transcode as T
    monkeypatch.setattr(T, "encoder", lambda ffmpeg: ("h264_nvenc", "NVIDIA GPU", [], ""))
    monkeypatch.setattr(T, "has_filter", lambda ffmpeg, name: False)

    def fake_ffmpeg(cmd, duration, progress):
        seen["cmds"].append(cmd)
        progress(0.5)
        with open(cmd[-1], "wb") as f:
            f.write(b"y" * 4000)

    monkeypatch.setattr(J, "_ffmpeg", fake_ffmpeg)
    return {"src": src, "seen": seen}


def test_original_is_ready_and_served_with_range_etag_and_a_clean_name(db, media):
    c = _client(db, profile_id=1)
    # a path from the client is never used: only the item is
    out = c.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                      "quality": "original", "path": "/etc/passwd"}).get_json()
    assert out["status"] == "ready" and out["url"] == f"/api/player/offline/file/{out['id']}"
    assert out["filename"] == "Severance - S2 E3 - Ep 3 [Original].mkv"
    r = c.get(out["url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100
    assert r.headers.get("ETag") and r.headers["Content-Range"] == "bytes 0-99/10000"
    assert "attachment" in r.headers["Content-Disposition"] and "Severance" in r.headers["Content-Disposition"]
    full = c.get(out["url"])
    assert full.status_code == 200 and full.data == media["src"].read_bytes()
    assert c.get("/api/player/offline/file/nope").status_code == 404
    bad = c.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "../../etc/passwd"})
    assert bad.status_code == 400


def test_the_file_route_checks_the_caller_may_play_it(db, media, monkeypatch):
    c = _client(db, profile_id=1)
    out = c.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103"}).get_json()
    import core.player.playable as P

    def refuse(*a, **k):
        raise Refused(403, "restricted", "That isn't available on this profile.")

    monkeypatch.setattr(P, "resolve", refuse)
    kid = _client(db, profile_id=3)
    assert kid.get(out["url"]).status_code == 403


def test_a_copy_is_made_once_and_shared(db, media, monkeypatch):
    ran = []
    monkeypatch.setattr(J, "_spawn", lambda fn: ran.append(fn))
    a = _client(db, profile_id=1)
    b = _client(db, profile_id=2)
    first = a.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                        "quality": "phone"}).get_json()
    second = b.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                         "quality": "phone"}).get_json()
    assert first["id"] == second["id"] and first["status"] == "queued" and len(ran) == 1
    ran.pop()()
    done = b.get(f"/api/player/offline/prepare/{first['id']}").get_json()
    assert done["status"] == "ready" and done["size_bytes"] == 4000 and done["progress"] == 1
    assert done["filename"] == "Severance - S2 E3 - Ep 3 [720p].mp4"
    # asked again: the ready copy, no new job
    again = a.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                        "quality": "phone"}).get_json()
    assert again["status"] == "ready" and ran == [] and len(media["seen"]["cmds"]) == 1
    cmd = media["seen"]["cmds"][0]
    assert cmd[cmd.index("-map_chapters") + 1] == "0" and "mov_text" in cmd and "0:s:0" in cmd
    assert cmd[cmd.index("-i") + 1] == str(media["src"])
    # the player transcoder's encoder (a GPU when one works), at 720p
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc" and any("min(720,ih)" in a for a in cmd)
    got = a.get(done["url"])
    assert got.status_code == 200 and got.data == b"y" * 4000 and got.headers["Content-Type"] == "video/mp4"


def test_the_queue_keeps_to_max_conversions(db, media, monkeypatch):
    ran = []
    monkeypatch.setattr(J, "_spawn", lambda fn: ran.append(fn))
    c = _client(db, profile_id=1, admin=True)
    for q in ("high", "phone", "small"):
        assert c.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                           "quality": q}).get_json()["status"] == "queued"
    assert len(J.running()) == 1 and len(J.waiting()) == 2 and len(ran) == 1
    assert c.put("/api/player/offline/settings", json={"max_conversions": 2}).status_code == 200
    assert len(J.running()) == 2 and len(ran) == 2
    ran.pop(0)()
    # one finished: the last one starts
    assert len(J.running()) == 2 and J.waiting() == []


def test_members_cant_ask_for_copies_when_the_admin_says_no(db, media, monkeypatch):
    monkeypatch.setattr(J, "_spawn", lambda fn: fn())
    S.put_settings(db, {"allow_conversion_for_members": False})
    member = _client(db, profile_id=2)
    r = member.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                         "quality": "phone"})
    assert r.status_code == 403 and r.get_json()["error"] == "conversion_off"
    assert member.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                            "quality": "original"}).status_code == 200
    admin = _client(db, profile_id=1, admin=True)
    made = admin.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                           "quality": "phone"}).get_json()
    assert made["status"] == "ready"
    # a copy that's already made is the member's too
    assert member.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                            "quality": "phone"}).get_json()["status"] == "ready"


def test_a_failed_copy_says_so(db, media, monkeypatch):
    monkeypatch.setattr(J, "_spawn", lambda fn: fn())

    def broken(cmd, duration, progress):
        raise RuntimeError("Invalid data")

    monkeypatch.setattr(J, "_ffmpeg", broken)
    c = _client(db, profile_id=1)
    out = c.post("/api/player/offline/prepare", json={"item_type": "episode", "item_id": "103",
                                                      "quality": "small"}).get_json()
    assert out["status"] == "failed" and out["error"] and out["url"] is None


def test_the_cache_drops_the_oldest_copies_first(db, tmp_path):
    files = []
    for n, size in enumerate((600, 500, 400)):
        p = tmp_path / f"c{n}.mp4"
        p.write_bytes(b"z")
        files.append(p)
        S.put_prepared(db, {"id": f"c{n}", "file_key": f"k{n}", "quality": "phone", "item_type": "movie",
                            "item_id": str(n), "status": "ready", "size_bytes": size * 1024 ** 2,
                            "path": str(p)})
        S.update_prepared(db, f"c{n}", last_used=1000 + n)
    S.put_settings(db, {"cache_gb": 1})
    assert J.cache_used(db) == 1500 * 1024 ** 2
    assert J.evict(db) == 1
    assert not files[0].exists() and files[1].exists() and S.prepared(db, "c0") is None
    assert J.cache_used(db) == 900 * 1024 ** 2


# ── wishes, rules, wanted ────────────────────────────────────────────────

def test_a_wish_adds_until_its_on_the_device_then_is_done(db, library):
    c = _client(db, profile_id=1)
    _report(c, items=[("episode", "101", "downloaded")])
    out = c.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "add", "type": "season",
                                                     "id": "7:2", "quality": "phone"}).get_json()
    assert out["wishes"][0]["title"] == "season 7:2" and out["wishes"][0]["action"] == "add"
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert [a["item_id"] for a in want["add"]] == ["102", "103", "104", "105", "106"]
    assert want["add"][0]["reason"] == "wish" and want["add"][0]["quality"] == "phone"
    _report(c, items=[("episode", str(100 + n), "downloaded") for n in range(1, 7)])
    assert c.get("/api/player/offline/devices/pixel/wanted").get_json() == {"success": True, "add": [], "remove": []}
    assert c.get("/api/player/offline/devices").get_json()["devices"][0]["wishes"] == []
    # one removed later stays removed
    c.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "remove", "type": "episode",
                                               "id": "103"})
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert want["remove"] == [{"item_type": "episode", "item_id": "103", "reason": "wish"}] and want["add"] == []
    _report(c, items=[("episode", str(100 + n), "downloaded") for n in (1, 2, 4, 5, 6)])
    assert c.get("/api/player/offline/devices/pixel/wanted").get_json()["add"] == []


def test_removing_a_pending_add_just_cancels_it(db, library):
    c = _client(db, profile_id=1)
    _report(c)
    c.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "add", "type": "episode",
                                               "id": "103", "quality": "original"})
    out = c.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "remove", "type": "episode",
                                                     "id": "103"}).get_json()
    assert out["wishes"] == []


def test_show_rule_keeps_the_next_unwatched_and_drops_watched(db, library):
    c = _client(db, profile_id=1)
    _report(c, items=[("episode", "101", "downloaded"), ("episode", "102", "downloaded")])
    rule = c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                                     "quality": "phone", "keep_next": 3,
                                                     "delete_watched": True}).get_json()["rule"]
    assert rule["keep_next"] == 3 and rule["delete_watched"] is True and rule["type"] == "show"
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    # nothing watched: episodes 1 and 2 are there, 3 is the third
    assert [a["item_id"] for a in want["add"]] == ["103"] and want["remove"] == []
    library["watched"].update({("episode", 101), ("episode", 102)})
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert [a["item_id"] for a in want["add"]] == ["103", "104", "105"]
    assert sorted(r["item_id"] for r in want["remove"]) == ["101", "102"]
    assert {r["reason"] for r in want["remove"]} == {"watched"}
    # keep the watched ones: no removes
    c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                              "quality": "phone", "keep_next": 1, "delete_watched": False})
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert [a["item_id"] for a in want["add"]] == ["103"] and want["remove"] == []
    assert len(c.get("/api/player/offline/devices").get_json()["devices"][0]["rules"]) == 1
    assert c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                                     "keep_next": 4}).status_code == 400


def test_player_progress_counts_as_watched_too(db, library):
    from core.player import progress
    c = _client(db, profile_id=1)
    _report(c)
    c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7",
                                              "quality": "phone", "keep_next": 1})
    progress.finish(db, 1, "episode", "103", 3000)
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert [a["item_id"] for a in want["add"]] == ["104"]


def test_playlist_rule_follows_the_playlist(db, library):
    c = _client(db, profile_id=1)
    _report(c, items=[("track", "1", "downloaded"), ("track", "2", "downloaded")])
    c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "playlist", "id": "user:4",
                                              "quality": "256", "follow": True})
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert [a["item_id"] for a in want["add"]] == ["3"] and want["add"][0]["quality"] == "256"
    _report(c, items=[("track", "1", "downloaded"), ("track", "2", "downloaded"), ("track", "3", "downloaded")])
    # song 2 leaves the playlist: it goes; song 1 stays
    library["lib"][("playlist", "user:4")] = [_track(1), _track(3), _track(5)]
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert [a["item_id"] for a in want["add"]] == ["5"]
    assert want["remove"] == [{"item_type": "track", "item_id": "2", "reason": "rule"}]
    # the device lists the playlist's songs under the playlist
    dev = c.get("/api/player/offline/devices").get_json()["devices"][0]
    assert {i["group"]["type"] for i in dev["items"] if i["item_id"] in ("1", "3")} == {"playlist"}


def test_a_song_another_rule_wants_isnt_removed(db, library):
    c = _client(db, profile_id=1)
    _report(c, items=[("track", "1", "downloaded"), ("track", "2", "downloaded")])
    c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "playlist", "id": "user:4",
                                              "quality": "original", "follow": True})
    c.get("/api/player/offline/devices/pixel/wanted")
    c.post("/api/player/offline/wishes", json={"device_id": "pixel", "action": "add", "type": "album",
                                               "id": "9", "quality": "original"})
    library["lib"][("playlist", "user:4")] = [_track(1)]
    want = c.get("/api/player/offline/devices/pixel/wanted").get_json()
    assert want["remove"] == []


# ── offline sync ─────────────────────────────────────────────────────────

def test_sync_applies_only_newer_progress_and_counts_plays_once(db, library, monkeypatch):
    from core.player import progress
    from core.player import watched
    marked = []
    monkeypatch.setattr(watched, "mark", lambda t, i, spawn=None: marked.append((t, i)) or True)
    recorded = []
    monkeypatch.setattr(api, "_record_track_play", lambda pid, tid, at, d: recorded.append((pid, tid)))
    c = _client(db, profile_id=1)
    progress.save(db, 1, "episode", "103", 900, 3000, now=1_800_000_000)   # 2027-01-15
    events = [
        {"kind": "progress", "item_type": "episode", "item_id": "103", "position_s": 100, "duration_s": 3000,
         "at": "2026-12-01T10:00:00Z"},     # older than the server's: skipped
        {"kind": "progress", "item_type": "movie", "item_id": "5", "position_s": 1200, "duration_s": 6000,
         "at": "2026-11-20T10:00:00Z"},
        {"kind": "watched", "item_type": "episode", "item_id": "104", "duration_s": 3000,
         "at": "2026-11-21T10:00:00Z"},
        {"kind": "progress", "item_type": "podcast_episode", "item_id": "12:guid-abc", "position_s": 60,
         "at": "2026-11-21T10:00:00Z"},
        {"kind": "play", "item_type": "track", "item_id": "1", "at": "2026-11-21T09:00:00Z"},
        {"kind": "play", "item_type": "track", "item_id": "1", "at": "2026-11-21T09:00:00Z"},
        {"kind": "play", "item_type": "track", "item_id": "1", "at": "2026-11-21T09:05:00Z"},
        {"kind": "nope", "item_type": "track", "item_id": "1", "at": "2026-11-21T09:05:00Z"},
    ]
    out = c.post("/api/player/offline/sync", json={"device_id": "pixel", "events": events}).get_json()
    assert out == {"success": True, "applied": 3, "skipped": 3, "plays": 2}
    assert progress.get(db, 1, "episode", "103")["position_seconds"] == 900
    assert progress.get(db, 1, "movie", "5")["position_seconds"] == 1200
    assert progress.get(db, 1, "episode", "104")["finished"] is True
    # a podcast episode lands on the row the players use (no podcast prefix)
    assert progress.get(db, 1, "podcast_episode", "guid-abc")["position_seconds"] == 60
    assert marked == [("episode", "104")] and recorded == [(1, "1"), (1, "1")]
    # sent again (a retry after a dropped connection): nothing twice
    again = c.post("/api/player/offline/sync", json={"device_id": "pixel", "events": events}).get_json()
    assert again["plays"] == 0 and again["applied"] == 0
    assert c.post("/api/player/offline/sync", json={"device_id": "pixel", "events": "x"}).status_code == 400


# ── settings ─────────────────────────────────────────────────────────────

def test_settings_are_the_admins(db):
    member = _client(db, profile_id=2)
    out = member.get("/api/player/offline/settings").get_json()
    assert out["settings"] == {"allow_conversion_for_members": True, "max_conversions": 1, "cache_gb": 50}
    assert out["is_admin"] is False and out["can_convert"] is True
    assert member.put("/api/player/offline/settings", json={"cache_gb": 5}).status_code == 403
    admin = _client(db, profile_id=1, admin=True)
    r = admin.put("/api/player/offline/settings", json={"cache_gb": 5, "allow_conversion_for_members": False})
    assert r.get_json()["settings"]["cache_gb"] == 5
    assert member.get("/api/player/offline/settings").get_json()["can_convert"] is False
    assert admin.put("/api/player/offline/settings", json={"max_conversions": 99}).status_code == 400
    assert admin.put("/api/player/offline/settings", json={"bogus": 1}).status_code == 400


def test_a_deleted_profile_takes_its_devices_with_it(db, library):
    c = _client(db, profile_id=2)
    _report(c, items=[("episode", "101", "downloaded")])
    c.post("/api/player/offline/rules", json={"device_id": "pixel", "type": "show", "id": "7", "keep_next": 1})
    keep = _client(db, profile_id=1)
    _report(keep)
    db.delete_profile_data(2)
    assert c.get("/api/player/offline/devices").get_json()["devices"] == []
    assert S.rules(db, 2) == [] and len(keep.get("/api/player/offline/devices").get_json()["devices"]) == 1
