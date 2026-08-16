"""#1091 through the REAL destination builder, on disk.

`test_case_folding.py` proves the resolver's rules. This proves the wiring:
that `build_final_path_for_track` — the single builder BOTH the download
pipeline and Reorganize use (reorganize calls it with create_dirs=False for
preview) — actually lands on the folder that already exists.

The unit tests would all pass with the resolver imported and never called.
"""

from __future__ import annotations

import os

import pytest

from core.imports.paths import build_final_path_for_track
from core.library_reorganize import _build_album_info, _build_post_process_context


@pytest.fixture(autouse=True)
def _transfer_dir(tmp_path, monkeypatch):
    """Point the builder at a temp library root."""
    root = tmp_path / 'Transfer'
    root.mkdir()

    class _Cfg:
        def get(self, key, default=None):
            if key == 'soulseek.transfer_path':
                return str(root)
            return default

        def get_active_media_server(self):
            return None

    monkeypatch.setattr('core.imports.paths._get_config_manager', lambda: _Cfg())
    monkeypatch.setattr('core.library_reorganize._preserve_casing_enabled', lambda: True)
    monkeypatch.setattr('core.library_reorganize._feat_in_title_enabled', lambda: False)
    return root


def _context(artist='Pink Floyd', album='The Wall', title='Another Brick'):
    return _build_post_process_context(
        {"id": "AL1", "name": album, "release_date": "1979-01-01",
         "total_tracks": 10, "images": [{"url": ""}]},
        {"name": title, "track_number": 1, "disc_number": 1,
         "artists": [{"name": artist}]},
        artist, album, 1, local_title=title)


def _build(create_dirs=False):
    ctx = _context()
    return build_final_path_for_track(
        ctx, ctx['spotify_artist'], _build_album_info(ctx), '.flac',
        create_dirs=create_dirs)[0]


def test_a_track_lands_in_the_folder_that_already_exists(_transfer_dir):
    """The reported bug. A lower-cased folder on disk must capture the write
    rather than a second, differently-cased folder appearing beside it."""
    existing = _transfer_dir / 'pink floyd' / 'pink floyd - the wall'
    existing.mkdir(parents=True)

    path = _build()

    assert os.path.dirname(path) == str(existing), (
        f'built {path!r} instead of joining the existing folder')


def test_no_second_folder_is_created_on_disk(_transfer_dir):
    """The half that matters on a case-sensitive filesystem: not just the
    right string, but no duplicate directory."""
    (_transfer_dir / 'pink floyd' / 'pink floyd - the wall').mkdir(parents=True)

    _build(create_dirs=True)

    artist_dirs = sorted(p.name for p in _transfer_dir.iterdir() if p.is_dir())
    assert artist_dirs == ['pink floyd'], f'a duplicate artist folder appeared: {artist_dirs}'


def test_a_fresh_library_still_gets_the_metadata_casing(_transfer_dir):
    """Nothing on disk means nothing to match, so the template's casing wins
    exactly as before."""
    path = _build(create_dirs=True)

    assert 'Pink Floyd' in path
    assert (_transfer_dir / 'Pink Floyd').is_dir()


def test_preview_does_not_create_anything(_transfer_dir):
    """Reorganize previews through this same call with create_dirs=False;
    #767 was empty folders appearing during preview."""
    _build(create_dirs=False)

    assert list(_transfer_dir.iterdir()) == []


def test_only_the_artist_folder_matching_still_reuses_it(_transfer_dir):
    (_transfer_dir / 'pink floyd').mkdir()

    path = _build(create_dirs=True)

    assert path.startswith(str(_transfer_dir / 'pink floyd'))
    assert sorted(p.name for p in _transfer_dir.iterdir()) == ['pink floyd']
