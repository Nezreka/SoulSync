""""Remove from playlist" did nothing on Jellyfin (#1159, AfonsoG6).

    "when I click on 'Remove from playlist' on a bad match, it is not actually
    removed from the server playlist."

Jellyfin playlist edits are delete-and-recreate: ``update_playlist`` backs up,
DELETEs the playlist, and creates a new one — with a NEW id. Three things then
conspired:

  1. The compare page holds the playlist id it loaded. Any recreate — a sync
     run, or the PREVIOUS click in this same editor — kills that id.
  2. ``get_playlist_tracks(dead_id)`` returns [], so ``remove_one_occurrence``
     removes nothing and the endpoint 404s. Nothing changes on the server.
  3. The Jellyfin branches never returned ``new_playlist_id``, so the client
     could not recover — even though BOTH editors already apply that field
     when present (the Plex branches have always sent it, because Plex has the
     same delete-recreate behaviour and got the ID-first-name-fallback
     treatment from day one; Jellyfin never did).

So on Jellyfin: the first edit of a session worked, and every one after it —
or any edit after a sync — silently failed. #837 fixed the ADD path the same
way this report is shaped ("update_playlist ... deletes and recreates").

The fix mirrors the documented Plex pattern: fetch by id, fall back to a
by-name re-resolve when the id yields nothing, and hand the recreated id back
to the client after every successful edit.

Still open, and deliberately not changed here: the recreate itself scrambles
order (get_playlist_tracks sorts by SortName, and the rebuild writes that
order back) and wipes description/artwork — pre-existing costs of the
delete-recreate approach, called out in #837's comment. The native-ops rewrite
is a separate piece of work.
"""

from types import SimpleNamespace

import pytest

import web_server


class _Track:
    def __init__(self, rk):
        self.ratingKey = rk


class _Jf:
    """A Jellyfin whose playlist was recreated: the OLD id is dead, the name
    resolves to the live playlist under a NEW id."""

    def __init__(self, live_id='new77', tracks=('heyjude', 'other')):
        self.live_id = live_id
        self.tracks = {live_id: [_Track(t) for t in tracks]}
        self.updated = []

    def get_playlist_tracks(self, playlist_id):
        return self.tracks.get(str(playlist_id), [])

    def get_playlist_by_name(self, name):
        return SimpleNamespace(id=self.live_id, title=name)

    def update_playlist(self, name, tracks):
        # the recreate: the live playlist gets ANOTHER new id
        new_id = self.live_id + 'r'
        self.tracks = {new_id: list(tracks)}
        self.live_id = new_id
        self.updated.append((name, [t.ratingKey for t in tracks]))
        return True


@pytest.fixture
def jf(monkeypatch):
    client = _Jf()
    monkeypatch.setattr(web_server, 'media_server_engine',
                        SimpleNamespace(client=lambda n: client if n == 'jellyfin' else None))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'jellyfin')
    monkeypatch.setattr(web_server, '_persist_find_and_add_match', lambda *a, **kw: None)
    return client


def _post(path, payload):
    return web_server.app.test_client().post(path, json=payload)


# ── remove: the reported click ──────────────────────────────────────────────

def test_remove_works_through_a_stale_playlist_id(jf):
    """The exact failure: the page's id died when a sync (or the previous
    edit) recreated the playlist. This used to 404 and change nothing."""
    resp = _post('/api/server/playlist/DEAD_ID/remove-track',
                 {'track_id': 'heyjude', 'playlist_name': 'Beatles'})
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'] is True
    assert jf.updated == [('Beatles', ['other'])], "hey jude actually gone"


def test_remove_hands_back_the_recreated_id(jf):
    """update_playlist just killed whatever id the client held — without the
    new one, the NEXT edit is the silent failure all over again. Both editors
    already apply new_playlist_id when present."""
    resp = _post('/api/server/playlist/DEAD_ID/remove-track',
                 {'track_id': 'heyjude', 'playlist_name': 'Beatles'})
    assert resp.get_json()['new_playlist_id'] == jf.live_id


def test_remove_of_a_genuinely_absent_track_still_404s(jf):
    """The fallback must not turn a real miss into a phantom success."""
    resp = _post('/api/server/playlist/DEAD_ID/remove-track',
                 {'track_id': 'not-there', 'playlist_name': 'Beatles'})
    assert resp.status_code == 404
    assert jf.updated == []


# ── replace: same machinery, same fix ───────────────────────────────────────

def test_replace_works_through_a_stale_playlist_id(jf):
    resp = _post('/api/server/playlist/DEAD_ID/replace-track',
                 {'old_track_id': 'heyjude', 'new_track_id': 'yesterday',
                  'playlist_name': 'Beatles'})
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'] is True
    assert jf.updated == [('Beatles', ['yesterday', 'other'])]
    assert body['new_playlist_id'] == jf.live_id


def test_a_live_id_is_used_as_is(jf):
    """The fallback only engages when the id yields nothing — a working id
    must not be second-guessed."""
    resp = _post(f'/api/server/playlist/{jf.live_id}/remove-track',
                 {'track_id': 'other', 'playlist_name': 'Beatles'})
    assert resp.get_json()['success'] is True
    assert jf.updated == [('Beatles', ['heyjude'])]


def test_a_failed_id_relookup_does_not_fail_the_edit(monkeypatch):
    """Learning the new id is best-effort: the edit already succeeded, and the
    stale-id fallback rescues the next one anyway."""
    class _NoLookup(_Jf):
        def __init__(self):
            super().__init__()
            self._edited = False

        def get_playlist_by_name(self, name):
            if self._edited:
                raise RuntimeError('jellyfin hiccup')
            return super().get_playlist_by_name(name)

        def update_playlist(self, name, tracks):
            out = super().update_playlist(name, tracks)
            self._edited = True
            return out

    client = _NoLookup()
    monkeypatch.setattr(web_server, 'media_server_engine',
                        SimpleNamespace(client=lambda n: client if n == 'jellyfin' else None))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'jellyfin')
    resp = _post('/api/server/playlist/DEAD_ID/remove-track',
                 {'track_id': 'heyjude', 'playlist_name': 'Beatles'})
    body = resp.get_json()
    assert body['success'] is True
    assert body['new_playlist_id'] is None
