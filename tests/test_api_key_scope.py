"""Read-scope API keys (#1586).

A key is "admin" (everything, as before) or "read". A record with no scope is an
older key and stays admin; a scope the code doesn't know is read. A read key may
GET under /api/v1, minus settings, key listings and profiles. Everything else is
403, whichever gate would have let it through.

The last section is the guard the maintainer asked for: a read key reaches every
allowlisted GET, so a GET that changes something is a hole.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import sqlite3
import tempfile
import textwrap

import pytest
from flask import Flask, g

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-key-scope-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'key-scope.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from api import auth, key_store  # noqa: E402
from api.auth import (  # noqa: E402
    READ_KEY_DENIED_PREFIXES,
    SCOPE_ADMIN,
    SCOPE_READ,
    key_scope,
    read_key_may_access,
)

web_server = pytest.importorskip('web_server')


def _key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


READ_RAW, ADMIN_RAW, LEGACY_RAW, ODD_RAW = (
    'sk_test-read', 'sk_test-admin', 'sk_test-legacy', 'sk_test-odd')
KEYS = [
    {'id': 'r', 'key_hash': _key(READ_RAW), 'label': 'dash', 'scope': 'read'},
    {'id': 'a', 'key_hash': _key(ADMIN_RAW), 'label': 'bot', 'scope': 'admin'},
    {'id': 'l', 'key_hash': _key(LEGACY_RAW), 'label': 'old'},               # no scope
    {'id': 'o', 'key_hash': _key(ODD_RAW), 'label': 'odd', 'scope': 'root'},  # unknown
]
READ = {'X-API-Key': READ_RAW}
ADMIN = {'X-API-Key': ADMIN_RAW}
SENSITIVE_V1 = [
    '/api/v1/settings',
    '/api/v1/api-keys',
    '/api/v1/api-keys-internal',
    '/api/v1/profiles',
    '/api/v1/profiles/1',
    '/api/v1/video/calendar',
]


# ---- the scope rules ---------------------------------------------------------

def test_missing_scope_is_admin_and_unknown_scope_is_read():
    assert key_scope({'id': 'x'}) == SCOPE_ADMIN
    assert key_scope({'scope': 'admin'}) == SCOPE_ADMIN
    assert key_scope({'scope': 'read'}) == SCOPE_READ
    assert key_scope({'scope': 'root'}) == SCOPE_READ
    assert key_scope({'scope': ''}) == SCOPE_READ
    assert key_scope(None) == SCOPE_ADMIN


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'OPTIONS'])
def test_read_key_may_fetch_v1_data(method):
    assert read_key_may_access(method, '/api/v1/library/stats')
    assert read_key_may_access(method, '/api/v1/library/stats/')


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_read_key_may_not_write(method):
    assert not read_key_may_access(method, '/api/v1/library/stats')


@pytest.mark.parametrize('path', SENSITIVE_V1 + [
    '/api/v1/api-keys/abc', '/api/v1/settings/', '/api/v1/profiles/1/anything'])
def test_read_key_may_not_read_sensitive_v1(path):
    assert not read_key_may_access('GET', path)


@pytest.mark.parametrize('path', [
    '/api/status', '/api/settings', '/api/library/artists', '/', '/static/x.js',
    '/api/v1', '/api/v1x/library/stats', '/api/v10/library/stats'])
def test_read_key_is_refused_outside_api_v1(path):
    assert not read_key_may_access('GET', path)


@pytest.mark.parametrize('path', [
    '//api/v1/library/stats', '/api/v1//settings', '/api/v1/library/../settings',
    '/api/v1/./settings', '/api/v1/library/./stats'])
def test_read_key_is_refused_on_paths_that_are_not_in_plain_form(path):
    assert not read_key_may_access('GET', path)


def test_new_keys_carry_a_scope_and_bad_ones_are_refused():
    class Cfg:
        def __init__(self):
            self.d = {}

        def get(self, k, default=None):
            return self.d.get(k, default)

        def set(self, k, v):
            self.d[k] = v

    cfg = Cfg()
    raw, rec = key_store.create_key(cfg, 'a')
    assert rec['scope'] == 'admin'
    raw, rec = key_store.create_key(cfg, 'r', 'read')
    assert rec['scope'] == 'read'
    assert key_store.created_view(raw, rec)['scope'] == 'read'
    with pytest.raises(ValueError):
        key_store.create_key(cfg, 'x', 'root')
    assert [k['scope'] for k in key_store.list_keys(cfg)] == ['admin', 'read']
    # an old record lists as admin, matching what it can do
    cfg.d['api_keys'].append({'id': 'old', 'key_hash': 'h', 'label': 'old'})
    assert key_store.list_keys(cfg)[-1]['scope'] == 'admin'


def test_a_read_key_context_has_no_admin_or_download_rights():
    app = Flask(__name__)
    with app.test_request_context('/'):
        auth._stamp_admin_context(scope=SCOPE_READ)
        assert g.is_admin is False and g.can_download is False
    with app.test_request_context('/'):
        auth._stamp_admin_context(scope=SCOPE_ADMIN)
        assert g.is_admin is True and g.can_download is True


# ---- through the real app and its gates ---------------------------------------

@pytest.fixture
def keys(monkeypatch):
    """The four test keys, held in memory so nothing reaches the real config."""
    store = [dict(k) for k in KEYS]
    real_get = web_server.config_manager.get

    def fake_get(name, default=None):
        if name == 'api_keys':
            return store
        return real_get(name, default)

    def fake_set(name, value, *a, **kw):
        if name == 'api_keys':
            store[:] = value

    monkeypatch.setattr(web_server.config_manager, 'get', fake_get)
    monkeypatch.setattr(web_server.config_manager, 'set', fake_set)
    with auth._usage_lock:
        auth._last_persisted_usage.update({k['key_hash']: auth.datetime.now(auth.timezone.utc) for k in KEYS})
    yield
    with auth._usage_lock:
        auth._last_persisted_usage.clear()


@pytest.fixture
def login_mode(monkeypatch, keys):
    fake_get = web_server.config_manager.get

    def get(name, default=None):
        if name == 'security.require_login':
            return True
        return fake_get(name, default)

    monkeypatch.setattr(web_server.config_manager, 'get', get)


@pytest.fixture
def client():
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


def _refused_for_scope(resp):
    # parsed by hand: an after_request hook relabels some responses (/sw.js is
    # text/javascript), which makes get_json() return nothing
    try:
        code = json.loads(resp.data)['error']['code']
    except (ValueError, KeyError, TypeError):
        return False
    return resp.status_code == 403 and code == 'INSUFFICIENT_SCOPE'


def test_read_key_can_fetch_library_stats(client, keys):
    resp = client.get('/api/v1/library/stats', headers=READ)
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True


@pytest.mark.parametrize('headers,query', [
    ({'Authorization': f'Bearer {READ_RAW}'}, ''),
    ({'X-API-Key': READ_RAW}, ''),
    ({}, f'?api_key={READ_RAW}'),
])
def test_every_key_transport_is_held_to_the_scope(client, keys, headers, query):
    assert client.get('/api/v1/library/stats' + query, headers=headers).status_code == 200
    assert _refused_for_scope(client.get('/api/v1/settings' + query, headers=headers))
    assert _refused_for_scope(client.post('/api/v1/wishlist' + query, headers=headers, json={}))


@pytest.mark.parametrize('path', SENSITIVE_V1)
def test_sensitive_gets_are_closed_to_read_keys_and_open_to_admin(client, keys, path):
    assert _refused_for_scope(client.get(path, headers=READ))
    assert client.get(path, headers=ADMIN).status_code not in (401, 403)


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/v1/wishlist'),
    ('PATCH', '/api/v1/settings'),
    ('DELETE', '/api/v1/api-keys/a'),
    ('POST', '/api/v1/api-keys'),
    ('POST', '/api/v1/api-keys-internal/generate'),
    ('DELETE', '/api/v1/api-keys-internal/revoke/a'),
    ('POST', '/api/profiles/invites'),
    ('PUT', '/api/library/clear-match'),
    ('POST', '/api/database/maintenance/vacuum'),
])
def test_read_key_cannot_write_anything(client, keys, method, path):
    assert _refused_for_scope(client.open(path, method=method, headers=READ, json={}))


@pytest.mark.parametrize('path', ['/api/settings', '/api/status', '/api/library/artists',
                                  '/api/image-proxy'])
def test_read_key_is_closed_out_of_non_v1_routes(client, keys, path):
    assert _refused_for_scope(client.get(path, headers=READ))


def test_legacy_key_without_scope_still_acts_as_admin(client, keys):
    assert client.get('/api/v1/settings', headers={'X-API-Key': LEGACY_RAW}).status_code == 200


def test_unknown_scope_is_held_to_read(client, keys):
    odd = {'X-API-Key': ODD_RAW}
    assert client.get('/api/v1/library/stats', headers=odd).status_code == 200
    assert _refused_for_scope(client.get('/api/v1/settings', headers=odd))
    assert _refused_for_scope(client.post('/api/v1/wishlist', headers=odd, json={}))


def test_a_session_does_not_widen_a_read_key(client, keys):
    # the default session is profile 1, an admin
    with client.session_transaction() as s:
        s['profile_id'] = 1
    assert _refused_for_scope(client.post('/api/v1/wishlist', headers=READ, json={}))
    assert _refused_for_scope(client.get('/api/v1/api-keys-internal', headers=READ))


def test_login_mode_gives_a_read_key_reads_and_nothing_else(client, login_mode):
    assert client.get('/api/v1/library/stats', headers=READ).status_code == 200
    assert _refused_for_scope(client.get('/api/v1/settings', headers=READ))
    assert _refused_for_scope(client.post('/api/profiles/invites', headers=READ, json={}))
    assert _refused_for_scope(client.get('/api/profiles/invites', headers=READ))
    # an admin key keeps what #1676 gave it
    assert client.get('/api/profiles/invites', headers=ADMIN).status_code == 200


def test_a_wrong_key_is_not_mistaken_for_a_read_key(client, keys):
    resp = client.get('/api/v1/library/stats', headers={'X-API-Key': 'sk_nope'})
    assert resp.status_code in (401, 403)
    assert not _refused_for_scope(resp)


def test_admin_creates_read_keys_through_both_doors(client, keys):
    resp = client.post('/api/v1/api-keys', headers=ADMIN, json={'label': 'widget', 'scope': 'read'})
    assert resp.status_code == 201 and resp.get_json()['data']['scope'] == 'read'
    resp = client.post('/api/v1/api-keys-internal/generate', headers=ADMIN, json={'label': 'widget2', 'scope': 'read'})
    assert resp.status_code == 201 and resp.get_json()['data']['scope'] == 'read'
    # no scope given keeps today's behaviour
    resp = client.post('/api/v1/api-keys', headers=ADMIN, json={'label': 'plain'})
    assert resp.get_json()['data']['scope'] == 'admin'
    for door, kw in (('/api/v1/api-keys', {'headers': ADMIN}), ('/api/v1/api-keys-internal/generate', {'headers': ADMIN})):
        bad = client.post(door, json={'label': 'x', 'scope': 'root'}, **kw)
        assert bad.status_code == 400
    listed = client.get('/api/v1/api-keys', headers=ADMIN).get_json()['data']['keys']
    assert {k['scope'] for k in listed} <= {'admin', 'read'}


# ---- guard: the allowlist is the only door ------------------------------------

def _concrete(rule):
    return re.sub(r'<[^>]+>', '1', rule.rule)


def test_every_route_a_read_key_should_not_reach_answers_403(client, keys):
    """Walk the whole route table. Anything outside the allowlist, and every
    non-GET, must be refused for scope before a view runs (so none of this
    executes a handler). Anything inside the allowlist is left alone here."""
    checked = refused = 0
    for rule in web_server.app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        path = _concrete(rule)
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            checked += 1
            if read_key_may_access(method, path):
                continue
            resp = client.open(path, method=method, headers=READ, json={})
            assert _refused_for_scope(resp), f'{method} {rule.rule} reached a view with a read key ({resp.status_code})'
            refused += 1
    assert checked > 1000 and refused > 1000


# ---- guard: a GET a read key can reach must not change anything ---------------

# Calls that change state when they appear in a GET handler. This is a source
# scan of the handler body (one level, not its helpers), the cheap half of the
# guard. The other half runs the reachable GETs against a database that refuses
# writes, below.
_WRITE_SQL = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE|DROP|CREATE|ALTER)\b', re.I)
_WRITE_ATTRS = {'commit', 'executescript', 'executemany', 'unlink', 'rmtree', 'remove',
                'rename', 'touch', 'write_text', 'write_bytes'}
_HTTP_WRITE_ATTRS = {'post', 'put', 'patch', 'delete'}


def _mutations(fn) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(fn))))
    except (OSError, TypeError, SyntaxError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            attr = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')
            owner = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ''
            if attr in _WRITE_ATTRS:
                found.append(attr)
            elif attr == 'set' and re.search(r'config|cfg', owner, re.I):
                found.append('config.set')
            elif attr == 'Thread':
                found.append('Thread')
            elif attr in _HTTP_WRITE_ATTRS and re.search(r'requests|session|http', owner, re.I):
                found.append(f'http {attr}')
            elif attr in ('execute',) and node.args:
                text = ''.join(c.value for c in ast.walk(node.args[0])
                               if isinstance(c, ast.Constant) and isinstance(c.value, str))
                if _WRITE_SQL.match(text):
                    found.append('write SQL')
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                and ast.unparse(node.value) == 'session':
            found.append('session write')
    return sorted(set(found))


def _read_key_gets():
    """(rule, view function) for every GET-only route a read key can reach."""
    out = []
    for rule in web_server.app.url_map.iter_rules():
        if rule.methods - {'GET', 'HEAD', 'OPTIONS'}:
            continue
        if read_key_may_access('GET', _concrete(rule)):
            out.append((rule, web_server.app.view_functions[rule.endpoint]))
    return out


def test_the_read_key_surface_is_what_we_think_it_is():
    paths = {r.rule for r, _ in _read_key_gets()}
    assert len(paths) > 40
    assert all(p.startswith('/api/v1/') for p in paths)
    for prefix in READ_KEY_DENIED_PREFIXES:
        assert not any(p == prefix or p.startswith(prefix + '/') for p in paths)


def test_no_get_a_read_key_can_reach_changes_state_in_its_own_body():
    offenders = {rule.rule: m for rule, fn in _read_key_gets() if (m := _mutations(fn))}
    assert not offenders, (
        'These GET routes are open to read-scope keys and change state. Make them '
        f'POST/PUT/DELETE, or add the path to DENIED_PREFIXES if it must stay a GET: {offenders}')


def test_the_scan_does_catch_a_mutating_get():
    def sneaky():
        conn.cursor().execute("UPDATE tracks SET play_count = 1")  # noqa: F821
        conn.commit()  # noqa: F821
        config_manager.set('x', 1)  # noqa: F821
        threading.Thread(target=print).start()  # noqa: F821

    assert {'write SQL', 'commit', 'config.set', 'Thread'} <= set(_mutations(sneaky))

    def honest():
        return conn.cursor().execute("SELECT 1").fetchall()  # noqa: F821

    assert _mutations(honest) == []


_SQLITE_WRITES = {
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE,
}


def test_reachable_gets_write_nothing_when_run(client, keys, monkeypatch):
    """Run every reachable GET that needs no path argument as a read key, with
    the database refusing writes and the config recording them. The scan above
    reads handler bodies; this catches a helper that writes underneath."""
    real_connect = sqlite3.connect
    writes: list[str] = []

    def authorizer(action, arg1, arg2, dbname, source):
        # SQLite builds its own temp tables for some sorts and joins; that is
        # not a write to the data
        if arg1 and str(arg1).startswith('sqlite_temp'):
            return sqlite3.SQLITE_OK
        if action in _SQLITE_WRITES:
            writes.append(f'{action} {arg1}')
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect(*a, **kw):
        conn = real_connect(*a, **kw)
        conn.set_authorizer(authorizer)
        return conn

    config_writes: list[str] = []
    monkeypatch.setattr(web_server.config_manager, 'set',
                        lambda name, value, *a, **kw: config_writes.append(name))
    monkeypatch.setattr(sqlite3, 'connect', connect)

    ran = 0
    culprits = {}
    for rule, _fn in _read_key_gets():
        if '<' in rule.rule:
            continue
        before = (len(writes), len(config_writes))
        client.get(rule.rule, headers=READ)
        ran += 1
        if (len(writes), len(config_writes)) != before:
            culprits[rule.rule] = (writes[before[0]:], config_writes[before[1]:])
    monkeypatch.undo()
    assert ran > 20
    assert not culprits, f'a GET open to read keys tried to write: {culprits}'
