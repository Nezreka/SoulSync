import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.spotify_client import normalize_spotify_oauth_config

def test_normalization():
    # Whitespace + quotes are stripped (paste garbage); the redirect_uri's
    # trailing slash is PRESERVED — Spotify matches it exactly against the app
    # dashboard, so stripping it could break a valid registration (#942 follow-up).
    config = {
        "client_id": '  "client_id"   ',
        "client_secret": "  client_secret  ",
        "redirect_uri": "  http://127.0.0.1:8888/callback/  "
    }
    expected = {
        "client_id": "client_id",
        "client_secret": "client_secret",
        "redirect_uri": "http://127.0.0.1:8888/callback/"   # slash kept
    }
    assert normalize_spotify_oauth_config(config) == expected


def test_trailing_slash_on_redirect_uri_is_preserved():
    """Regression guard: Spotify requires an EXACT redirect-URI match against the
    app dashboard, so a trailing slash a user registered must NOT be stripped —
    stripping it would send '…/callback' and trigger INVALID_CLIENT (#942)."""
    with_slash = {"client_id": "x", "client_secret": "y",
                  "redirect_uri": "http://127.0.0.1:8888/callback/"}
    without_slash = {"client_id": "x", "client_secret": "y",
                     "redirect_uri": "http://127.0.0.1:8888/callback"}
    assert normalize_spotify_oauth_config(with_slash)["redirect_uri"] == "http://127.0.0.1:8888/callback/"
    assert normalize_spotify_oauth_config(without_slash)["redirect_uri"] == "http://127.0.0.1:8888/callback"

def test_empty_values():
    # Empty input values
    config = {
        "client_id": "",
        "client_secret": None,
        "redirect_uri": ""
    }
    # When value is None, it falls into the else branch: normalized[key] = value
    # value is None, so expected is None for client_secret
    expected = {
        "client_id": "",
        "client_secret": None,
        "redirect_uri": ""
    }
    assert normalize_spotify_oauth_config(config) == expected

def test_missing_keys():
    # Input dictionary with missing keys
    config = {
        "client_id": "client_id"
    }
    # .get(key, "") means missing keys become ""
    expected = {
        "client_id": "client_id",
        "client_secret": "",
        "redirect_uri": ""
    }
    assert normalize_spotify_oauth_config(config) == expected

def test_non_string_values():
    # Input dictionary with non-string values for the keys
    config = {
        "client_id": 123,
        "client_secret": True,
        "redirect_uri": None
    }
    # When value is not a string, it falls into the else branch: normalized[key] = value
    expected = {
        "client_id": 123,
        "client_secret": True,
        "redirect_uri": None
    }
    assert normalize_spotify_oauth_config(config) == expected

def test_no_input():
    # Empty input dictionary
    config = {}
    # .get(key, "") means missing keys become ""
    expected = {
        "client_id": "",
        "client_secret": "",
        "redirect_uri": ""
    }
    assert normalize_spotify_oauth_config(None) == {}
    assert normalize_spotify_oauth_config(config) == expected

# ── create_or_update_playlist: export a mirrored playlist back to Spotify (#945) ──

from core.spotify_client import SpotifyClient as _SpotifyClient


class _FakeSp:
    def __init__(self):
        self.calls = []

    def current_user(self):
        self.calls.append(('current_user',))
        return {'id': 'user-1'}

    def user_playlist_create(self, user_id, name, public=False, description=''):
        self.calls.append(('create', user_id, name, public))
        return {'id': 'pl-new'}

    def playlist_add_items(self, pid, uris):
        self.calls.append(('add', pid, list(uris)))

    def playlist_replace_items(self, pid, uris):
        self.calls.append(('replace', pid, list(uris)))


def _spotify_with(sp, authed=True):
    c = _SpotifyClient.__new__(_SpotifyClient)
    c.sp = sp
    c.is_spotify_authenticated = lambda: authed
    return c


def test_create_new_playlist_adds_tracks():
    sp = _FakeSp()
    res = _spotify_with(sp).create_or_update_playlist('My Mix', ['a', 'b', 'c'])
    assert res['success'] and res['playlist_id'] == 'pl-new'
    assert res['url'] == 'https://open.spotify.com/playlist/pl-new'
    assert res['added'] == 3
    assert ('create', 'user-1', 'My Mix', False) in sp.calls
    assert ('add', 'pl-new', ['spotify:track:a', 'spotify:track:b', 'spotify:track:c']) in sp.calls


def test_update_existing_replaces_no_create():
    sp = _FakeSp()
    res = _spotify_with(sp).create_or_update_playlist('My Mix', ['a', 'b'], existing_id='pl-x')
    assert res['success'] and res['playlist_id'] == 'pl-x'
    assert ('replace', 'pl-x', ['spotify:track:a', 'spotify:track:b']) in sp.calls
    assert not any(c[0] == 'create' for c in sp.calls)


def test_chunks_over_100_tracks():
    sp = _FakeSp()
    res = _spotify_with(sp).create_or_update_playlist('Big', [str(i) for i in range(250)])
    assert res['added'] == 250
    adds = [c for c in sp.calls if c[0] == 'add']
    assert len(adds) == 3 and len(adds[0][2]) == 100 and len(adds[2][2]) == 50


def test_empty_tracks_errors_no_api_calls():
    sp = _FakeSp()
    res = _spotify_with(sp).create_or_update_playlist('X', [])
    assert not res['success'] and 'No matching' in res['error']
    assert sp.calls == []


def test_not_authed_errors():
    res = _spotify_with(_FakeSp(), authed=False).create_or_update_playlist('X', ['a'])
    assert not res['success'] and 'not connected' in res['error']


def test_insufficient_scope_says_reconnect():
    class _ScopeErr(_FakeSp):
        def user_playlist_create(self, *a, **k):
            raise Exception('403 Forbidden: insufficient client scope')
    res = _spotify_with(_ScopeErr()).create_or_update_playlist('X', ['a'])
    assert not res['success'] and 'Reconnect Spotify' in res['error']


# ── Spotify auth regression hotfix: scope must not force re-auth; callbacks must write
#    the DB store the client reads (else a re-auth never takes effect) ──

import os as _os


def test_oauth_scope_has_no_write_scope_that_forces_reauth():
    """Spotipy invalidates a cached token the moment the requested scope stops being a subset
    of the token's granted scope — so GROWING the global scope forces every user to re-auth on
    upgrade (it broke all Spotify users). The write scope (playlist-modify) must NOT live in the
    global scope; request it on-demand instead."""
    from core.spotify_client import SPOTIFY_OAUTH_SCOPE
    assert 'playlist-modify' not in SPOTIFY_OAUTH_SCOPE
    # the read scopes existing tokens already carry must stay
    for s in ('user-library-read', 'user-read-private', 'playlist-read-private',
              'playlist-read-collaborative', 'user-read-email', 'user-follow-read'):
        assert s in SPOTIFY_OAUTH_SCOPE


def test_global_oauth_callbacks_use_db_token_cache_not_file():
    """The OAuth callbacks wrote the new token to the legacy file cache while the client reads
    DatabaseTokenCache, so a re-auth never reached the client ("validation failed" despite a good
    exchange). The global callbacks must write the same DB-backed store the client uses."""
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = open(_os.path.join(root, 'web_server.py'), encoding='utf-8').read()
    assert "cache_path='config/.spotify_cache'" not in src          # no global file-cache writes
    assert src.count('cache_handler=DatabaseTokenCache(config_manager)') >= 2


# ── on-demand Spotify export write-auth (#945 follow-up) ──

def test_export_scope_is_global_plus_write_and_global_stays_readonly():
    """The export scope adds playlist-modify ON TOP of the unchanged global scope. Critically
    the GLOBAL scope must NOT gain write (that's what force-invalidated everyone's token)."""
    from core.spotify_client import SPOTIFY_OAUTH_SCOPE, SPOTIFY_EXPORT_SCOPE
    assert 'playlist-modify' not in SPOTIFY_OAUTH_SCOPE           # global stays read-only
    assert 'playlist-modify-public' in SPOTIFY_EXPORT_SCOPE
    assert 'playlist-modify-private' in SPOTIFY_EXPORT_SCOPE
    # export scope is a strict superset of the global read scope
    assert set(SPOTIFY_OAUTH_SCOPE.split()).issubset(set(SPOTIFY_EXPORT_SCOPE.split()))


class _CacheHandler:
    def __init__(self, token):
        self._token = token

    def get_cached_token(self):
        return self._token


def _client_with_token(token):
    import types
    c = _SpotifyClient.__new__(_SpotifyClient)
    c.sp = types.SimpleNamespace(auth_manager=types.SimpleNamespace(cache_handler=_CacheHandler(token)))
    return c


def test_has_write_scope_true_when_token_carries_playlist_modify():
    tok = {'scope': 'user-library-read playlist-modify-public playlist-read-private'}
    assert _client_with_token(tok).has_write_scope() is True


def test_has_write_scope_false_for_readonly_token_or_missing():
    assert _client_with_token({'scope': 'user-library-read playlist-read-private'}).has_write_scope() is False
    assert _client_with_token(None).has_write_scope() is False           # no cached token
    assert _client_with_token({}).has_write_scope() is False             # token w/o scope field


def test_has_write_scope_false_when_no_client():
    c = _SpotifyClient.__new__(_SpotifyClient)
    c.sp = None
    assert c.has_write_scope() is False
