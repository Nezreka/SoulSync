"""The art endpoints that lock and release a hand-picked image.

POST /api/album/<id>/art and /api/artist/<id>/art apply a pick — and now LOCK it,
so the next library sync cannot write the media server's art back over it
(TheHomeGuy: "if for some reason i need to manually sync the artist to the
library, it seems to remove the custom album art that I added").

DELETE on the same URLs is the way back. It exists because the picker sources its
candidates from external services only and can find none at all — his own
screenshot reads "No alternative covers found for this album" — so a user who
locked art they no longer want would otherwise have nothing to switch to.

The database behaviour is covered by test_custom_art_survives_sync.py; these
tests are about the HTTP surface: does the endpoint reach the lock, and does it
answer honestly when the row isn't there.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-artlock-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'artlock.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

CUSTOM = "https://example.invalid/custom-cover.jpg"


@pytest.fixture
def client():
    return web_server.app.test_client()


def _match_status_columns(conn, table):
    """Every ``*_match_status`` column on a table, read from the live schema
    rather than hardcoded — sources get added over time."""
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            if r[1].endswith('_match_status')]


@pytest.fixture
def seeded():
    """One artist + one album, both following the server (art_locked = 0).

    Marked as already-attempted on every metadata source, and deleted again
    afterwards. Importing web_server starts the real enrichment workers, and
    they claim any row whose ``*_match_status`` is NULL — so a naively seeded
    fixture hands them work and they go and hit the live iTunes API. That was
    happening: "iTunes search for 'Locked Art Artist Locked Art Album' returned
    10 results" turned up in a run of the wider suite. Tests must not reach the
    network, and a fixture must not leave bait behind for a background thread.
    """
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO artists (id, name, thumb_url) VALUES (?,?,?)",
                     ('art-1', 'Locked Art Artist', 'http://server/artist.jpg'))
        conn.execute("INSERT OR REPLACE INTO albums (id, artist_id, title, thumb_url) "
                     "VALUES (?,?,?,?)",
                     ('alb-1', 'art-1', 'Locked Art Album', 'http://server/cover.jpg'))
        for table, row_id in (('artists', 'art-1'), ('albums', 'alb-1')):
            for col in _match_status_columns(conn, table):
                conn.execute(f"UPDATE {table} SET {col} = 'not_found' WHERE id = ?", (row_id,))
        conn.commit()
    finally:
        conn.close()

    yield db

    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM albums WHERE id = ?", ('alb-1',))
        conn.execute("DELETE FROM artists WHERE id = ?", ('art-1',))
        conn.commit()
    finally:
        conn.close()


def _locked(db, table, row_id):
    conn = db._get_connection()
    try:
        row = conn.execute(f"SELECT art_locked FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return None if row is None else row['art_locked']
    finally:
        conn.close()


# ── applying through the endpoint locks the row ──────────────────────────────

def test_applying_album_art_locks_the_row(client, seeded, monkeypatch):
    # The endpoint also writes cover.jpg next to the tracks; there is no such
    # folder here, and that half is best-effort anyway.
    monkeypatch.setattr(web_server, '_derive_album_folder', lambda *a, **k: None)

    r = client.post('/api/album/alb-1/art', json={'url': CUSTOM})

    assert r.status_code == 200
    assert r.get_json()['success'] is True
    assert _locked(seeded, 'albums', 'alb-1') == 1, \
        "the pick was applied but not locked — the next sync would overwrite it"


# ── releasing it hands the art back ──────────────────────────────────────────

def test_deleting_album_art_releases_the_lock(client, seeded, monkeypatch):
    monkeypatch.setattr(web_server, '_derive_album_folder', lambda *a, **k: None)
    client.post('/api/album/alb-1/art', json={'url': CUSTOM})
    assert _locked(seeded, 'albums', 'alb-1') == 1

    r = client.delete('/api/album/alb-1/art')

    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True and body['art_locked'] is False
    assert _locked(seeded, 'albums', 'alb-1') == 0


def test_deleting_artist_art_releases_the_lock(client, seeded):
    seeded.set_artist_thumb_url('art-1', CUSTOM)
    assert _locked(seeded, 'artists', 'art-1') == 1

    r = client.delete('/api/artist/art-1/art')

    assert r.status_code == 200
    assert r.get_json()['art_locked'] is False
    assert _locked(seeded, 'artists', 'art-1') == 0


def test_releasing_leaves_the_current_image_alone(client, seeded):
    """Unlocking must not blank the page — the image stays until the next sync
    replaces it."""
    seeded.set_artist_thumb_url('art-1', CUSTOM)
    client.delete('/api/artist/art-1/art')

    conn = seeded._get_connection()
    try:
        row = conn.execute("SELECT thumb_url FROM artists WHERE id = ?", ('art-1',)).fetchone()
    finally:
        conn.close()
    assert row['thumb_url'] == CUSTOM


# ── honest answers for rows that aren't there ────────────────────────────────

def test_releasing_an_ALREADY_unlocked_row_is_still_200(client, seeded):
    """Relies on SQLite counting matched rows for an UPDATE even when the value
    does not change. If that were not true, releasing an album that is already
    following the server would report a bogus 404 — so this pins the assumption
    the 404 branch rests on."""
    r = client.delete('/api/album/alb-1/art')      # never locked
    assert r.status_code == 200
    assert r.get_json()['success'] is True


def test_releasing_an_unknown_album_is_404(client, seeded):
    r = client.delete('/api/album/does-not-exist/art')
    assert r.status_code == 404


def test_releasing_an_unknown_artist_is_404(client, seeded):
    r = client.delete('/api/artist/does-not-exist/art')
    assert r.status_code == 404


def test_the_delete_route_did_not_displace_the_post_route(client, seeded, monkeypatch):
    """Both verbs live on the same URL; registering DELETE must not shadow POST."""
    monkeypatch.setattr(web_server, '_derive_album_folder', lambda *a, **k: None)
    assert client.post('/api/album/alb-1/art', json={'url': CUSTOM}).status_code == 200
    assert client.delete('/api/album/alb-1/art').status_code == 200
    # …and a verb nobody registered is still rejected.
    assert client.put('/api/album/alb-1/art', json={'url': CUSTOM}).status_code == 405
