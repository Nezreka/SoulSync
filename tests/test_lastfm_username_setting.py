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
# the card is React (SETTINGS_PORT.md step 10b): connections-extras.tsx
_CARDS = (_ROOT / "webui" / "src" / "features" / "settings" / "tabs" / "connections-extras.tsx").read_text(
    encoding="utf-8", errors="ignore"
)
_CARD = _CARDS[_CARDS.index("function LastfmCard(") :]
_CARD = _CARD[: _CARD.index("\n}\n")]


def _strip_comments(text: str) -> str:
    """A guard must never pass on the text of a comment explaining it."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_the_field_exists_on_the_settings_page():
    assert 'id="lastfm-username"' in _CARD


def test_it_sits_in_the_lastfm_service_card():
    """Next to the credentials: anywhere else and it is lost again."""
    assert 'service="lastfm"' in _CARD
    assert 'id="lastfm-username"' in _CARD
    assert 'id="lastfm-api-key"' in _CARD


def test_the_saved_value_is_read_back_into_the_field():
    """Without the load half the field is blank every time settings open."""
    assert "useTrimmed(store, 'lastfm', 'username')" in _strip_comments(_CARD)


def test_the_save_sends_only_what_was_typed():
    """The settings-wipe class: a save that reads a field off a page that does
    not have it writes '' over the stored value. The card saves this key only
    when it is typed in, trimmed as the page's _cfgStr did."""
    trimmed = _CARDS[_CARDS.index("function useTrimmed(") :]
    trimmed = trimmed[: trimmed.index("\n}\n")]
    assert "store.set(section, key, shape(raw.trim()))" in trimmed
    assert "getElementById" not in _CARD


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
