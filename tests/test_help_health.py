"""Health for the help sheet: each check fires on its condition and stays
quiet without it, a dead or slow service turns into one "couldn't check" line
without holding up the list, and only admins get any of it."""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest
from flask import Flask

from core.help import health

SEV = set(health.SEVERITIES)
FIX_KINDS = {"post", "settings", "page", "external", "checklist", "logs", "recheck"}


@pytest.fixture(autouse=True)
def _clean():
    health.reset()
    yield
    health.reset()


def _config(values: dict):
    def get(key, default=None):
        node = values
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return values.get(key, default)
        return node
    return get


def _folders(tmp_path, **extra):
    dl, tr = tmp_path / "downloads", tmp_path / "Transfer"
    dl.mkdir(exist_ok=True)
    tr.mkdir(exist_ok=True)
    cfg = {"soulseek": {"download_path": str(dl), "transfer_path": str(tr)},
           "active_media_server": "jellyfin",
           "jellyfin": {"base_url": "http://jellyfin:8096", "api_key": "k"},
           "download_source": {"mode": "soulseek"}}
    cfg.update(extra)
    return cfg


def assert_shape(f: dict):
    assert set(f) <= {"id", "severity", "area", "title", "detail", "fix", "docs"}
    assert f["id"] and f["area"] and f["title"]
    assert f["severity"] in SEV
    for text in (f["title"], f.get("detail", "")):
        assert "—" not in text and "–" not in text, text
    for fx in f.get("fix") or []:
        assert fx["kind"] in FIX_KINDS and fx["label"]
        if fx["kind"] == "post":
            assert fx["url"].startswith("/api/")
        if fx["kind"] == "settings":
            assert fx["tab"]
        if fx["kind"] == "page":
            assert fx["path"].startswith("/")


def _by_id(findings):
    return {f["id"]: f for f in findings}


# ---------------------------------------------------------------- services
def test_media_server_down_is_an_error_up_is_ok(tmp_path):
    health.configure(config_get=_config(_folders(tmp_path)), media_server_probe=lambda: False)
    [f] = health.check_media_server()
    assert_shape(f)
    assert f["severity"] == "error" and f["title"] == "Jellyfin isn't answering"
    assert "http://jellyfin:8096" in f["detail"]
    assert f["fix"][0] == {"label": "Test again", "kind": "post", "url": "/api/test-connection",
                           "recheck": True, "body": {"service": "server"}}
    health.configure(media_server_probe=lambda: True)
    [f] = health.check_media_server()
    assert f["severity"] == "ok" and f["title"] == "Jellyfin connected"


def test_media_server_not_set_up_is_info_and_never_probed(tmp_path):
    called = []
    cfg = _folders(tmp_path)
    cfg["jellyfin"] = {"base_url": "", "api_key": ""}
    health.configure(config_get=_config(cfg), media_server_probe=lambda: called.append(1))
    [f] = health.check_media_server()
    assert f["severity"] == "info" and "isn't set up" in f["title"]
    assert called == []


def test_spotify_rate_limit_warns_only_while_limited(tmp_path):
    health.configure(config_get=_config(_folders(tmp_path)),
                     spotify_status=lambda: {"rate_limited": True, "rate_limit": {"remaining_seconds": 1380}})
    found = _by_id(health.check_metadata())
    f = found["services.spotify_rate_limit"]
    assert_shape(f)
    assert f["severity"] == "warning" and "23 more minutes" in f["detail"]
    health.configure(spotify_status=lambda: {"rate_limited": False})
    assert "services.spotify_rate_limit" not in _by_id(health.check_metadata())


@pytest.mark.parametrize("state,only,sev", [
    (False, True, "error"), (False, False, "warning"), (True, True, "ok"),
    ("unconfigured", False, "warning"), ("unconfigured", True, "error"),
])
def test_download_source_states(tmp_path, state, only, sev):
    health.configure(config_get=_config(_folders(tmp_path)), download_probe=lambda s: state)
    [f] = health.check_download_source("soulseek", only=only)
    assert_shape(f)
    assert f["severity"] == sev and f["id"] == "services.download.soulseek"


def test_a_missing_client_is_left_to_the_failures_check(tmp_path):
    health.configure(config_get=_config(_folders(tmp_path)), download_probe=lambda s: "missing")
    assert health.check_download_source("hifi", only=False) == []
    health.configure(download_failures=lambda: ["HiFi"])
    [f] = health.check_download_clients()
    assert f["severity"] == "warning" and "HiFi" in f["detail"]
    health.configure(download_failures=lambda: [])
    assert health.check_download_clients() == []
    health.configure(download_failures=lambda: ["ALL (orchestrator failed to initialize)"])
    assert health.check_download_clients()[0]["severity"] == "error"


def test_hybrid_chain_gets_one_check_per_source(tmp_path):
    cfg = _folders(tmp_path)
    health.configure(config_get=_config(cfg), download_probe=lambda s: s != "soulseek",
                     download_sources=lambda: ["hifi", "youtube", "soulseek"])
    ids = [p[0] for p in health._plan()]
    assert {"download.hifi", "download.youtube", "download.soulseek"} <= set(ids)
    result = health.collect([p for p in health._plan() if p[0].startswith("download.")])
    found = _by_id(result["findings"])
    assert found["services.download.soulseek"]["severity"] == "warning"   # others still work
    assert found["services.download.hifi"]["severity"] == "ok"


# ---------------------------------------------------------------- paths
def test_paths_ok_missing_and_not_writable(tmp_path):
    cfg = _folders(tmp_path)
    health.configure(config_get=_config(cfg), is_docker=lambda: False)
    found = _by_id(health.check_paths())
    assert found["paths.download"]["severity"] == "ok"
    assert found["paths.transfer"]["severity"] == "ok"

    cfg["soulseek"]["download_path"] = str(tmp_path / "nope" / "deeper")
    health.configure(writable=lambda p: os.path.basename(p) != "nope" and "nope" not in p
                     and not p.endswith(str(tmp_path.name)))
    found = _by_id(health.check_paths())
    f = found["paths.download"]
    assert_shape(f)
    assert f["severity"] == "error" and f["title"] == "Download folder is missing"

    cfg["soulseek"]["download_path"] = str(tmp_path / "downloads")
    health.configure(writable=lambda p: not p.endswith("downloads"))
    f = _by_id(health.check_paths())["paths.download"]
    assert f["severity"] == "error" and f["title"] == "Download folder isn't writable"
    assert f["fix"][0]["tab"] == "downloads"


@pytest.mark.skipif(os.name == "nt", reason="a Windows path in docker is its own problem")
def test_docker_hint_on_a_folder_that_cant_be_written(tmp_path):
    cfg = _folders(tmp_path)
    health.configure(config_get=_config(cfg), is_docker=lambda: True, mounts=lambda: [],
                     writable=lambda p: not p.endswith("downloads"))
    f = _by_id(health.check_paths())["paths.download"]
    assert "PUID" in f["detail"] and f["docs"] == "gs-docker"


def test_download_inside_transfer_warns(tmp_path):
    cfg = _folders(tmp_path)
    inner = tmp_path / "Transfer" / "incoming"
    inner.mkdir()
    cfg["soulseek"]["download_path"] = str(inner)
    health.configure(config_get=_config(cfg), is_docker=lambda: False)
    found = _by_id(health.check_paths())
    f = found["paths.download.inside"]
    assert f["severity"] == "warning" and "inside the music library folder" in f["title"]


def test_library_folder_may_be_the_transfer_folder(tmp_path):
    cfg = _folders(tmp_path, library={"music_paths": [str(tmp_path / "Transfer")]})
    health.configure(config_get=_config(cfg), is_docker=lambda: False)
    found = _by_id(health.check_paths())
    assert found["paths.music_library_0"]["severity"] == "ok"
    assert found["paths.transfer"]["severity"] == "ok"


def test_path_mapping(tmp_path):
    cfg = _folders(tmp_path)
    health.configure(config_get=_config(cfg))
    assert health.check_path_mapping() == []          # none configured, nothing to say
    cfg["download_source"]["path_mappings"] = [{"from": "/data", "to": str(tmp_path / "missing")}]
    [f] = health.check_path_mapping()
    assert_shape(f)
    assert f["severity"] == "error" and "missing folder" in f["title"]
    cfg["download_source"]["path_mappings"] = [{"from": "/data", "to": ""}]
    assert health.check_path_mapping()[0]["title"].endswith("incomplete")
    cfg["download_source"]["path_mappings"] = [{"from": "/data", "to": str(tmp_path / "downloads")}]
    assert health.check_path_mapping()[0]["severity"] == "ok"
    health.configure(open_import_paths=lambda: ["/nowhere/at/all/x"])
    found = _by_id(health.check_path_mapping())
    assert found["paths.mapping.imports"]["severity"] == "warning"


# ---------------------------------------------------------------- disk
@pytest.mark.parametrize("free,floor,sev", [
    (0.5, 0, "error"), (1.5, 0, "warning"), (40.0, 50, "warning"), (500.0, 50, "ok"), (500.0, 0, "ok"),
])
def test_music_disk(tmp_path, monkeypatch, free, floor, sev):
    import core.video.disk_guard as dg
    monkeypatch.setattr(dg, "free_gb", lambda p: free)
    health.configure(config_get=_config(_folders(tmp_path)),
                     video_settings=lambda: {"min_free_disk_gb": floor})
    found = health.check_disk()
    assert len(found) == 1          # downloads and Transfer share a drive: one line
    assert_shape(found[0])
    assert found[0]["severity"] == sev
    if floor and sev == "warning":
        assert "50 GB minimum" in found[0]["detail"]


# ---------------------------------------------------------------- video
class _VideoDb:
    def __init__(self, settings):
        self.settings = settings

    def get_setting(self, key):
        return self.settings.get(key)


def test_video_checks_map_and_skip_when_unused(monkeypatch, tmp_path):
    import core.video.health as vh
    monkeypatch.setattr(vh, "collect", lambda db: {"status": "error", "checks": [
        {"id": "movies_path", "label": "Movie library", "status": "error",
         "detail": "/movies is unreachable — a drive or mount may be down"},
        {"id": "movies_path_space", "label": "Movie library", "status": "warning",
         "detail": "38.4 GB free — under your 50 GB minimum; new grabs are paused"},
        {"id": "youtube_health", "label": "YouTube", "status": "ok", "detail": "yt-dlp 2026.1"},
    ]})
    health.configure(video_db=lambda: _VideoDb({}))
    assert health.check_video() == []
    health.configure(video_db=lambda: _VideoDb({"movies_path": "/movies"}))
    found = _by_id(health.check_video())
    for f in found.values():
        assert_shape(f)
    assert found["video.movies_path"]["title"] == "Movie library folder is unreachable"
    assert found["video.movies_path"]["fix"][0] == {"label": "Open Library folders", "kind": "settings",
                                                    "tab": "library", "media": "video"}
    assert found["video.movies_path_space"]["title"] == "Movie library drive is nearly full"
    assert found["video.youtube_health"]["severity"] == "ok"


# ---------------------------------------------------------------- workers
class _Worker:
    def __init__(self, paused=False, alive=True):
        self.paused = paused
        self.running = True
        self.thread = SimpleNamespace(is_alive=lambda: alive)


def _svc(sid, worker, key=""):
    return SimpleNamespace(id=sid, display_name=sid.title(), config_paused_key=key,
                           get_worker=lambda: worker)


def test_workers(tmp_path):
    cfg = {"lastfm_enrichment_paused": True}
    health.configure(config_get=_config(cfg), enrichment_services=lambda: [
        _svc("lastfm", _Worker(paused=True), "lastfm_enrichment_paused"),
        _svc("deezer", _Worker(paused=True), "deezer_enrichment_paused"),   # auto-paused, not by a person
        _svc("genius", _Worker(alive=False)),
        _svc("musicbrainz", _Worker()),
        _svc("discogs", None),
    ])
    found = _by_id(health.check_workers())
    for f in found.values():
        assert_shape(f)
    assert found["workers.lastfm"]["severity"] == "warning"
    assert found["workers.lastfm"]["fix"][0]["url"] == "/api/enrichment/lastfm/resume"
    assert "workers.deezer" not in found
    assert found["workers.genius"]["title"] == "Genius enrichment stopped"
    assert found["workers.running"]["title"] == "2 enrichment workers running"


# ---------------------------------------------------------------- downloads, automations, setup, updates
def test_review_queue():
    health.configure(review_counts=lambda: {"quarantine": 3, "unverified": 1})
    [f] = health.check_review_queue()
    assert_shape(f)
    assert f["severity"] == "warning" and f["title"] == "4 downloads are waiting for you"
    health.configure(review_counts=lambda: {"quarantine": 0, "unverified": 0})
    assert health.check_review_queue()[0]["severity"] == "ok"


def test_automations_last_run_failed():
    health.configure(automations=lambda: [
        {"id": 1, "name": "Nightly scan", "enabled": 1, "last_error": "boom"},
        {"id": 2, "name": "Off one", "enabled": 0, "last_error": "old"},
        {"id": 3, "name": "Fine", "enabled": 1, "last_error": None},
    ])
    [f] = health.check_automations()
    assert_shape(f)
    assert f["severity"] == "warning" and "Nightly scan" in f["detail"] and "Off one" not in f["detail"]
    health.configure(automations=lambda: [{"id": 3, "name": "Fine", "enabled": 1, "last_error": None}])
    assert health.check_automations()[0]["severity"] == "ok"


def test_setup_items_until_done_or_dismissed():
    items = [{"id": "torrent", "title": "Add torrent and usenet", "description": "d", "action": "Set up", "done": False},
             {"id": "spotify", "title": "Connect Spotify", "description": "d", "action": "Connect", "done": True},
             {"id": "slskd_shares", "title": "Share", "description": "d", "action": "Open slskd",
              "done": False, "href": "http://slskd:5030/shares"}]
    health.configure(checklist=lambda: {"dismissed": False, "items": items})
    found = _by_id(health.check_setup())
    assert set(found) == {"setup.torrent", "setup.slskd_shares"}
    for f in found.values():
        assert_shape(f)
        assert f["severity"] == "info"
    assert found["setup.slskd_shares"]["fix"][0] == {"label": "Open slskd", "kind": "checklist",
                                                     "item": "slskd_shares", "href": "http://slskd:5030/shares"}
    health.configure(checklist=lambda: {"dismissed": True, "items": items})
    assert health.check_setup() == []


def test_updates():
    health.configure(update_info=lambda: {"update_available": True, "latest_version": "3.6.8",
                                          "current_version": "3.6.7", "severity": "routine",
                                          "release_url": "https://github.com/x/y/releases/tag/v3.6.8"})
    [f] = health.check_updates()
    assert_shape(f)
    assert f["severity"] == "info" and f["title"] == "SoulSync 3.6.8 is out"
    health.configure(update_info=lambda: {"update_available": True, "latest_version": "3.7.0",
                                          "current_version": "3.6.7", "severity": "critical"})
    assert health.check_updates()[0]["severity"] == "warning"
    health.configure(update_info=lambda: {"update_available": False, "current_version": "3.6.7"})
    assert health.check_updates()[0] == {"id": "updates.version", "severity": "ok", "area": "updates",
                                         "title": "3.6.7 is the latest", "detail": ""}


# ---------------------------------------------------------------- runner
def test_a_dead_service_reports_without_holding_the_list(tmp_path):
    release = threading.Event()
    calls = []

    def hang():
        calls.append(1)
        release.wait(30)
        return True

    health.configure(config_get=_config(_folders(tmp_path)), media_server_probe=hang,
                     review_counts=lambda: {"quarantine": 0, "unverified": 0})
    plan = [("media_server", "the media server", health.check_media_server, 0.5),
            ("review", "the review queue", health.check_review_queue, 0.5)]
    try:
        started = time.monotonic()
        result = health.collect(plan)
        assert time.monotonic() - started < 2.0
        found = _by_id(result["findings"])
        f = found["checks.media_server"]
        assert_shape(f)
        assert f["severity"] == "warning" and f["title"] == "Couldn't check the media server"
        assert found["downloads.review"]["severity"] == "ok"      # the rest still came back
        # a second run while the first probe is stuck does not start another
        health.collect(plan)
        assert calls == [1]
    finally:
        release.set()


def test_a_check_that_raises_reports_itself():
    def boom():
        raise RuntimeError("nope")
    health.configure(review_counts=lambda: {"quarantine": 0, "unverified": 0})
    result = health.collect([("x", "something", boom, 1.0),
                             ("review", "the review queue", health.check_review_queue, 1.0)])
    found = _by_id(result["findings"])
    assert found["checks.x"]["severity"] == "warning" and "RuntimeError" in found["checks.x"]["detail"]
    assert "downloads.review" in found


def test_full_run_shape_and_order(tmp_path, monkeypatch):
    import core.video.disk_guard as dg
    monkeypatch.setattr(dg, "free_gb", lambda p: 500.0)
    health.configure(config_get=_config(_folders(tmp_path)), media_server_probe=lambda: False,
                     spotify_status=lambda: {"rate_limited": False}, is_docker=lambda: False,
                     review_counts=lambda: {"quarantine": 2, "unverified": 0},
                     automations=lambda: [], checklist=lambda: {"items": []},
                     update_info=lambda: {"update_available": False, "current_version": "3.6.7"},
                     enrichment_services=lambda: [], video_db=lambda: _VideoDb({}),
                     download_probe=lambda s: True, download_sources=lambda: ["soulseek"],
                     download_failures=lambda: [])
    result = health.run_health()
    assert set(result) == {"status", "checked_at", "age_seconds", "took_ms", "counts", "summary", "findings"}
    assert result["status"] == "error"
    assert result["counts"]["error"] == 1 and result["counts"]["warning"] == 1
    assert result["summary"].startswith("One thing needs you. One is worth a look.")
    sevs = [f["severity"] for f in result["findings"]]
    assert sevs == sorted(sevs, key=health.SEVERITIES.index)
    assert len({f["id"] for f in result["findings"]}) == len(result["findings"])
    for f in result["findings"]:
        assert_shape(f)
    assert not any(f["area"] == "checks" for f in result["findings"]), result["findings"]


def test_cache_and_fresh(tmp_path):
    calls = []
    health.configure(review_counts=lambda: calls.append(1) or {"quarantine": 0, "unverified": 0})
    plan = [("review", "the review queue", health.check_review_queue, 1.0)]
    orig = health._plan
    health._plan = lambda: plan
    try:
        health.run_health()
        health.run_health()
        assert len(calls) == 1
        health._cache["at"] -= 5          # past the 1 s "just ran" window
        health.run_health(fresh=True)
        assert len(calls) == 2
        health._cache["at"] -= health.CACHE_SECONDS + 1
        health.run_health()
        assert len(calls) == 3
    finally:
        health._plan = orig


def test_summary_sentences():
    assert health.summary_sentence({"error": 2, "warning": 4, "ok": 14}) == \
        "Two things need you. Four are worth a look. Everything else, 14 checks, is running."
    assert health.summary_sentence({"error": 0, "warning": 1, "ok": 3}) == \
        "Nothing needs you. One is worth a look. Everything else, 3 checks, is running."
    assert health.summary_sentence({"ok": 9}) == "Everything is running. 9 checks passed."


def test_status_snapshot_never_runs_on_the_request():
    gate = threading.Event()
    health.configure(review_counts=lambda: gate.wait(5) and {"quarantine": 0, "unverified": 0})
    orig = health._plan
    health._plan = lambda: [("review", "the review queue", health.check_review_queue, 5.0)]
    try:
        started = time.monotonic()
        snap = health.status_snapshot()
        assert time.monotonic() - started < 0.5
        assert snap == {"errors": 0, "checked_at": None, "refreshing": True}
        gate.set()
        for _ in range(50):
            if health._cache.get("result"):
                break
            time.sleep(0.05)
        snap = health.status_snapshot()
        assert snap["checked_at"] and snap["errors"] == 0
    finally:
        health._plan = orig


# ---------------------------------------------------------------- routes
@pytest.fixture
def client(monkeypatch):
    import api.help as help_api
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(help_api.create_blueprint())
    health._cache.update({"result": {"status": "error", "checked_at": "2026-11-17T21:42:07Z",
                                     "counts": {"error": 2, "warning": 1, "info": 0, "ok": 3},
                                     "findings": [], "summary": "", "took_ms": 1, "age_seconds": 0},
                          "at": time.monotonic()})
    return app.test_client()


def test_routes_for_an_admin(client):
    data = client.get("/api/help/health").get_json()
    assert data["status"] == "error" and data["counts"]["error"] == 2
    assert client.get("/api/help/status").get_json() == {
        "errors": 2, "checked_at": "2026-11-17T21:42:07Z", "refreshing": False}


def test_a_member_gets_nothing(client, monkeypatch):
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    resp = client.get("/api/help/health")
    assert resp.status_code == 403 and resp.get_json()["error"] == "Admin access required"
    resp = client.get("/api/help/health?fresh=1")
    assert resp.status_code == 403
    resp = client.get("/api/help/status")
    assert resp.status_code == 200 and resp.get_json() == {}


# ---------------------------------------------------------------- the real app
def test_the_real_app_registers_every_help_route_admin_only(monkeypatch):
    import web_server
    rules = {r.rule for r in web_server.app.url_map.iter_rules() if r.rule.startswith("/api/help/")}
    assert rules == {"/api/help/status", "/api/help/health", "/api/help/bundle", "/api/help/logs",
                     "/api/help/logs/download", "/api/help/logs/download-all"}
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    client = web_server.app.test_client()
    for url in ("/api/help/health", "/api/help/bundle", "/api/help/logs",
                "/api/help/logs/download?name=app.log", "/api/help/logs/download-all"):
        assert client.get(url).status_code == 403, url
    assert client.post("/api/help/bundle", json={}).status_code == 403
    assert client.get("/api/help/status").get_json() == {}
    # /api/debug-info still answers its existing callers
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: True)
    resp = client.get("/api/debug-info?lines=10")
    assert resp.status_code == 200 and "recent_logs" in resp.get_json()


def test_auth_proxy_header_without_trusted_list_warns():
    health.configure(config_get=_config({"security": {"auth_proxy_header": "Remote-User"}}))
    found = _by_id(health.check_security())
    f = found["security.auth_proxy_trusted"]
    assert_shape(f)
    assert f["severity"] == "warning"
    health.configure(config_get=_config({"security": {"auth_proxy_header": "Remote-User",
                                                      "auth_proxy_trusted": "172.18.0.0/16"}}))
    assert health.check_security() == []
    health.configure(config_get=_config({"security": {"auth_proxy_header": ""}}))
    assert health.check_security() == []
