"""the library coverage routes, with fake Plex, Jellyfin and Navidrome clients
behind a fake media server engine. Every route is admin only."""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

import api.library_coverage as routes
import core.profile_context as profile_context
from core.library import coverage, path_mappings
from core.library2.media_mappings import ensure_media_mapping_schema


class FakeDB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


class Engine:
    def __init__(self, name, client):
        self.active_server = name
        self._client = client

    def active_client(self):
        return self._client


class FakePlex:
    def __init__(self, folders):
        self.folders = folders

    def get_music_library_folders(self):
        return [{"library": "Music", "folders": self.folders}]


class FakeJellyfin(FakePlex):
    pass


class FakeNavidrome:
    def get_music_folders(self):
        return [{"title": "Music", "key": "1"}]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
        c.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    ensure_media_mapping_schema(c.cursor())
    path_mappings.ensure_schema(c.cursor())
    c.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER,"
              " path TEXT NOT NULL, size INTEGER)")
    lib = tmp_path / "music"
    for i in range(4):
        f = lib / "Beck" / "Guero" / f"{i:02d}.flac"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"a")
    c.commit()
    c.close()
    state = {"engine": None, "admin": True, "lib": lib, "db": db}

    def add(kind, paths):
        with sqlite3.connect(db) as conn:
            for i, p in enumerate(paths, 1):
                conn.execute(
                    "INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source,"
                    " server_id, server_path) VALUES('track', ?, ?, ?, ?)", (i, kind, str(i), p))
    state["add"] = add
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: state["admin"])
    routes.configure(get_database=lambda: FakeDB(str(db)),
                     media_server_engine_getter=lambda: state["engine"],
                     env_factory=lambda: coverage.Env(library_dirs=[str(lib)]))
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint())
    coverage.forget()
    yield app.test_client(), state
    coverage.forget()
    path_mappings.configure(None)


def _paths(prefix):
    return [f"{prefix}/Beck/Guero/{i:02d}.flac" for i in range(4)]


def test_plex_folders_are_listed_and_checked(setup):
    client, state = setup
    state["engine"] = Engine("plex", FakePlex(["/plexdata/music", "/plexdata/archive"]))
    state["add"]("plex", _paths("/plexdata/music") + ["/plexdata/archive/A/B/1.flac"])
    got = client.get("/api/library/coverage?refresh=1").get_json()
    assert got["success"] and got["server"] == "plex" and got["total_tracks"] == 5
    by = {f["server_folder"]: f for f in got["folders"]}
    assert by["/plexdata/music"]["status"] == "covered"
    assert by["/plexdata/music"]["local_folder"] == str(state["lib"])
    assert by["/plexdata/archive"]["status"] == "not_mounted"
    assert got["reachable_pct"] == 80.0
    # remembered: the plain GET answers from the last run
    state["engine"] = Engine("plex", FakePlex([]))
    assert client.get("/api/library/coverage").get_json()["reachable_pct"] == 80.0


def test_jellyfin_folders(setup):
    client, state = setup
    state["engine"] = Engine("jellyfin", FakeJellyfin(["/media/music"]))
    state["add"]("jellyfin", _paths("/media/music"))
    got = client.get("/api/library/coverage?refresh=1").get_json()
    assert got["folders_from"] == "server"
    assert got["folders"][0]["status"] == "covered" and got["reachable_pct"] == 100.0


def test_navidrome_folders_are_inferred(setup):
    client, state = setup
    state["engine"] = Engine("navidrome", FakeNavidrome())
    state["add"]("navidrome", _paths("/nd/music"))
    got = client.get("/api/library/coverage?refresh=1").get_json()
    assert got["folders_from"] == "inferred"
    assert [f["server_folder"] for f in got["folders"]] == ["/nd/music"]
    assert got["folders"][0]["status"] == "covered"


def test_no_media_server_is_a_plain_answer(setup):
    client, state = setup
    got = client.get("/api/library/coverage?refresh=1").get_json()
    assert got["success"] and got["folders"] == [] and "No media server" in got["message"]


def test_one_folder_as_if_mapped_to_a_typed_path(setup):
    client, state = setup
    state["engine"] = Engine("plex", FakePlex(["/weird"]))
    state["add"]("plex", _paths("/weird/place"))
    got = client.post("/api/library/coverage/folder",
                      json={"server_folder": "/weird/place",
                            "local_folder": str(state["lib"])}).get_json()
    assert got["folder"]["found_by"] == "override" and got["folder"]["readable"] == 4
    # nothing was saved by the preview
    assert client.get("/api/library/path-mappings").get_json()["mappings"] == []


def test_check_path(setup):
    client, state = setup
    got = client.post("/api/library/coverage/check-path", json={"path": str(state["lib"])}).get_json()
    assert got["is_dir"] and got["readable"] and got["sample"] == ["Beck"]
    assert got["library_folder"] == str(state["lib"])
    got = client.post("/api/library/coverage/check-path", json={"path": "/no/such/dir"}).get_json()
    assert got["exists"] is False and got["readable"] is False
    assert client.post("/api/library/coverage/check-path", json={}).status_code == 400


def test_override_crud_and_verify(setup):
    client, state = setup
    state["engine"] = Engine("plex", FakePlex(["/weird/place"]))
    state["add"]("plex", _paths("/weird/place"))
    made = client.post("/api/library/path-mappings",
                       json={"server_prefix": "/weird/place/", "local_prefix": str(state["lib"]),
                             "this_server": True}).get_json()
    assert made["success"] and made["mapping"]["server_kind"] == "plex"
    mid = made["mapping"]["id"]
    assert made["mapping"]["server_prefix"] == "/weird/place"
    got = client.post(f"/api/library/path-mappings/{mid}/verify").get_json()
    assert got["folder"]["readable_pct"] == 100.0
    [row] = client.get("/api/library/path-mappings").get_json()["mappings"]
    assert row["verify"]["readable"] == 4 and row["verified_at"]
    assert client.put(f"/api/library/path-mappings/{mid}",
                      json={"server_prefix": "/weird/place", "local_prefix": "nope"}).status_code == 400
    assert client.delete(f"/api/library/path-mappings/{mid}").get_json()["success"]
    assert client.delete(f"/api/library/path-mappings/{mid}").status_code == 404


def test_saving_an_override_reaches_the_resolver(setup):
    client, state = setup
    client.post("/api/library/path-mappings",
                json={"server_prefix": "/weird/place", "local_prefix": str(state["lib"])})
    assert path_mappings.local_path("/weird/place/Beck/Guero/00.flac") == str(
        state["lib"] / "Beck" / "Guero" / "00.flac")


ROUTES = [
    ("get", "/api/library/coverage", None),
    ("post", "/api/library/coverage/folder", {"server_folder": "/x"}),
    ("post", "/api/library/coverage/check-path", {"path": "/"}),
    ("get", "/api/library/path-mappings", None),
    ("post", "/api/library/path-mappings", {"server_prefix": "/x", "local_prefix": "/y"}),
    ("put", "/api/library/path-mappings/1", {"server_prefix": "/x", "local_prefix": "/y"}),
    ("delete", "/api/library/path-mappings/1", None),
    ("post", "/api/library/path-mappings/1/verify", None),
]


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_every_route_is_admin_only(setup, method, url, body):
    client, state = setup
    state["admin"] = False
    res = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert res.status_code == 403


def test_every_route_declares_admin(setup):
    from core.permissions import declared_access
    app = Flask("inv")
    app.register_blueprint(routes.create_blueprint())
    rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("library_coverage.")]
    assert len(rules) == len(ROUTES)
    for rule in rules:
        assert declared_access(app.view_functions[rule.endpoint])[0] == "admin", rule.rule
