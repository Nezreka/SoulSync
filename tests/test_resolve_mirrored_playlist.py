"""Seam tests for resolve_mirrored_playlist source-vs-PK resolution.

Regression cover for the PR #780 follow-up: numeric *upstream* ids (Deezer
playlist ids are all-digit) must resolve by (source, source_playlist_id), NOT
be mistaken for the mirrored-playlists primary key. The old PK-first logic made
the Deezer organize-by-playlist toggle resolve the wrong row (or nothing).
"""

from __future__ import annotations

from database.music_database import MusicDatabase


def test_numeric_source_id_resolves_by_source_not_pk(tmp_path):
    """A Deezer-style all-numeric upstream id resolves the right row."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    pk = db.mirror_playlist(source='deezer', source_playlist_id='908622995',
                            name='My Deezer Mix', tracks=[], profile_id=1)
    assert pk
    row = db.resolve_mirrored_playlist('908622995', profile_id=1, default_source='deezer')
    assert row is not None
    assert row['id'] == pk
    assert row['source'] == 'deezer'
    # And it must NOT have been a PK lookup: 908622995 is not a valid PK here.
    assert db.get_mirrored_playlist(908622995) is None


def test_spotify_alphanumeric_resolves_by_source(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    pk = db.mirror_playlist(source='spotify', source_playlist_id='37i9dQZF1DXcBWIGoYBM5M',
                            name='Top Hits', tracks=[], profile_id=1)
    row = db.resolve_mirrored_playlist('37i9dQZF1DXcBWIGoYBM5M', profile_id=1, default_source='spotify')
    assert row is not None and row['id'] == pk


def test_pk_fallback_when_no_source_match(tmp_path):
    """A numeric ref that isn't a known source id still resolves via PK fallback."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    pk = db.mirror_playlist(source='spotify', source_playlist_id='abc123XYZ',
                            name='Sp', tracks=[], profile_id=1)
    row = db.resolve_mirrored_playlist(str(pk), profile_id=1, default_source='spotify')
    assert row is not None and row['id'] == pk


def test_resolution_is_profile_scoped(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.mirror_playlist(source='deezer', source_playlist_id='555',
                       name='D', tracks=[], profile_id=1)
    # Another profile must not resolve profile 1's Deezer playlist by source.
    assert db.resolve_mirrored_playlist('555', profile_id=2, default_source='deezer') is None


def test_empty_refs_return_none(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    assert db.resolve_mirrored_playlist(None) is None
    assert db.resolve_mirrored_playlist('') is None
    assert db.resolve_mirrored_playlist('   ') is None
