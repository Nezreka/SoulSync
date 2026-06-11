"""Settings-secret redaction for GET /api/settings (#832 follow-up).

GET /api/settings used to ship the DECRYPTED config to the browser — every API
key, token, and password in cleartext. redacted_config() masks configured
secrets with a sentinel; set() refuses to let that round-tripped sentinel
overwrite the real value. Together: secrets never reach the client, and saving
an unchanged form never clobbers them.

ConfigManager.__init__ touches the DB, so these build instances via __new__ and
set config_data directly — the methods under test only read/write that dict.
"""

from __future__ import annotations

from config.settings import ConfigManager

S = ConfigManager.REDACTED_SENTINEL


def _cm(config_data):
    cm = ConfigManager.__new__(ConfigManager)
    cm.config_data = config_data
    cm._save_config = lambda: None
    return cm


# ── redacted_config: secrets out, everything else intact ────────────────────

def test_configured_secrets_are_masked():
    cm = _cm({'spotify': {'client_secret': 'REAL', 'redirect_uri': 'http://x'},
              'plex': {'token': 'PLEXTOK', 'url': 'http://plex'}})
    r = cm.redacted_config()
    assert r['spotify']['client_secret'] == S
    assert r['plex']['token'] == S
    # Non-secret siblings pass through untouched.
    assert r['spotify']['redirect_uri'] == 'http://x'
    assert r['plex']['url'] == 'http://plex'


def test_unset_secret_stays_empty_not_masked():
    # An empty secret must NOT become the sentinel — the UI shows "not set".
    cm = _cm({'jellyfin': {'api_key': ''}, 'navidrome': {'password': None}})
    r = cm.redacted_config()
    assert r['jellyfin']['api_key'] == ''
    assert r['navidrome']['password'] is None


def test_dict_valued_secret_is_masked():
    # OAuth session blobs (tidal/qobuz) collapse to the sentinel.
    cm = _cm({'tidal_download': {'session': {'access': 'A', 'refresh': 'R'}}})
    assert cm.redacted_config()['tidal_download']['session'] == S


def test_redaction_does_not_mutate_live_config():
    cm = _cm({'spotify': {'client_secret': 'REAL'}})
    cm.redacted_config()
    assert cm.config_data['spotify']['client_secret'] == 'REAL'


def _put(cfg, path, value):
    parent = cfg
    keys = path.split('.')
    for k in keys[:-1]:
        parent = parent.setdefault(k, {})
    parent[keys[-1]] = value


def _at(cfg, path):
    cur = cfg
    for k in path.split('.'):
        cur = cur[k]
    return cur


def test_every_sensitive_path_is_masked():
    # Put a value at every sensitive path (any depth) — none may survive in clear.
    cfg = {}
    for path in ConfigManager._SENSITIVE_PATHS:
        _put(cfg, path, 'VALUE')
    r = _cm(cfg).redacted_config()
    leaked = [p for p in ConfigManager._SENSITIVE_PATHS if _at(r, p) != S]
    assert leaked == [], f"secrets shipped in cleartext: {leaked}"


# ── set() guard: the sentinel can never overwrite a real secret ─────────────

def test_sentinel_roundtrip_keeps_existing_secret():
    cm = _cm({'spotify': {'client_secret': 'REAL'}})
    cm.set('spotify.client_secret', S)            # untouched masked field saved
    assert cm.config_data['spotify']['client_secret'] == 'REAL'


def test_real_value_overwrites():
    cm = _cm({'spotify': {'client_secret': 'REAL'}})
    cm.set('spotify.client_secret', 'NEW')
    assert cm.config_data['spotify']['client_secret'] == 'NEW'


def test_empty_value_clears_secret():
    # Deliberately clearing a secret must still work (empty != sentinel).
    cm = _cm({'spotify': {'client_secret': 'REAL'}})
    cm.set('spotify.client_secret', '')
    assert cm.config_data['spotify']['client_secret'] == ''


def test_sentinel_on_non_secret_path_writes_normally():
    # The guard is scoped to sensitive paths — a literal sentinel elsewhere is
    # a normal write (absurd in practice, but proves the guard isn't global).
    cm = _cm({'ui_appearance': {'theme': 'dark'}})
    cm.set('ui_appearance.theme', S)
    assert cm.config_data['ui_appearance']['theme'] == S
