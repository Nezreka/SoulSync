"""The Last.fm username has to be editable somewhere.

Reported as #1241: a user typed their username wrong on the Stats page and had
no way to correct it. The Stats page asks for it once — `needsUsername` is false
as soon as a username is stored, WRONG or not, so the input disappears
permanently — and it lived nowhere else in the app.

The fix is where the reporter suggested: beside the API key and secret, which is
where every other Last.fm credential already lives.

These read the real files rather than a running app, because the failure was
never in behaviour: the field simply did not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="ignore")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8", errors="ignore")


def _strip_comments(text: str) -> str:
    """A guard must never pass on the text of a comment explaining it."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_the_field_exists_on_the_settings_page():
    assert 'id="lastfm-username"' in _INDEX


def test_it_sits_in_the_lastfm_service_card():
    """Next to the key and secret — anywhere else and it is lost again."""
    card = _INDEX.split('data-service="lastfm"', 1)[1].split("</div>\n                                </div>", 1)[0]
    assert 'id="lastfm-username"' in card
    assert 'id="lastfm-api-key"' in card


def test_the_saved_value_is_read_back_into_the_field():
    """Without the load half the field is blank every time settings open, and
    saving then wipes the stored username."""
    js = _strip_comments(_SETTINGS_JS)
    assert "settings.lastfm?.username" in js


def test_the_save_uses_cfgstr_not_a_raw_value_read():
    """_cfgStr returns undefined for a missing element, and JSON.stringify drops
    an undefined key. A raw .value read on a field that is not on the page is
    how a save writes '' over a stored setting — the settings-wipe class."""
    js = _strip_comments(_SETTINGS_JS)
    assert re.search(r"username:\s*_cfgStr\('lastfm-username'", js), (
        "the username must be collected with _cfgStr"
    )
    assert not re.search(r"username:\s*document\.getElementById\('lastfm-username'\)\.value", js)


def test_the_key_path_is_the_one_the_importer_reads():
    """The field is useless if it writes a key nothing consumes.

    The settings POST writes `<section>.<key>`, so a `username` key inside the
    `lastfm` section becomes `lastfm.username` — which is exactly what the
    importer and the automation handler ask for.
    """
    importer = (_ROOT / "core" / "listening_import" / "lastfm.py").read_text(encoding="utf-8")
    handler = (_ROOT / "core" / "automation" / "handlers" / "lastfm_import.py").read_text(encoding="utf-8")
    assert 'get("lastfm.username"' in importer
    assert 'get("lastfm.username"' in handler


def test_lastfm_is_a_section_the_server_persists():
    """A section missing from the server's list is accepted and silently
    dropped — the class test_settings_persist_whitelist.py exists for."""
    server = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="ignore")
    line = next(ln for ln in server.splitlines() if "for service in [" in ln)
    assert "'lastfm'" in line


def test_status_returns_corrected_configuration_instead_of_failed_username(monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    import api.stats as stats
    monkeypatch.setattr(stats, "config_manager", SimpleNamespace(get=lambda key, default=None: {"lastfm.username": "corrected", "lastfm.api_key": "key"}.get(key, default)))
    monkeypatch.setattr(stats, "_lastfm_import_worker", lambda: SimpleNamespace(status=lambda: {"username": "k", "status": "error"}))
    monkeypatch.setattr(stats, "_automation_engine", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(stats.bp)
    response = app.test_client().get("/api/lastfm/listening-import/status")
    assert response.status_code == 200
    assert response.get_json()["username"] == "corrected"
