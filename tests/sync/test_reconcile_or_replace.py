"""sync_mode='reconcile' dispatch + fallback (#792).

_reconcile_or_replace tries the client's in-place reconcile and falls back to
the destructive replace only when reconcile is unavailable or fails, so a sync
always succeeds while preferring the non-destructive path.
"""

import sys
import types

# Stub optional Spotify dep so services.sync_service imports in the test env.
if 'spotipy' not in sys.modules:
    sp = types.ModuleType('spotipy'); oa = types.ModuleType('spotipy.oauth2')
    sp.Spotify = type('S', (), {}); oa.SpotifyOAuth = oa.SpotifyClientCredentials = type('O', (), {})
    sp.oauth2 = oa; sys.modules['spotipy'] = sp; sys.modules['spotipy.oauth2'] = oa

from services.sync_service import PlaylistSyncService


def _service():
    return PlaylistSyncService.__new__(PlaylistSyncService)


class _Client:
    def __init__(self, reconcile=None):
        self._reconcile = reconcile
        self.reconcile_calls = []
        self.replace_calls = []
        if reconcile is not None:
            def reconcile_playlist(name, tracks):
                self.reconcile_calls.append(name)
                if isinstance(reconcile, Exception):
                    raise reconcile
                return reconcile
            self.reconcile_playlist = reconcile_playlist

    def update_playlist(self, name, tracks):
        self.replace_calls.append(name)
        return True


def test_reconcile_success_does_not_fall_back():
    c = _Client(reconcile=True)
    assert _service()._reconcile_or_replace(c, 'P', []) is True
    assert c.reconcile_calls == ['P']
    assert c.replace_calls == []           # never recreated


def test_reconcile_false_falls_back_to_replace():
    c = _Client(reconcile=False)
    assert _service()._reconcile_or_replace(c, 'P', []) is True
    assert c.reconcile_calls == ['P']
    assert c.replace_calls == ['P']        # fell back so the sync still happens


def test_reconcile_exception_falls_back_to_replace():
    c = _Client(reconcile=RuntimeError('boom'))
    assert _service()._reconcile_or_replace(c, 'P', []) is True
    assert c.reconcile_calls == ['P']
    assert c.replace_calls == ['P']


def test_client_without_reconcile_uses_replace():
    c = _Client(reconcile=None)            # no reconcile_playlist attribute
    assert not hasattr(c, 'reconcile_playlist')
    assert _service()._reconcile_or_replace(c, 'P', []) is True
    assert c.replace_calls == ['P']
