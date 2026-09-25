"""Per-target cover-art apply (Pache711: 'select one or the other to fix').

A missing-cover-art finding offers album art AND artist art as independently
applyable targets. ``_fix_missing_cover_art`` routes on ``_fix_action``:
'album' (default), 'artist', or 'both'. Verified against a real SQLite DB so
the UPDATE statements are exercised.

The subject is ``lib2:1`` because that is the only kind ``missing_cover_art``
emits. These ran against a bare ``'al1'`` while the handler still carried a
legacy branch behind that check — so they exercised the version no finding
could reach, and the artwork columns they asserted on (``albums.thumb_url``,
``artists.thumb_url``) are not the ones the apply writes.
"""

from __future__ import annotations

import sys
import types

if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = type("S", (), {})
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = oauth2.SpotifyClientCredentials = type("O", (), {})
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _Cfg:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _Cfg()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

import sqlite3

import pytest

from core.repair_worker import RepairWorker


class _DB:
    def __init__(self, path):
        from core.library2.schema import ensure_library_v2_schema

        self.path = str(path)
        conn = self._get_connection()
        ensure_library_v2_schema(conn)
        conn.execute(
            "INSERT INTO lib2_artists (id, name, sort_name, image_url) "
            "VALUES (1, 'Forre Sterra', 'Forre Sterra', 'http://old/artist.jpg')")
        conn.execute(
            "INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) "
            "VALUES (1, 1, 'For You', NULL)")
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (1, 1, 'Song')")
        conn.commit()
        conn.close()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _worker(tmp_path):
    w = RepairWorker.__new__(RepairWorker)
    w.db = _DB(tmp_path / "m.db")
    w.transfer_folder = str(tmp_path)
    w._config_manager = None
    return w


def _thumbs(w):
    conn = w.db._get_connection()
    try:
        alb = conn.execute("SELECT image_url FROM lib2_albums WHERE id=1").fetchone()[0]
        art = conn.execute("SELECT image_url FROM lib2_artists WHERE id=1").fetchone()[0]
    finally:
        conn.close()
    return alb, art


DETAILS = {
    'album_id': 'lib2:1', 'album_title': 'For You', 'artist': 'Forre Sterra',
    'found_artwork_url': 'http://new/album.jpg',
    'found_artist_url': 'http://new/artist.jpg',
}


def test_artist_only_sets_artist_leaves_album(tmp_path):
    w = _worker(tmp_path)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'artist'})
    assert res['success'] and res['action'] == 'applied_artist_art'
    album_thumb, artist_thumb = _thumbs(w)
    assert artist_thumb == 'http://new/artist.jpg'   # artist updated
    assert album_thumb is None                       # album untouched


def test_album_only_sets_album_leaves_artist(tmp_path):
    w = _worker(tmp_path)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'album'})
    assert res['success']
    album_thumb, artist_thumb = _thumbs(w)
    assert album_thumb == 'http://new/album.jpg'     # album updated
    assert artist_thumb == 'http://old/artist.jpg'   # artist left as-is


def test_default_action_is_album_only(tmp_path):
    # No _fix_action → behaves exactly like the old "Apply Art" (album only).
    w = _worker(tmp_path)
    w._fix_missing_cover_art('album', 'lib2:1', None, dict(DETAILS))
    album_thumb, artist_thumb = _thumbs(w)
    assert album_thumb == 'http://new/album.jpg'
    assert artist_thumb == 'http://old/artist.jpg'


def test_both_sets_album_and_artist(tmp_path):
    w = _worker(tmp_path)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'both'})
    assert res['success']
    album_thumb, artist_thumb = _thumbs(w)
    assert album_thumb == 'http://new/album.jpg'
    assert artist_thumb == 'http://new/artist.jpg'
    assert 'artist image' in res['message']


def test_artist_action_without_found_artist_url_fails_cleanly(tmp_path):
    w = _worker(tmp_path)
    res = w._fix_missing_cover_art('album', 'lib2:1', None,
                                   {**DETAILS, 'found_artist_url': None, '_fix_action': 'artist'})
    assert res['success'] is False
    album_thumb, artist_thumb = _thumbs(w)
    assert artist_thumb == 'http://old/artist.jpg'   # nothing changed


# ── apply-result message accuracy (Sokhi/Boulder: "read-only?" on writable fs) ──

def _add_track(w, path):
    conn = w.db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
            "VALUES (1, ?, 'mp3', 1)", (str(path),))
        conn.commit()
    finally:
        conn.close()


def _apply_returns(monkeypatch, **art_result):
    import core.metadata.art_apply as aa
    base = {'embedded': 0, 'failed': 0, 'skipped': 0, 'cover_written': False, 'read_only_fs': False}
    base.update(art_result)
    monkeypatch.setattr(aa, 'apply_art_to_album_files', lambda *a, **k: base)


def test_already_arted_reports_present_not_readonly(tmp_path, monkeypatch):
    # The bug: all files already had art (skipped) → embedded 0, cover 0 → the
    # old message cried "(read-only?)" on a perfectly writable library.
    w = _worker(tmp_path)
    f = tmp_path / 'song.mp3'; f.write_bytes(b'x')
    _add_track(w, f)
    _apply_returns(monkeypatch, skipped=1)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'album'})
    assert res['success'] is True
    assert 'already present' in res['message'].lower()
    assert 'read-only' not in res['message'].lower()


def test_failed_writes_blame_permissions_not_readonly(tmp_path, monkeypatch):
    w = _worker(tmp_path)
    f = tmp_path / 'song.mp3'; f.write_bytes(b'x')
    _add_track(w, f)
    _apply_returns(monkeypatch, failed=1)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'album'})
    assert res['success'] is True
    assert 'permission' in res['message'].lower()
    assert 'read-only' not in res['message'].lower()


def test_genuine_read_only_still_hard_fails(tmp_path, monkeypatch):
    w = _worker(tmp_path)
    f = tmp_path / 'song.mp3'; f.write_bytes(b'x')
    _add_track(w, f)
    _apply_returns(monkeypatch, read_only_fs=True)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'album'})
    assert res['success'] is False
    assert 'read-only' in res['error'].lower()


def test_embedded_success_message(tmp_path, monkeypatch):
    w = _worker(tmp_path)
    f = tmp_path / 'song.mp3'; f.write_bytes(b'x')
    _add_track(w, f)
    _apply_returns(monkeypatch, embedded=1)
    res = w._fix_missing_cover_art('album', 'lib2:1', None, {**DETAILS, '_fix_action': 'album'})
    assert res['success'] is True and 'embedded into 1' in res['message']
