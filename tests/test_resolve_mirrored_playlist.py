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


def test_synthetic_mirrored_batch_ref_resolves_by_pk(tmp_path):
    """A discovery/mirror batch carries a synthetic playlist_id like
    youtube_mirrored_<pk> with batch source 'mirrored'. (source, source_playlist_id)
    can't match it — it must resolve via the embedded PK. Regression for the
    organize-by-playlist 'all found but no folder built' report."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    pk = db.mirror_playlist(source='youtube', source_playlist_id='abc123XYZ',
                            name='My Mirror', tracks=[], profile_id=1)
    assert pk
    for ref in (f'youtube_mirrored_{pk}', f'auto_mirror_{pk}', f'mirrored_{pk}'):
        row = db.resolve_mirrored_playlist(ref, profile_id=1, default_source='mirrored')
        assert row is not None and row['id'] == pk, ref


def test_extract_mirrored_pk_pure():
    from core.playlists.source_refs import extract_mirrored_pk
    assert extract_mirrored_pk('youtube_mirrored_63') == 63
    assert extract_mirrored_pk('auto_mirror_7') == 7
    assert extract_mirrored_pk('mirrored_12') == 12
    assert extract_mirrored_pk('42') == 42          # bare PK
    assert extract_mirrored_pk('908622995') == 908622995  # numeric upstream id (PK fallback only)
    assert extract_mirrored_pk('37i9dQZF1DXcBWIGoYBM5M') is None  # real spotify id
    assert extract_mirrored_pk('youtube_mirrored_') is None       # no digits
    assert extract_mirrored_pk('') is None
    assert extract_mirrored_pk(None) is None
