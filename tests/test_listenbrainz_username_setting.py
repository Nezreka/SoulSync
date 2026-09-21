"""The ListenBrainz username has to be editable in settings or auto-detected.

Parity with Last.fm (#1241) and issue #1264: allows configuring or overriding
the ListenBrainz username in Settings > Services > ListenBrainz, and exposes it
to the listening importer and stats API.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="ignore")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8", errors="ignore")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_the_field_exists_on_the_settings_page():
    assert 'id="listenbrainz-username"' in _INDEX


def test_it_sits_in_the_listenbrainz_service_card():
    card = _INDEX.split('data-service="listenbrainz"', 1)[1].split("</div>\n                                        </div>", 1)[0]
    assert 'id="listenbrainz-username"' in card
    assert 'id="listenbrainz-token"' in card


def test_the_saved_value_is_read_back_into_the_field():
    js = _strip_comments(_SETTINGS_JS)
    assert "settings.listenbrainz?.username" in js


def test_the_save_uses_cfgstr_not_a_raw_value_read():
    js = _strip_comments(_SETTINGS_JS)
    assert re.search(r"username:\s*_cfgStr\('listenbrainz-username'", js), (
        "the username must be collected with _cfgStr"
    )
    assert not re.search(r"username:\s*document\.getElementById\('listenbrainz-username'\)\.value", js)


def test_the_key_path_is_the_one_the_importer_reads():
    importer = (_ROOT / "core" / "listening_import" / "listenbrainz.py").read_text(encoding="utf-8")
    handler = (_ROOT / "core" / "automation" / "handlers" / "listenbrainz_import.py").read_text(encoding="utf-8")
    assert 'get("listenbrainz.username"' in importer
    assert 'get("listenbrainz.username"' in handler


def test_listenbrainz_is_a_section_the_server_persists():
    server = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="ignore")
    line = next(ln for ln in server.splitlines() if "for service in [" in ln)
    assert "'listenbrainz'" in line


def test_status_returns_configured_username_when_present(monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    import api.stats as stats
    monkeypatch.setattr(stats, "config_manager", SimpleNamespace(get=lambda key, default=None: {"listenbrainz.username": "lbuser", "listenbrainz.token": "tok"}.get(key, default)))
    monkeypatch.setattr(stats, "_listenbrainz_import_worker", lambda: SimpleNamespace(status=lambda: {"username": "old_user", "status": "idle"}))
    monkeypatch.setattr(stats, "_automation_engine", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(stats.bp)
    response = app.test_client().get("/api/listenbrainz/listening-import/status")
    assert response.status_code == 200
    assert response.get_json()["username"] == "lbuser"
