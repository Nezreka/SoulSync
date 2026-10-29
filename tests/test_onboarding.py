"""first-run setup flow backend (core/onboarding, api/onboarding).

the old wizard (webui/static/setup-wizard.js, oct 2026 review) saved a
docker-only /app path on bare metal installs and tested connections by
writing the candidate values into the live config. these pin the
replacements: folders that fit where soulsync runs, and probes that never
write anything.
"""

from __future__ import annotations

import os

import pytest

from core.onboarding import checklist, discovery, environment, paths
from core.onboarding.probes import Prober, probe


# ---- environment ------------------------------------------------------

def test_bare_metal_never_gets_container_paths():
    """the old wizard forced /app/downloads on every install"""
    env = environment.describe(exists=lambda p: False, environ={}, system=lambda: "Linux")
    assert env["docker"] is False
    assert env["folders"]["download"] == "./downloads"
    assert env["folders"]["library"] == "./Transfer"
    assert not any(str(v).startswith("/app") for v in env["folders"].values())


def test_docker_gets_the_compose_paths():
    env = environment.describe(exists=lambda p: p == "/.dockerenv", environ={})
    assert env["docker"] is True
    assert env["folders"]["download"] == "/app/downloads"
    assert env["folders"]["movies"] == "/media/movies"


def test_docker_flag_from_env_var():
    assert environment.is_docker(exists=lambda p: False, environ={"SOULSYNC_IN_DOCKER": "true"})


# ---- paths --------------------------------------------------------------

def test_existing_writable_folder_is_ok(tmp_path):
    result = paths.check_path(str(tmp_path), docker=False)
    assert result["state"] == "ok"
    assert result["free_bytes"] is not None and result["free_bytes"] > 0


def test_missing_folder_with_writable_parent_will_be_created(tmp_path):
    result = paths.check_path(str(tmp_path / "a" / "b"), docker=False)
    assert result["state"] == "create"
    assert not (tmp_path / "a").exists(), "checking must never create anything"


def test_unwritable_folder_is_a_problem(tmp_path):
    result = paths.check_path(str(tmp_path), docker=True, writable=lambda p: False)
    assert result["state"] == "problem"
    assert "PUID" in result["message"]


def test_missing_folder_that_cannot_be_created(tmp_path):
    result = paths.check_path(str(tmp_path / "nope"), docker=True, writable=lambda p: False)
    assert result["state"] == "problem"
    assert "compose" in result["message"]


def test_a_file_is_not_a_folder(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert paths.check_path(str(f), docker=False)["state"] == "problem"


def test_windows_path_inside_docker_is_caught():
    result = paths.check_path(r"H:\Soulseek\Downloaded", docker=True)
    assert result["state"] == "problem"
    assert "Windows path" in result["message"]


def test_empty_path_asks_for_one():
    assert paths.check_path("  ", docker=False)["state"] == "problem"


def test_two_rows_on_one_folder_are_flagged(tmp_path):
    results = paths.check_paths({"download": str(tmp_path), "library": str(tmp_path) + os.sep},
                                docker=False)
    assert results["download"]["state"] == "ok"
    assert results["library"]["state"] == "problem"
    assert "Same folder" in results["library"]["message"]


def test_a_folder_inside_another_row_is_noted(tmp_path):
    lib = tmp_path / "library"
    results = paths.check_paths({"download": str(tmp_path), "library": str(lib),
                                 "other": str(tmp_path.parent / "elsewhere")}, docker=False)
    assert results["library"]["inside"] == "download"
    assert "inside" not in results["download"] and "inside" not in results["other"]
    # the nearest parent wins
    deep = paths.check_paths({"a": str(tmp_path), "b": str(lib), "c": str(lib / "x")},
                             docker=False)
    assert deep["c"]["inside"] == "b"


def test_docker_folder_on_no_mapped_volume_is_noted(tmp_path):
    mounts = ["/", str(tmp_path / "mapped")]
    results = paths.check_paths({"lost": str(tmp_path / "loose"),
                                 "kept": str(tmp_path / "mapped" / "movies")},
                                docker=True, mounts=mounts)
    assert results["lost"].get("unmapped") is True
    assert "unmapped" not in results["kept"]
    # bare metal never asks
    assert "unmapped" not in paths.check_paths({"x": str(tmp_path / "loose")},
                                               docker=False, mounts=mounts)["x"]


def test_mount_points_reads_mountinfo_and_fails_soft(tmp_path):
    info = tmp_path / "mountinfo"
    info.write_text("22 1 0:21 / / rw - overlay overlay rw\n"
                    "23 22 8:1 /m /media/my\\040movies rw - ext4 /dev/sda1 rw\n")
    assert paths.mount_points(str(info)) == ["/", "/media/my movies"]
    assert paths.mount_points(str(tmp_path / "nope")) == []
    assert paths.on_mapped_volume("/media/my movies/x", ["/", "/media/my movies"]) is True
    assert paths.on_mapped_volume("/app/downloads", ["/", "/app/config"]) is False
    assert paths.on_mapped_volume("/app/downloads", []) is None


# ---- probes -------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, json=None, text=""):
        self.status_code = status
        self._json = json
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _Http:
    """routes by url substring; records every call"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _match(self, url):
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise ConnectionError(url)

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return self._match(url)

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        return self._match(url)


@pytest.fixture
def no_config_writes(monkeypatch):
    """a probe that writes config fails the test"""
    from core.settings import config_manager

    def refuse(*a, **k):
        raise AssertionError("onboarding probes must never write config")

    monkeypatch.setattr(config_manager, "set", refuse)


def test_slskd_ok_and_refused(no_config_writes):
    http = _Http({"/api/v0/application": _Resp(200, {"version": {"full": "0.22"}})})
    res = Prober(http).slskd({"slskd_url": "http://h:5030/", "api_key": "k"})
    assert res["ok"] and res["detail"]["version"] == "0.22"
    assert http.calls[0][2]["headers"]["X-API-Key"] == "k"
    refused = Prober(_Http({"/api/v0/application": _Resp(401)})).slskd({"slskd_url": "http://h:5030"})
    assert not refused["ok"] and "API key" in refused["message"]


def test_unreachable_service_is_not_an_exception(no_config_writes):
    res = Prober(_Http({})).slskd({"slskd_url": "http://gone:5030"})
    assert res == {"ok": False, "message": "Can't reach slskd at http://gone:5030", "detail": {}}


def test_docker_url_fix_is_applied_but_message_shows_what_was_typed():
    http = _Http({})
    res = Prober(http, resolve_url=lambda u: u.replace("localhost", "host.docker.internal")).slskd(
        {"slskd_url": "http://localhost:5030"})
    assert "host.docker.internal" in http.calls[0][1]
    assert "localhost" in res["message"]


def test_plex_probe_reads_server_name(no_config_writes):
    http = _Http({"h:32400/": _Resp(200, {"MediaContainer": {"friendlyName": "Den"}})})
    res = Prober(http).plex({"base_url": "http://h:32400", "token": "t"})
    assert res["ok"] and res["message"] == "Connected to Den"
    assert Prober(_Http({"h:32400/": _Resp(401)})).plex({"base_url": "http://h:32400"})["ok"] is False


def test_tmdb_read_access_token_gets_a_useful_message():
    res = Prober(_Http({})).tmdb({"api_key": "eyJhbGciOiJIUzI1NiJ9.x.y"})
    assert not res["ok"] and "read access token" in res["message"]


def test_prowlarr_counts_enabled_indexers(no_config_writes):
    http = _Http({"/api/v1/indexer": _Resp(200, [{"enable": True}, {"enable": False}, {"enable": True}])})
    res = Prober(http).prowlarr({"url": "http://p:9696", "api_key": "k"})
    assert res["ok"] and res["detail"]["indexers"] == 2 and "2 indexers" in res["message"]


def test_qbittorrent_login(no_config_writes):
    assert Prober(_Http({"/auth/login": _Resp(200, text="Ok.")})).qbittorrent({"url": "http://q:8080"})["ok"]
    assert not Prober(_Http({"/auth/login": _Resp(200, text="Fails.")})).qbittorrent({"url": "http://q:8080"})["ok"]


def test_listenbrainz_token(no_config_writes):
    ok = Prober(_Http({"validate-token": _Resp(200, {"valid": True, "user_name": "bb"})})).listenbrainz({"token": "x"})
    assert ok["ok"] and ok["detail"]["user"] == "bb"
    assert not Prober(_Http({"validate-token": _Resp(200, {"valid": False})})).listenbrainz({"token": "x"})["ok"]


def test_unknown_service_and_crashing_probe_never_raise():
    assert probe("nope", {})["ok"] is False

    class Boom(Prober):
        def slskd(self, cfg):
            raise RuntimeError("x")

    assert probe("slskd", {}, Boom())["ok"] is False


def test_plex_libraries_split_by_type_with_counts():
    sections = {"MediaContainer": {"Directory": [
        {"key": "1", "type": "artist", "title": "Music", "Location": [{"path": "/data/music"}]},
        {"key": "2", "type": "movie", "title": "Movies"},
        {"key": "3", "type": "show", "title": "TV"},
        {"key": "4", "type": "photo", "title": "Photos"},
    ]}}
    http = _Http({"/library/sections/1/all": _Resp(200, {"MediaContainer": {"totalSize": 2184}}),
                  "/library/sections/2/all": _Resp(200, {"MediaContainer": {"totalSize": 612}}),
                  "/library/sections/3/all": _Resp(500),
                  "/library/sections": _Resp(200, sections)})
    libs = Prober(http).libraries("plex", {"base_url": "http://h:32400", "token": "t"})
    assert [l["title"] for l in libs["music"]] == ["Music"]
    assert libs["music"][0]["count"] == 2184 and libs["music"][0]["locations"] == ["/data/music"]
    assert libs["movies"][0]["count"] == 612
    assert libs["tv"][0]["count"] is None


def test_top_artists_dedupes_and_drops_various_artists():
    http = _Http({"/library/sections/1/all": _Resp(200, {"MediaContainer": {"Metadata": [
        {"title": "Radiohead"}, {"title": "Various Artists"}, {"title": "radiohead"}, {"title": "Björk"}]}})})
    names = Prober(http).top_artists("plex", {"base_url": "http://h", "token": "t"}, "1")
    assert names == ["Radiohead", "Björk"]


def test_top_artists_on_a_dead_server_is_empty():
    assert Prober(_Http({})).top_artists("plex", {"base_url": "http://h"}, "1") == []


# ---- discovery ----------------------------------------------------------

def _fake_get(answers):
    def get(url, **kw):
        for key, resp in answers.items():
            if url.startswith(key):
                return resp
        raise ConnectionError(url)
    return get


def test_docker_skips_localhost_and_tries_the_host():
    hosts = discovery.candidate_hosts(docker=True, local_ip="192.168.1.5")
    assert "localhost" not in hosts
    assert hosts[:2] == ["host.docker.internal", "172.17.0.1"]
    assert "192.168.1.1" in hosts


def test_compose_name_wins_over_a_host_match():
    get = _fake_get({
        "http://prowlarr:9696/": _Resp(200, text="<title>Prowlarr</title>"),
        "http://192.168.1.5:9696/": _Resp(200, text="<title>Prowlarr</title>"),
    })
    found = discovery.discover(["prowlarr"], docker=True, local_ip="192.168.1.5",
                               resolves=lambda n: n == "prowlarr", get=get)
    assert found == {"prowlarr": "http://prowlarr:9696"}


def test_qbittorrent_and_sabnzbd_on_8080_are_told_apart():
    get = _fake_get({
        "http://localhost:8080/api": _Resp(404, text="nope"),
        "http://localhost:8080/": _Resp(200, text="<title>qBittorrent WebUI</title>"),
        "http://localhost:8085/api": _Resp(200, text='{"version": "4.3.2"}'),
    })
    found = discovery.discover(["qbittorrent", "sabnzbd"], docker=False, local_ip=None,
                               resolves=lambda n: False, get=get)
    assert found == {"qbittorrent": "http://localhost:8080", "sabnzbd": "http://localhost:8085"}


def test_plex_fingerprint_needs_the_identity_payload():
    get = _fake_get({"http://localhost:32400/identity": _Resp(200, text="<html>router login</html>")})
    assert discovery.discover(["plex"], docker=False, local_ip=None, resolves=lambda n: False, get=get) == {}


def test_unknown_services_are_ignored():
    assert discovery.discover(["nope"], docker=False, local_ip=None, resolves=lambda n: False,
                              get=_fake_get({})) == {}


# ---- checklist ----------------------------------------------------------

def _getter(values):
    return lambda key, default=None: values.get(key, default)


def test_checklist_only_lists_what_applies():
    data = checklist.build(_getter({"download_source.mode": "hifi"}), profile_count=1)
    ids = [i["id"] for i in data["items"]]
    assert "slskd_shares" not in ids and "video" not in ids
    assert {"torrent", "spotify", "extension", "invite"} <= set(ids)


def test_checklist_detects_done_items_from_config():
    data = checklist.build(_getter({
        "download_source.mode": "hybrid", "download_source.hybrid_order": ["soulseek", "hifi"],
        "soulseek.slskd_url": "http://slskd:5030/", "setup.purposes": ["music", "video"],
        "prowlarr.url": "http://p:9696", "setup.checklist_done": ["extension"],
    }), profile_count=2, tmdb_key="abc")
    items = {i["id"]: i for i in data["items"]}
    assert items["slskd_shares"]["href"] == "http://slskd:5030/shares"
    assert not items["slskd_shares"]["done"]
    assert items["video"]["done"] and items["torrent"]["done"] and items["invite"]["done"]
    assert items["extension"]["done"] and not items["spotify"]["done"]


# ---- api ----------------------------------------------------------------

class _Config:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _client(monkeypatch, *, admin=True, cfg=None):
    from flask import Flask

    import api.onboarding as onboarding_api

    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: admin)
    cfg = cfg or _Config()

    class _Db:
        def get_all_profiles(self):
            return [{"id": 1}]

    onboarding_api.configure(config_manager_=cfg, get_database_=lambda: _Db())
    monkeypatch.setattr(onboarding_api, "_tmdb_key", lambda: "")
    app = Flask(__name__)
    app.register_blueprint(onboarding_api.create_blueprint())
    return app.test_client(), cfg


def test_api_is_admin_only(monkeypatch):
    client, _ = _client(monkeypatch, admin=False)
    for method, url in (("get", "/api/onboarding/environment"), ("post", "/api/onboarding/probe"),
                        ("post", "/api/onboarding/discover"), ("get", "/api/onboarding/checklist")):
        assert getattr(client, method)(url, json={}).status_code == 403


def test_api_probe_passes_config_through(monkeypatch):
    seen = {}

    def fake_probe(service, cfg, prober):
        seen.update(service=service, cfg=cfg)
        return {"ok": True, "message": "fine", "detail": {}}

    monkeypatch.setattr("api.onboarding.probe", fake_probe)
    client, cfg = _client(monkeypatch)
    reply = client.post("/api/onboarding/probe", json={"service": "slskd", "config": {"api_key": "k"}})
    assert reply.get_json()["ok"] is True
    assert seen == {"service": "slskd", "cfg": {"api_key": "k"}}
    assert cfg.values == {}


def test_api_environment_pretend(monkeypatch):
    client, _ = _client(monkeypatch)
    bare = client.get("/api/onboarding/environment?pretend=bare").get_json()
    assert bare["docker"] is False and bare["folders"]["download"] == "./downloads"
    docker = client.get("/api/onboarding/environment?pretend=docker").get_json()
    assert docker["docker"] is True and docker["folders"]["download"] == "/app/downloads"


def test_api_check_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("core.onboarding.environment.is_docker", lambda *a, **k: False)
    client, _ = _client(monkeypatch)
    reply = client.post("/api/onboarding/check-paths", json={"paths": {"download": str(tmp_path)}})
    assert reply.get_json()["results"]["download"]["state"] == "ok"


def test_api_checklist_ticks_and_dismisses(monkeypatch):
    client, cfg = _client(monkeypatch)
    client.post("/api/onboarding/checklist", json={"done": "extension"})
    client.post("/api/onboarding/checklist", json={"done": "extension"})
    assert cfg.values["setup.checklist_done"] == ["extension"]
    reply = client.post("/api/onboarding/checklist", json={"dismissed": True}).get_json()
    assert reply["dismissed"] is True
    assert next(i for i in reply["items"] if i["id"] == "extension")["done"] is True


# ---- checklist items that tick themselves --------------------------------
# boulder, oct 20: "how do you actually get them checked?" the slskd shares
# and browser extension rows only ticked by clicking a circle nothing pointed
# at. both are now noticed for real.

def test_slskd_share_count_from_application_state():
    http = _Http({"/api/v0/application": _Resp(200, {"shares": {"files": 1234, "directories": 50}})})
    assert Prober(http).slskd_shared_files({"slskd_url": "http://s:5030", "api_key": "k"}) == 1234


def test_slskd_share_count_falls_back_to_the_share_list():
    http = _Http({
        "/api/v0/application": _Resp(200, {"version": {}}),
        "/api/v0/shares": _Resp(200, {"local": [{"localPath": "/music", "files": 40},
                                                {"localPath": "/more", "files": 2}]}),
    })
    assert Prober(http).slskd_shared_files({"slskd_url": "http://s:5030"}) == 42


def test_slskd_share_count_unknown_is_none_not_zero():
    http = _Http({"/api/v0/application": _Resp(401), "/api/v0/shares": _Resp(401)})
    assert Prober(http).slskd_shared_files({"slskd_url": "http://s:5030"}) is None
    assert Prober(_Http({})).slskd_shared_files({"slskd_url": ""}) is None


@pytest.fixture
def fresh_extension_flag(monkeypatch):
    monkeypatch.setattr(checklist, "_extension_noted", False)


def test_extension_origin_is_noted_once(fresh_extension_flag):
    cfg = _Config()
    calls = []

    def set_(key, value):
        calls.append(key)
        cfg.set(key, value)

    assert checklist.note_api_client("https://evil.example", cfg.get, set_) is False
    assert checklist.note_api_client(None, cfg.get, set_) is False
    assert checklist.note_api_client("chrome-extension://abcdef", cfg.get, set_) is True
    assert checklist.note_api_client("moz-extension://x", cfg.get, set_) is False  # already noted
    assert calls == ["setup.extension_seen"]
    items = {i["id"]: i for i in checklist.build(cfg.get, profile_count=1)["items"]}
    assert items["extension"]["done"] is True


def test_a_broken_config_never_breaks_the_request(fresh_extension_flag):
    def boom(*a, **k):
        raise RuntimeError("db locked")

    assert checklist.note_api_client("chrome-extension://x", boom, boom) is False


def test_extension_key_call_ticks_the_checklist_through_the_real_decorator(fresh_extension_flag):
    """the seam: a v1 call with a valid key from an extension origin"""
    import hashlib

    from flask import Flask

    from api.auth import require_api_key

    raw = "sk_onboarding-test"
    cfg = _Config()
    cfg.values["api_keys"] = [{"id": "k1", "key_hash": hashlib.sha256(raw.encode()).hexdigest(), "label": "ext"}]
    app = Flask(__name__)
    app.soulsync = {"config_manager": cfg}

    @app.route("/api/v1/system/stats")
    @require_api_key
    def stats():
        return {"ok": True}

    client = app.test_client()
    client.get("/api/v1/system/stats", headers={"Authorization": f"Bearer {raw}"})
    assert not cfg.values.get("setup.extension_seen")
    client.get("/api/v1/system/stats", headers={"Authorization": f"Bearer {raw}",
                                                "Origin": "chrome-extension://abc"})
    assert cfg.values.get("setup.extension_seen") is True


def test_checklist_asks_slskd_and_remembers_a_yes(monkeypatch):
    import api.onboarding as onboarding_api

    cfg = _Config()
    cfg.values.update({"download_source.mode": "soulseek", "soulseek.slskd_url": "http://s:5030"})
    client, _ = _client(monkeypatch, cfg=cfg)
    answers = iter([0, 25])
    asked = []

    class _P:
        def slskd_shared_files(self, c):
            asked.append(c["slskd_url"])
            return next(answers)

    monkeypatch.setattr(onboarding_api, "_prober", lambda: _P())
    monkeypatch.setattr(onboarding_api, "_shares_checked_at", 0.0)
    clock = iter([1000.0, 1010.0, 1200.0, 1300.0])
    monkeypatch.setattr(onboarding_api.time, "monotonic", lambda: next(clock))

    def item():
        data = client.get("/api/onboarding/checklist").get_json()
        return next(i for i in data["items"] if i["id"] == "slskd_shares")

    assert item()["done"] is False          # slskd says 0 files
    assert item()["done"] is False          # too soon, slskd isn't asked again
    assert item()["done"] is True           # asked again, now shares 25
    assert item()["done"] is True           # remembered, not asked
    assert asked == ["http://s:5030", "http://s:5030"]
    assert "slskd_shares" in cfg.values["setup.checklist_done"]
