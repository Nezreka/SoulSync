"""the help sheet's Logs: every log file listed with its backups, downloads
redacted by default, raw only on the flag, only names from the server's own
list, admins only."""

from __future__ import annotations

import io
import zipfile

import pytest
from flask import Flask

# every one of these must be gone from anything downloaded without raw=1
SECRETS = {
    "apikey": "GET http://jellyfin:8096/Items?api_key=SECRETAPIKEY0123456789 failed",
    "bearer": "headers {'Authorization': 'Bearer abcdefghijklmnopqrstuvwxyz0123456789'}",
    "password": "login http://slskd:5030/api?password=hunter2password&user=me",
    "dictpw": "settings {'password': 'hunter2dictpw'}",
    "url": "connect http://boulder:sup3rs3cretpw@navidrome:4533/ ok",
    "plex": "GET http://plex:32400/library?X-Plex-Token=plextokenvalue99",
    "subsonic": "GET http://nd:4533/rest/ping?u=me&p=navpassword77&t=subsonictoken55&s=saltysalt",
    "jellyfin": 'Authorization: MediaBrowser Client="SoulSync", Token="jellyfintoken4242"',
}
NEEDLES = ("SECRETAPIKEY0123456789", "abcdefghijklmnopqrstuvwxyz0123456789", "hunter2password",
           "hunter2dictpw", "sup3rs3cretpw", "plextokenvalue99", "navpassword77", "subsonictoken55",
           "jellyfintoken4242")


@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    body = "".join(f"2026-11-17 10:00:0{i} - soulsync.x - ERROR - f:1 - {line}\n"
                   for i, line in enumerate(SECRETS.values()))
    (d / "app.log").write_text(body + "2026-11-17 10:01:00 - soulsync.x - INFO - f:1 - fine\n", encoding="utf-8")
    (d / "app.log.1").write_text("older " + SECRETS["apikey"] + "\n", encoding="utf-8")
    (d / "app.log.2").write_text("oldest " + SECRETS["bearer"] + "\n", encoding="utf-8")
    (d / "acoustid.log").write_text("fingerprint ok\n", encoding="utf-8")
    (d / "acoustid.log.1").write_bytes(b"older fingerprint\n")
    (d / "post_processing.log").write_text("", encoding="utf-8")
    (d / "newmusic.log").write_text("", encoding="utf-8")
    (d / "config.json").write_text('{"api_key": "inthefolder"}', encoding="utf-8")
    (d / "notes.txt").write_text("not a log", encoding="utf-8")
    (tmp_path / "outside.log").write_text("outside the folder", encoding="utf-8")
    return d


@pytest.fixture
def client(log_dir, monkeypatch):
    import api.help as help_api
    import core.profile_context as profile_context
    from core.help import bundle, health
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: True)
    help_api.configure(log_path=str(log_dir / "app.log"), version="3.6.7+abc1234")
    app = Flask(__name__)
    app.register_blueprint(help_api.create_blueprint())
    yield app.test_client()
    health.reset()
    bundle.reset()
    help_api._state.update({"log_path": None, "version": None})


def _zip(resp):
    return zipfile.ZipFile(io.BytesIO(resp.data))


# ---------------------------------------------------------------- listing
def test_list_matches_the_files_on_disk_with_backups(client, log_dir):
    data = client.get("/api/help/logs").get_json()
    names = [g["name"] for g in data["logs"]]
    # known logs first in their order, then the rest; never config.json or notes.txt
    assert names == ["app", "post_processing", "acoustid", "newmusic"]
    app = data["logs"][0]
    assert [f["name"] for f in app["files"]] == ["app.log", "app.log.1", "app.log.2"]
    assert [f["backup"] for f in app["files"]] == [False, True, True]
    assert app["label"] == "App log"
    for f in app["files"]:
        assert f["bytes"] == (log_dir / f["name"]).stat().st_size
        assert f["last_written"].endswith("Z")
    assert app["bytes"] == sum(f["bytes"] for f in app["files"])
    assert data["total_bytes"] == sum(g["bytes"] for g in data["logs"])
    assert data["zip_name"].startswith("soulsync-logs-3.6.7-")
    every = {f["name"] for g in data["logs"] for f in g["files"]}
    on_disk = {p.name for p in log_dir.iterdir() if ".log" in p.name}
    assert every == on_disk


# ---------------------------------------------------------------- one file
def test_one_file_is_redacted_by_default(client):
    resp = client.get("/api/help/logs/download?name=app.log")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    text = resp.get_data(as_text=True)
    for needle in NEEDLES:
        assert needle not in text, needle
    assert "***" in text
    assert "fine" in text   # the rest of the log is untouched


def test_raw_only_with_the_flag(client):
    text = client.get("/api/help/logs/download?name=app.log&raw=1").get_data(as_text=True)
    assert "SECRETAPIKEY0123456789" in text
    for flag in ("raw=0", "raw=", "raw=no"):
        text = client.get(f"/api/help/logs/download?name=app.log&{flag}").get_data(as_text=True)
        assert "SECRETAPIKEY0123456789" not in text


def test_a_backup_by_name(client):
    text = client.get("/api/help/logs/download?name=app.log.1").get_data(as_text=True)
    assert text.startswith("older ")
    assert "SECRETAPIKEY0123456789" not in text


def test_a_log_name_alone_is_its_current_file(client):
    text = client.get("/api/help/logs/download?name=app").get_data(as_text=True)
    assert "fine" in text and "older" not in text


# ---------------------------------------------------------------- zips
def test_one_log_with_backups_is_a_zip(client):
    resp = client.get("/api/help/logs/download?name=app&with_backups=1")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert "soulsync-logs-app-3.6.7-" in resp.headers["Content-Disposition"]
    zf = _zip(resp)
    assert sorted(zf.namelist()) == ["README.txt", "app.log", "app.log.1", "app.log.2"]
    for name in ("app.log", "app.log.1", "app.log.2"):
        body = zf.read(name).decode()
        for needle in NEEDLES:
            assert needle not in body, (name, needle)
    assert "Secrets: removed" in zf.read("README.txt").decode()


def test_everything_as_one_zip(client, log_dir):
    resp = client.get("/api/help/logs/download-all")
    zf = _zip(resp)
    names = set(zf.namelist())
    assert names == {"README.txt", "app.log", "app.log.1", "app.log.2", "acoustid.log",
                     "acoustid.log.1", "post_processing.log", "newmusic.log"}
    assert zf.read("acoustid.log.1").decode() == "older fingerprint\n"
    assert all(n not in zf.read("app.log").decode() for n in NEEDLES)
    readme = zf.read("README.txt").decode()
    assert "SoulSync 3.6.7 logs" in readme and "app.log.2" in readme
    raw = _zip(client.get("/api/help/logs/download-all?raw=1"))
    assert "SECRETAPIKEY0123456789" in raw.read("app.log").decode()
    assert "Secrets: left in" in raw.read("README.txt").decode()


def test_zip_streams_a_file_bigger_than_a_chunk(client, log_dir):
    line = "2026-11-17 10:00:00 - x - INFO - f:1 - " + "y" * 100 + " ?api_key=BIGSECRET99999\n"
    (log_dir / "app.log").write_text(line * 3000, encoding="utf-8")   # ~420 KB, many chunks
    zf = _zip(client.get("/api/help/logs/download?name=app&with_backups=1"))
    body = zf.read("app.log").decode()
    assert body.count("\n") == 3000 and "BIGSECRET99999" not in body


# ---------------------------------------------------------------- refusals
@pytest.mark.parametrize("name", [
    "../outside.log", "..\\outside.log", "outside.log", "config.json", "notes.txt",
    "/etc/passwd", "app.log/../config.json", "logs/app.log", "", "app.log\x00", "APP.LOG.9",
])
def test_a_name_not_in_the_list_is_refused(client, name):
    for url in (f"/api/help/logs/download?name={name}",
                f"/api/help/logs/download?name={name}&with_backups=1"):
        resp = client.get(url)
        assert resp.status_code == 404, (url, resp.status_code)
        assert resp.get_json()["error"] == "No such log"


def test_a_symlink_in_the_folder_is_not_listed(client, log_dir, tmp_path):
    try:
        (log_dir / "probe.lnk").symlink_to(tmp_path / "outside.log")
        (log_dir / "probe.lnk").unlink()
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    (log_dir / "sneaky.log").symlink_to(tmp_path / "outside.log")
    names = {f["name"] for g in client.get("/api/help/logs").get_json()["logs"] for f in g["files"]}
    assert "sneaky.log" not in names
    assert client.get("/api/help/logs/download?name=sneaky.log").status_code == 404


def test_find_and_safe_path_guards(log_dir):
    from core.help import logs
    listed = logs.list_logs(log_dir, log_dir / "app.log")
    assert logs.find("../app.log", listed) == (None, None)
    assert logs.find("config.json", listed) == (None, None)
    group, f = logs.find("app.log.2", listed)
    assert group["name"] == "app" and f["name"] == "app.log.2"
    assert logs.safe_path(log_dir, "../outside.log") is None
    assert logs.safe_path(log_dir, "app.log") is not None


# ---------------------------------------------------------------- members
@pytest.mark.parametrize("url", [
    "/api/help/logs", "/api/help/logs/download?name=app.log",
    "/api/help/logs/download?name=app&with_backups=1", "/api/help/logs/download-all",
    "/api/help/logs/download?name=app.log&raw=1",
])
def test_a_member_is_refused(client, monkeypatch, url):
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    resp = client.get(url)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "Admin access required"


# ---------------------------------------------------------------- helpers
def test_tail_lines_reads_the_end_redacted(log_dir):
    from core.help.logs import tail_lines
    got = tail_lines(log_dir / "app.log", 3)
    assert len(got) == 3 and got[-1].endswith("fine")
    assert all("SECRETAPIKEY" not in g for g in tail_lines(log_dir / "app.log", 50))
