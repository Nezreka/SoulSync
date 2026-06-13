"""Mirrored-playlist custom name (alias) — seam + DB regression tests.

Users can rename a mirrored playlist; the alias overrides the name shown in the
UI and used when syncing, while the playlist stays tied to its upstream source.
The non-negotiable guarantee: the alias must SURVIVE an upstream refresh (which
rewrites the upstream `name`).
"""

from __future__ import annotations

from core.playlists.naming import effective_mirrored_name
from database.music_database import MusicDatabase


# ── pure seam ────────────────────────────────────────────────────────────────

def test_custom_name_wins_when_set():
    assert effective_mirrored_name({'name': 'Discover Weekly', 'custom_name': 'My Jams'}) == 'My Jams'


def test_falls_back_to_upstream_name_when_no_alias():
    assert effective_mirrored_name({'name': 'Discover Weekly', 'custom_name': None}) == 'Discover Weekly'
    assert effective_mirrored_name({'name': 'Discover Weekly', 'custom_name': ''}) == 'Discover Weekly'
    assert effective_mirrored_name({'name': 'Discover Weekly', 'custom_name': '   '}) == 'Discover Weekly'
    assert effective_mirrored_name({'name': 'Discover Weekly'}) == 'Discover Weekly'


def test_safe_on_bad_input():
    assert effective_mirrored_name(None) == ''
    assert effective_mirrored_name('nope') == ''
    assert effective_mirrored_name({}) == ''


# ── DB behaviour ─────────────────────────────────────────────────────────────

def _mk(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    pk = db.mirror_playlist(source='spotify', source_playlist_id='PL1',
                            name='Original Name', tracks=[], profile_id=1)
    assert pk
    return db, pk


def test_set_and_clear_custom_name(tmp_path):
    db, pk = _mk(tmp_path)
    assert db.get_mirrored_playlist(pk).get('custom_name') in (None, '')

    assert db.set_mirrored_playlist_custom_name(pk, 'My Alias') is True
    assert db.get_mirrored_playlist(pk)['custom_name'] == 'My Alias'

    # Blank clears it back to upstream.
    assert db.set_mirrored_playlist_custom_name(pk, '   ') is True
    assert db.get_mirrored_playlist(pk).get('custom_name') is None

    # None clears too.
    db.set_mirrored_playlist_custom_name(pk, 'Again')
    assert db.set_mirrored_playlist_custom_name(pk, None) is True
    assert db.get_mirrored_playlist(pk).get('custom_name') is None


def test_alias_survives_upstream_refresh(tmp_path):
    """THE regression: re-mirroring (refresh) rewrites the upstream `name` but
    must NOT touch the custom alias."""
    db, pk = _mk(tmp_path)
    db.set_mirrored_playlist_custom_name(pk, 'My Alias')

    # Upstream renamed the playlist + added tracks → refresh.
    db.mirror_playlist(source='spotify', source_playlist_id='PL1',
                       name='Upstream Renamed', tracks=[
                           {'track_name': 'A', 'artist_name': 'X'},
                       ], profile_id=1)

    row = db.get_mirrored_playlist(pk)
    assert row['name'] == 'Upstream Renamed'      # upstream name keeps tracking
    assert row['custom_name'] == 'My Alias'       # alias preserved
    assert effective_mirrored_name(row) == 'My Alias'


def test_set_custom_name_does_not_touch_other_fields(tmp_path):
    db, pk = _mk(tmp_path)
    before = db.get_mirrored_playlist(pk)
    db.set_mirrored_playlist_custom_name(pk, 'Alias')
    after = db.get_mirrored_playlist(pk)
    assert after['name'] == before['name']
    assert after['source'] == before['source']
    assert after['source_playlist_id'] == before['source_playlist_id']
