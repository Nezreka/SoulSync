"""Regression tests for QobuzClient.reload_credentials.

Discord-reported (Foxxify): logging in via the Qobuz Connect button on
Settings showed "Connected: <username> (Active)" but underneath an error
"Qobuz not authenticated...", and the dashboard indicator stayed
yellow even after a successful login.

Root cause: SoulSync runs two QobuzClient instances side by side — one
through ``download_orchestrator.client('qobuz')`` for the auth-flow endpoints, and a
second owned by the enrichment worker thread for thread safety. Login
only updated the first instance's in-memory state. The dashboard's
"configured" check (and the connection-test step) read the worker
instance, which still believed itself unauthenticated until the next
process restart.

The fix adds ``QobuzClient.reload_credentials()`` — a public,
network-free method that re-reads the saved session from config and
updates the instance's in-memory state + session headers. Called from
the auth login / token / logout endpoints to keep the worker instance
in lockstep with the auth instance.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# Stubs for heavyweight dependencies that QobuzClient pulls in at import time
# ---------------------------------------------------------------------------


@pytest.fixture
def qobuz_client_module():
    """Import core.qobuz_client with config_manager stubbed to a mutable
    in-memory dict so we can drive `qobuz.session` from the test.

    Snapshots and restores sys.modules entries on teardown — without
    this, every downstream test that imports config.settings would
    receive our stub and the real config_manager.get would no longer
    reach the live config (which breaks tests like
    test_tidal_auth_instructions that monkeypatch config_manager.get
    directly).
    """
    config_state: Dict[str, Any] = {}

    class _StubConfigManager:
        def get(self, key, default=None):
            cur: Any = config_state
            for part in key.split('.'):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return default
            return cur

        def set(self, key, value):
            cur: Any = config_state
            parts = key.split('.')
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value

    # Snapshot what we are about to mutate so teardown can put it back.
    original_modules = {
        name: sys.modules.get(name)
        for name in ('config', 'config.settings', 'core.qobuz_client')
    }

    if 'config' not in sys.modules:
        sys.modules['config'] = types.ModuleType('config')
    settings_mod = types.ModuleType('config.settings')
    settings_mod.config_manager = _StubConfigManager()
    sys.modules['config.settings'] = settings_mod

    sys.modules.pop('core.qobuz_client', None)
    try:
        import core.qobuz_client as qobuz_client_module
        yield qobuz_client_module, config_state
    finally:
        # Restore each entry — set back to original, or pop if it didn't
        # exist beforehand. This protects every downstream test that
        # imports any of these modules.
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture
def fresh_client(qobuz_client_module):
    """A QobuzClient instance with no saved session — the constructor's
    ``_restore_session()`` call is a no-op when config is empty."""
    module, _config = qobuz_client_module
    return module.QobuzClient()


# ---------------------------------------------------------------------------
# reload_credentials — populates an empty client from saved config
# ---------------------------------------------------------------------------


class TestReloadCredentialsPopulatesFromConfig:
    def test_picks_up_token_app_id_and_app_secret(self, qobuz_client_module, fresh_client):
        _module, config = qobuz_client_module
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        # Initial state — empty (constructor saw empty config).
        assert fresh_client.user_auth_token is None
        assert fresh_client.app_id is None
        assert fresh_client.app_secret is None

        fresh_client.reload_credentials()

        assert fresh_client.user_auth_token == 'TOKEN-1'
        assert fresh_client.app_id == 'APP-1'
        assert fresh_client.app_secret == 'SECRET-1'

    def test_session_headers_get_set(self, qobuz_client_module, fresh_client):
        _module, config = qobuz_client_module
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        fresh_client.reload_credentials()
        assert fresh_client.session.headers.get('X-App-Id') == 'APP-1'
        assert fresh_client.session.headers.get('X-User-Auth-Token') == 'TOKEN-1'

    def test_authenticated_after_reload(self, qobuz_client_module, fresh_client):
        _module, config = qobuz_client_module
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        fresh_client.reload_credentials()
        assert fresh_client.is_authenticated() is True


# ---------------------------------------------------------------------------
# reload_credentials — clears state when config is wiped (logout path)
# ---------------------------------------------------------------------------


class TestReloadCredentialsClearsOnEmptyConfig:
    def test_clears_token_app_id_and_app_secret(self, qobuz_client_module, fresh_client):
        _module, config = qobuz_client_module
        # Pre-populate
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        fresh_client.reload_credentials()
        assert fresh_client.user_auth_token == 'TOKEN-1'

        # Simulate logout — config wiped
        config['qobuz']['session'] = {}
        fresh_client.reload_credentials()

        assert fresh_client.user_auth_token is None
        assert fresh_client.app_id is None
        assert fresh_client.app_secret is None

    def test_session_headers_get_cleared(self, qobuz_client_module, fresh_client):
        _module, config = qobuz_client_module
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        fresh_client.reload_credentials()
        assert 'X-User-Auth-Token' in fresh_client.session.headers

        config['qobuz']['session'] = {}
        fresh_client.reload_credentials()
        assert 'X-User-Auth-Token' not in fresh_client.session.headers
        assert 'X-App-Id' not in fresh_client.session.headers

    def test_user_info_reset_when_token_cleared(self, qobuz_client_module, fresh_client):
        """When the token gets cleared, stale user_info should not survive
        — otherwise downstream code could think a user is still attached
        to an unauthenticated instance."""
        _module, config = qobuz_client_module
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        fresh_client.reload_credentials()
        fresh_client.user_info = {'display_name': 'someone'}

        config['qobuz']['session'] = {}
        fresh_client.reload_credentials()
        assert fresh_client.user_info is None

    def test_not_authenticated_after_clear(self, qobuz_client_module, fresh_client):
        _module, config = qobuz_client_module
        config['qobuz'] = {
            'session': {
                'app_id': 'APP-1',
                'app_secret': 'SECRET-1',
                'user_auth_token': 'TOKEN-1',
            }
        }
        fresh_client.reload_credentials()
        config['qobuz']['session'] = {}
        fresh_client.reload_credentials()
        assert fresh_client.is_authenticated() is False


# ---------------------------------------------------------------------------
# reload_credentials — defensive against missing config keys
# ---------------------------------------------------------------------------


class TestReloadCredentialsDefensive:
    def test_no_qobuz_key_at_all_clears_state(self, qobuz_client_module, fresh_client):
        _module, _config = qobuz_client_module
        # Set then tear down so there's nothing in config
        fresh_client.app_id = 'X'
        fresh_client.user_auth_token = 'Y'
        fresh_client.reload_credentials()
        assert fresh_client.app_id is None
        assert fresh_client.user_auth_token is None

    def test_partial_session_doesnt_crash(self, qobuz_client_module, fresh_client):
        """If only token is in config but app_id/secret missing, no crash —
        client just isn't authenticated."""
        _module, config = qobuz_client_module
        config['qobuz'] = {'session': {'user_auth_token': 'TOKEN-1'}}
        fresh_client.reload_credentials()
        assert fresh_client.user_auth_token == 'TOKEN-1'
        assert fresh_client.app_id is None
        assert fresh_client.is_authenticated() is False


# ---------------------------------------------------------------------------
# Sync scenario — the actual reported bug
# ---------------------------------------------------------------------------


class TestTwoInstanceSync:
    """Reproduce the Foxxify scenario: instance A logs in (writes to
    config), instance B reads stale state until reload_credentials is
    called."""

    def test_second_instance_unaware_until_reload(self, qobuz_client_module):
        module, config = qobuz_client_module
        instance_a = module.QobuzClient()
        instance_b = module.QobuzClient()

        # Instance A "logs in" — directly mutate the way login() would
        instance_a.app_id = 'APP-A'
        instance_a.app_secret = 'SECRET-A'
        instance_a.user_auth_token = 'TOKEN-A'
        instance_a._save_session()

        # Instance B is still in the dark.
        assert instance_b.user_auth_token is None
        assert instance_b.is_authenticated() is False

        # Sync.
        instance_b.reload_credentials()

        assert instance_b.user_auth_token == 'TOKEN-A'
        assert instance_b.is_authenticated() is True
        assert instance_b.session.headers.get('X-User-Auth-Token') == 'TOKEN-A'
