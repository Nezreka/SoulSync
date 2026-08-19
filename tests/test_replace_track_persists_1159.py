"""Fixing a bad match through replace-track saved nothing (#1159, AfonsoG6).

The second half of "incorrect matches reappear after I have manually changed
them". The replace-track endpoint edited the server playlist and returned —
no ``record_manual_match``, no durable library match — while add-track (the
Find & Add flow) persists both through ``_persist_find_and_add_match``. So a
manual correction existed only as a playlist edit:

  - the 0.75 auto-match the sync had cached stayed in ``sync_match_cache``
    (nothing overwrote it),
  - the next sync's fast-path read that row and RE-ADDED the wrong track to
    the server playlist,
  - and the next compare re-paired it, dumping the hand-picked track into
    Extras.

``save_sync_match_cache`` is INSERT OR REPLACE, so the confidence-1.0 write
this enables also overwrites the bad cached row — the correction sticks at
compare time AND at sync time with one write.

Wiring tests, not route tests: the endpoint body is Plex/Jellyfin/Navidrome
network calls end to end, so what can regress silently is the seam — the
payload fields being read, and the persist running on each server's success
branch. The frontend halves are pinned too: both editors held
``track.source_track`` at the call site and simply didn't send it, and the
backend can't persist what never arrives.
"""

import re

import pytest

WEB_SERVER = 'web_server.py'


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture(scope='module')
def endpoint():
    src = read(WEB_SERVER)
    i = src.index('def server_playlist_replace_track(')
    return src[i:src.index('\n@app.route', i)]


def test_the_payload_now_carries_the_source_identity(endpoint):
    """Same four fields Find & Add always sent — without a source_track_id
    there is nothing to key a pairing by."""
    for field in ("source_track_id", "source_title", "source_artist", "'source'"):
        assert field in endpoint, field


def test_every_server_branch_persists_on_success(endpoint):
    """Plex, Jellyfin, Navidrome — each has its own success return, and a
    branch that skips the persist reverts on exactly that server."""
    calls = re.findall(r'^[ \t]+_persist_replacement\(\)$', endpoint, re.M)
    assert len(calls) == 3, f"found {len(calls)} call statements"


def test_the_persist_runs_before_the_success_return(endpoint):
    """After the return it is dead code."""
    for m in re.finditer(r'^[ \t]+_persist_replacement\(\)$', endpoint, re.M):
        following = endpoint[m.end():m.end() + 400]
        assert 'return jsonify({"success": True' in following, (
            "persist must sit immediately before a success return")


def test_it_reuses_the_find_and_add_persistence(endpoint):
    """One persistence path, not a second copy. _persist_find_and_add_match is
    the code that writes BOTH stores (cache override + durable manual match)
    and already handles the missing-id case."""
    assert '_persist_find_and_add_match(' in endpoint


def test_a_compare_without_source_info_still_edits(endpoint):
    """source_track_id is absent for non-mirrored compares. The edit must run;
    only the persistence is skipped — and quietly, not with the loud Find & Add
    warning on every ordinary replace."""
    body = endpoint[endpoint.index('def _persist_replacement'):]
    body = body[:body.index('\n        #') if '\n        #' in body else len(body)]
    assert 'if not _src_track_id' in body
    assert 'logger.debug' in body


# ── the two frontends actually send the fields ──────────────────────────────

def test_the_react_editor_sends_the_source_fields():
    src = read('webui/src/routes/sync/-sync.server.ts')
    fn = src[src.index('export async function replaceServerTrack'):]
    fn = fn[:fn.index('\n}')]
    for field in ('source_track_id', 'source_title', 'source_artist'):
        assert field in fn, field
    caller = read('webui/src/routes/sync/-ui/server-compare-editor.tsx')
    call = caller[caller.index('await replaceServerTrack('):]
    call = call[:call.index(')')]
    assert 'track.source_track' in call, "the caller has the row in hand and must pass it"


def test_the_vanilla_editor_sends_them_too():
    src = read('webui/static/pages-extra.js')
    i = src.index('/replace-track`')
    block = src[i:i + 900]
    for field in ('source_track_id', 'source_title', 'source_artist'):
        assert field in block, field


# ── the seam, driven for real ───────────────────────────────────────────────
#
# The wiring tests above prove the code is THERE; none of them prove the
# payload values actually reach _persist_find_and_add_match (a mutant that
# read the fields into a dead variable survived them). So drive the real
# endpoint: POST the payload with the server engine stubbed, and assert the
# persistence was called with exactly what was sent.

def test_a_jellyfin_replace_persists_the_pairing(monkeypatch):
    import web_server
    from types import SimpleNamespace

    class _Track:
        def __init__(self, rk):
            self.ratingKey = rk

    class _Jf:
        def __init__(self):
            self.updated = []

        def get_playlist_tracks(self, playlist_id):
            return [_Track('heyjude'), _Track('other')]

        def update_playlist(self, name, tracks):
            self.updated.append((name, [t.ratingKey for t in tracks]))
            return True

    jf = _Jf()
    monkeypatch.setattr(web_server, 'media_server_engine',
                        SimpleNamespace(client=lambda name: jf if name == 'jellyfin' else None))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server',
                        lambda: 'jellyfin')
    persisted = []
    monkeypatch.setattr(web_server, '_persist_find_and_add_match',
                        lambda *a, **kw: persisted.append((a, kw)))

    client = web_server.app.test_client()
    resp = client.post('/api/server/playlist/pl1/replace-track', json={
        'old_track_id': 'heyjude',
        'new_track_id': 'yesterday',
        'playlist_name': 'Beatles',
        'source_track_id': 'sp1',
        'source_title': 'Yesterday (Remastered 2015)',
        'source_artist': 'The Beatles',
        'source': 'spotify_public',
        'new_track_title': 'Yesterday',
    })
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert jf.updated == [('Beatles', ['yesterday', 'other'])], "the swap itself"
    assert len(persisted) == 1, "the correction must be persisted"
    args, kwargs = persisted[0]
    flat = list(args) + list(kwargs.values())
    assert 'sp1' in flat, "keyed by the source track id from the payload"
    assert 'yesterday' in flat, "pointing at the NEW track, not the old one"
    assert 'heyjude' not in flat, "persisting the OLD id would re-pin the bad match"
    assert 'spotify_public' in flat, "the provider rides along for the durable match"


def test_a_replace_without_source_info_edits_but_does_not_persist(monkeypatch):
    """Non-mirrored compares have no source_track_id — the edit must still run,
    with nothing persisted and no spurious call."""
    import web_server
    from types import SimpleNamespace

    class _Track:
        def __init__(self, rk):
            self.ratingKey = rk

    class _Jf:
        def get_playlist_tracks(self, playlist_id):
            return [_Track('heyjude')]

        def update_playlist(self, name, tracks):
            return True

    monkeypatch.setattr(web_server, 'media_server_engine',
                        SimpleNamespace(client=lambda name: _Jf() if name == 'jellyfin' else None))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server',
                        lambda: 'jellyfin')
    persisted = []
    monkeypatch.setattr(web_server, '_persist_find_and_add_match',
                        lambda *a, **kw: persisted.append(a))

    resp = web_server.app.test_client().post('/api/server/playlist/pl1/replace-track', json={
        'old_track_id': 'heyjude', 'new_track_id': 'yesterday', 'playlist_name': 'Beatles',
    })
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    assert persisted == []
