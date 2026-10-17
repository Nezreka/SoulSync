"""The server descriptors are the one place the server list and each
server's config shape live. These tests pin that: the registry and the
descriptors agree on the server names, the "is configured" rule matches
what settings.py used to spell out per server, and the UI payload never
carries a secret value."""

from __future__ import annotations

import pytest

from core.media_server import specs


class _Cfg:
    """Minimal stand-in for config_manager: dotted get over a dict."""

    def __init__(self, data, active='plex'):
        self._data = data
        self._active = active

    def get(self, key, default=None):
        cur = self._data
        for part in key.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def get_active_media_server(self):
        return self._active


def test_descriptor_names_match_registry():
    from core.media_server.registry import build_default_registry

    assert specs.names() == build_default_registry().names()


def test_every_descriptor_has_a_display_name_and_brand():
    for d in specs.iter_descriptors():
        assert d.display_name
        assert d.brand is not None and d.brand.color and d.brand.logo


@pytest.mark.parametrize('name,config,expected', [
    ('plex', {'base_url': 'http://p:32400', 'token': 't'}, True),
    ('plex', {'base_url': 'http://p:32400', 'token': ''}, False),
    ('jellyfin', {'base_url': 'http://j:8096', 'api_key': 'k'}, True),
    ('jellyfin', {'base_url': '', 'api_key': 'k'}, False),
    ('navidrome', {'base_url': 'http://n:4533', 'username': 'u', 'password': 'p'}, True),
    ('navidrome', {'base_url': 'http://n:4533', 'username': 'u'}, False),
    ('soulsync', {}, True),
])
def test_is_configured_matches_the_old_per_server_rules(name, config, expected):
    assert specs.describe(name).is_configured(config) is expected


def test_config_from_returns_the_section_or_the_borrowed_keys():
    cfg = _Cfg({
        'plex': {'base_url': 'http://p', 'token': 't', 'extra': 1},
        'soulseek': {'transfer_path': '/x', 'slskd_url': 'http://s'},
    })
    assert specs.describe('plex').config_from(cfg) == {'base_url': 'http://p', 'token': 't', 'extra': 1}
    # The standalone library borrows one key from the soulseek section
    # and gets its default when the key is absent.
    assert specs.describe('soulsync').config_from(cfg) == {'transfer_path': '/x'}
    assert specs.describe('soulsync').config_from(_Cfg({})) == {'transfer_path': './Transfer'}


def test_describe_is_case_insensitive_and_none_safe():
    assert specs.describe('Plex') is specs.PLEX
    assert specs.describe(None) is None
    assert specs.describe('emby') is None
    assert specs.is_known('navidrome') and not specs.is_known('emby')


def test_flag_queries():
    assert specs.names_where(supports_own_libraries=True) == ['plex', 'jellyfin']
    assert specs.names_where(per_user_accounts=True) == ['plex', 'jellyfin', 'navidrome']
    assert specs.external_names() == ['plex', 'jellyfin', 'navidrome']


def test_public_dict_carries_field_kinds_but_no_values():
    d = specs.describe('navidrome').to_public_dict()
    kinds = {f['key']: f['kind'] for f in d['fields']}
    assert kinds['password'] == 'password' and kinds['base_url'] == 'url'
    flat = str(d)
    for forbidden in ('value', 'http://', 'secret'):
        assert forbidden not in flat
    assert d['detection'] == {'port': 4533, 'path': '/rest/ping'}


def test_detection_ports_are_the_ones_connection_detect_probes():
    from core.connection_detect import default_port

    assert default_port('plex') == 32400
    assert default_port('jellyfin') == 8096
    assert default_port('navidrome') == 4533
    assert default_port('slskd') == 5030


def test_settings_validation_goes_through_the_descriptors(monkeypatch):
    from core.settings import ConfigManager

    cm = ConfigManager.__new__(ConfigManager)
    data = {
        'active_media_server': 'navidrome',
        'navidrome': {'base_url': 'http://n', 'username': 'u', 'password': 'p'},
        'spotify': {'client_id': 'a', 'client_secret': 'b'},
        'soulseek': {'slskd_url': 'http://s', 'transfer_path': './T'},
    }
    cm.get = _Cfg(data).get
    assert cm.get_active_media_server() == 'navidrome'
    assert cm.get_active_media_server_config() == data['navidrome']
    assert cm.is_configured() is True
    v = cm.validate_config()
    assert v['navidrome'] is True and v['plex'] is False and v['soulsync'] is True
    assert v['active_media_server'] == 'navidrome'
    with pytest.raises(ValueError):
        cm.set_active_media_server('emby')


def test_media_servers_endpoint_lists_every_server_without_secrets():
    from flask import Flask

    from api import media_servers

    cm = _Cfg({'plex': {'base_url': 'http://p', 'token': 'sekrit'}}, active='plex')
    media_servers.configure(config_manager_=cm, engine_getter=lambda: None)
    app = Flask(__name__)
    app.register_blueprint(media_servers.create_blueprint())
    body = app.test_client().get('/api/media-servers').get_json()

    assert body['active'] == 'plex'
    assert [s['name'] for s in body['servers']] == specs.names()
    plex = body['servers'][0]
    assert plex['configured'] is True and plex['active'] is True and plex['connected'] is None
    assert 'sekrit' not in str(body)
