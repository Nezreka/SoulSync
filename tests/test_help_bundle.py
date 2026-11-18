"""Report a problem: the bundle is the text people read, with every secret
removed, every part switchable, and shareable as a GitHub issue, a Discord
message or a file. Copy Debug Info reads the same source and no longer sends
log lines raw."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask

from core.help import bundle

NOW = datetime(2026, 11, 17, 21, 42, tzinfo=timezone.utc)

PLANTED_CONFIG = {
    "spotify": {"client_id": "__redacted_unchanged__", "client_secret": "__redacted_unchanged__"},
    "soulseek": {"slskd_url": "http://admin:slskdurlpass@slskd:5030", "api_key": "slskdapikey12345",
                 "download_path": "/downloads"},
    "plex": {"base_url": "http://plex:32400", "token": "plextoken777"},
    "jellyfin": {"base_url": "http://jellyfin:8096", "api_key": "jellyapikey888"},
    "navidrome": {"base_url": "http://nd:4533/rest/ping?u=me&p=navurlpass", "password": "navpass999"},
    "discord": {"webhook_url": "https://discord.com/api/webhooks/1/hooksecret"},
    "lastfm": {"api_key": "", "session_key": "lastfmsession"},
    "download_source": {"mode": "hybrid", "hybrid_order": ["hifi", "soulseek"],
                        "path_mappings": [{"from": "/data", "to": "/mnt/secretname"}]},
    "logging": {"level": "INFO"},
    "post_processing": {"enabled": True},
}
CONFIG_NEEDLES = ("slskdurlpass", "slskdapikey12345", "plextoken777", "jellyapikey888", "navurlpass",
                  "navpass999", "hooksecret", "lastfmsession", "__redacted_unchanged__", "secretname")

PLANTED_LOG = [
    "GET http://jellyfin:8096/Library/Refresh?api_key=LOGAPIKEY1234567890 timed out",
    "{'Authorization': 'Bearer LOGBEARERTOKEN0123456789abcdef'}",
    "login http://slskd:5030/api?password=LOGPASSWORD99",
    "http://boulder:LOGURLPASS@navidrome:4533/rest/ping?u=me&p=LOGSUBPASS&t=LOGSUBTOKEN",
    'Authorization: MediaBrowser Token="LOGJELLYTOKEN42"',
    "GET http://plex/x?X-Plex-Token=LOGPLEXTOKEN77",
    "spotify {'client_secret': 'LOGSPOTIFYSECRET'}",
]
LOG_NEEDLES = ("LOGAPIKEY1234567890", "LOGBEARERTOKEN0123456789abcdef", "LOGPASSWORD99", "LOGURLPASS",
               "LOGSUBPASS", "LOGSUBTOKEN", "LOGJELLYTOKEN42", "LOGPLEXTOKEN77", "LOGSPOTIFYSECRET")

INFO = {
    "version": "3.6.7+d7e7541", "os": "Linux 6.6.87", "python": "3.12.7", "docker": True,
    "runner": "gunicorn", "ffmpeg": "7.1", "uptime": "3 days, 4:00:00", "memory_usage": "612 MB",
    "system_memory": "43%", "database_size": "1.2 GB", "library": {"artists": 1, "albums": 2, "tracks": 3},
    "services": {"music_source": "deezer", "spotify_connected": False, "media_server_type": "jellyfin",
                 "media_server_connected": False, "soulseek_connected": True, "download_source": "hybrid"},
    "config": {"hybrid_sources": ["hifi", "soulseek"]},
    "enrichment_workers": {"lastfm": "paused", "deezer": "active"},
    "paths": {"download_path": "/downloads", "download_path_exists": True, "download_path_writable": False,
              "transfer_folder": "/music", "transfer_folder_exists": True, "transfer_folder_writable": True,
              "staging_folder": "", "music_library_paths": [{"path": "/music", "exists": True}]},
}
HEALTH = {"status": "error", "checked_at": "2026-11-17T21:42:00Z",
          "counts": {"error": 1, "warning": 1, "info": 0, "ok": 5},
          "findings": [
              {"id": "services.media_server", "severity": "error", "area": "services",
               "title": "Jellyfin isn't answering", "detail": "http://jellyfin:8096 didn't answer."},
              {"id": "downloads.review", "severity": "warning", "area": "downloads",
               "title": "4 downloads are waiting for you", "detail": ""},
              {"id": "paths.transfer", "severity": "ok", "area": "paths", "title": "Transfer folder writable",
               "detail": ""}]}


@pytest.fixture
def app_log(tmp_path):
    log = tmp_path / "app.log"
    recent = (NOW - timedelta(hours=1)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    old = (NOW - timedelta(days=3)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"{old} - soulsync.x - ERROR - f:1 - an old error"]
    lines += [f"{recent} - soulsync.x - ERROR - f:1 - {s}" for s in PLANTED_LOG]
    lines += [f"{recent} - soulsync.x - INFO - f:1 - just info"]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


@pytest.fixture(autouse=True)
def fakes(app_log):
    bundle.reset()
    bundle.configure(debug_info=lambda log_lines=20: dict(INFO), health=lambda: HEALTH,
                     redacted_config=lambda: PLANTED_CONFIG, version=lambda: "3.6.7+d7e7541",
                     default_config=lambda: {"logging": {"level": "INFO"}, "jellyfin": {"api_key": ""}},
                     app_log_files=lambda: [app_log, app_log.with_name("app.log.1")])
    yield
    bundle.reset()


def _parts(data):
    return {p["id"]: p for p in data["parts"]}


# ---------------------------------------------------------------- redaction
def test_no_planted_secret_survives_by_default():
    data = bundle.build(parts=list(bundle.PART_IDS), now=NOW)
    everything = data["text"] + "".join(p["text"] for p in data["parts"]) + data["discord"] + data["github"]["url"]
    for needle in CONFIG_NEEDLES + LOG_NEEDLES:
        assert needle not in everything, needle
    settings = _parts(data)["settings"]["text"]
    assert "soulseek.api_key  ***" in settings
    assert "spotify.client_secret  ***" in settings
    assert "jellyfin.base_url  http://jellyfin:8096" in settings      # the shape still reads
    assert "download_source.path_mappings  [1 items]" in settings
    assert "lastfm.api_key" not in settings                            # empty, left out
    assert "***" in _parts(data)["logs"]["text"]


def test_settings_show_only_what_changed():
    text = _parts(bundle.build(now=NOW))["settings"]["text"]
    assert "logging.level" not in text                      # at its default
    assert "post_processing.enabled  on" in text            # not in the defaults: shown
    assert "jellyfin.api_key  ***" in text                  # set, so changed
    first = text.splitlines()[1]
    assert first.endswith("changed from the defaults (the rest are default):")
    bundle.configure(default_config=lambda: PLANTED_CONFIG)
    assert "Every setting is at its default." in _parts(bundle.build(now=NOW))["settings"]["text"]


def test_a_secret_in_the_note_is_removed_too():
    data = bundle.build(note="my key is ?api_key=NOTEKEY1234567890 help", now=NOW)
    assert "NOTEKEY1234567890" not in data["text"]
    assert "NOTEKEY1234567890" not in data["github"]["url"]
    assert "NOTEKEY1234567890" not in data["discord"]


# ---------------------------------------------------------------- parts
def test_default_parts_and_shapes():
    data = bundle.build(now=NOW)
    assert [p["id"] for p in data["parts"]] == list(bundle.PART_IDS)
    parts = _parts(data)
    assert [pid for pid, p in parts.items() if p["included"]] == list(bundle.DEFAULT_PARTS)
    assert not parts["errors"]["included"]
    for p in data["parts"]:
        assert p["text"].startswith(f"== {p['label']} ==")
        assert p["bytes"] == len(p["text"].encode())
        assert "—" not in p["text"] and "–" not in p["text"]
    assert data["filename"] == "soulsync-diagnostics-3.6.7-2026-11-17.txt"
    assert data["version"] == "3.6.7+d7e7541"
    assert data["text"].startswith("SoulSync diagnostics, 2026-11-17 21:42 UTC")
    assert data["bytes"] == len(data["text"].encode())


@pytest.mark.parametrize("off", list(bundle.DEFAULT_PARTS))
def test_switching_a_part_off_removes_it_from_the_text(off):
    on = [p for p in bundle.DEFAULT_PARTS if p != off]
    data = bundle.build(parts=on, note="it broke", where="Downloads, Music", now=NOW)
    parts = _parts(data)
    assert parts[off]["text"] not in data["text"]
    for pid in on:
        assert parts[pid]["text"] in data["text"]
    # the UI's join rule gives the same string as the server
    header = data["text"].splitlines()[0]
    assert bundle.join(header, "it broke", "Downloads, Music", data["parts"]) == data["text"]


def test_parts_as_a_comma_string_or_a_dict():
    a = bundle.build(parts="system,paths", now=NOW)
    b = bundle.build(parts={"system": True, "paths": True, "logs": False}, now=NOW)
    assert a["text"] == b["text"]
    assert "== Folder paths ==" in a["text"] and "== Settings" not in a["text"]
    assert bundle.build(parts="bogus", now=NOW)["text"].strip() == "SoulSync diagnostics, 2026-11-17 21:42 UTC"


def test_services_carry_health():
    text = _parts(bundle.build(now=NOW))["services"]["text"]
    assert "Health: 1 errors, 1 warnings" in text
    assert "[error] Jellyfin isn't answering" in text
    assert "Transfer folder writable" not in text          # ok findings stay out
    assert "Paused        lastfm" in text or "lastfm" in text


def test_paths_part():
    text = _parts(bundle.build(now=NOW))["paths"]["text"]
    assert "/downloads  exists, not writable" in text
    assert "Library 1" in text and "/music  found" in text


def test_logs_part_and_line_count(app_log):
    data = bundle.build(lines=10, now=NOW)
    logs = _parts(data)["logs"]
    assert logs["label"] == "Last 10 log lines"
    body = logs["text"].splitlines()[1:]
    assert len(body) == 9 and body[-1].endswith("just info")


def test_recent_errors_only_the_last_day():
    text = _parts(bundle.build(parts=["errors"], now=NOW))["errors"]["text"]
    assert "an old error" not in text and "just info" not in text
    assert text.count(" - ERROR - ") == len(PLANTED_LOG)


def test_a_part_that_fails_says_so_and_the_rest_build():
    def boom():
        raise RuntimeError("x")
    bundle.configure(redacted_config=boom)
    data = bundle.build(now=NOW)
    assert "(couldn't read the settings)" in _parts(data)["settings"]["text"]
    assert "== Version and platform ==" in data["text"]


# ---------------------------------------------------------------- sharing
def test_github_url_stays_under_the_cap():
    # 4000 characters that each take 6 in a url: far past the cap untrimmed
    data = bundle.build(note="\u00e9" * 20000, where="Downloads", now=NOW)
    gh = data["github"]
    assert gh["length"] == len(gh["url"]) <= bundle.GITHUB_URL_CAP
    assert gh["trimmed"] is True
    assert gh["url"].startswith("https://github.com/Nezreka/SoulSync/issues/new?")
    assert "template=bug_report.yml" in gh["url"] and "version=3.6.7" in gh["url"]
    short = bundle.build(note="Lateralus never shows up\nmore detail", now=NOW)["github"]
    assert short["trimmed"] is False and "title=Lateralus%20never%20shows%20up" in short["url"]


def test_issue_form_has_the_fields_the_url_fills():
    import yaml
    form = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/ISSUE_TEMPLATE/bug_report.yml")
                          .read_text(encoding="utf-8"))
    ids = {item.get("id") for item in form["body"]}
    url = bundle.build(note="n", where="w", now=NOW)["github"]["url"]
    for field in ("what-happened", "where", "version", "platform", "diagnostics"):
        assert field in ids
        assert f"{field}=" in url


def test_discord_fits_one_message():
    data = bundle.build(note="y" * 5000, where="Downloads", now=NOW)
    assert len(data["discord"]) <= bundle.DISCORD_CAP
    assert data["discord"].startswith("**SoulSync 3.6.7+d7e7541** on Linux 6.6.87, Docker")
    assert "Health: 1 errors, 1 warnings" in data["discord"]


# ---------------------------------------------------------------- routes
@pytest.fixture
def client(monkeypatch):
    import api.help as help_api
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(help_api.create_blueprint())
    return app.test_client()


def test_routes_get_post_and_download(client):
    got = client.get("/api/help/bundle?parts=system,paths&note=hello").get_json()
    assert "== Folder paths ==" in got["text"] and "hello" in got["text"]
    posted = client.post("/api/help/bundle", json={"parts": ["system"], "note": "long note", "lines": 50}).get_json()
    assert "long note" in posted["text"] and "== Folder paths ==" not in posted["text"]
    resp = client.post("/api/help/bundle", json={"parts": ["system"], "download": 1})
    assert resp.mimetype == "text/plain"
    assert 'filename="soulsync-diagnostics-3.6.7-' in resp.headers["Content-Disposition"]
    assert resp.get_data(as_text=True).startswith("SoulSync diagnostics,")


def test_a_member_is_refused(client, monkeypatch):
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    assert client.get("/api/help/bundle").status_code == 403
    assert client.post("/api/help/bundle", json={}).status_code == 403
    assert client.get("/api/help/bundle?download=1").status_code == 403


# ---------------------------------------------------------------- copy debug info
def test_debug_info_log_lines_are_redacted_and_tailed(app_log, monkeypatch):
    import core.debug_info as di
    monkeypatch.setattr(di, "_status_cache", {})
    monkeypatch.setattr(di, "_log_path", str(app_log))
    monkeypatch.setattr(di, "_log_dir", app_log.parent)
    monkeypatch.setattr(di, "get_database", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    info = di.collect_debug_info(log_lines=5, log_source="app")
    assert len(info["recent_logs"]) == 5
    joined = "\n".join(info["recent_logs"])
    for needle in LOG_NEEDLES:
        assert needle not in joined, needle
    assert info["available_logs"][0]["file"] == "app.log"
