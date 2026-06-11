"""Tests for playlist-folder existence detection."""

import os
from unittest.mock import patch

import pytest

from core.downloads.playlist_folder import (
    candidate_playlist_folder_paths,
    effective_keep_playlist_folder_copies,
    resolve_playlist_folder_mode_for_batch,
    resolve_wishlist_track_playlist_folder_mode,
    track_exists_in_playlist_folder,
)


class _FakeDB:
    def __init__(self, mirrored=None):
        self._mirrored = mirrored

    def resolve_mirrored_playlist(self, playlist_ref, profile_id=1, default_source='spotify'):
        return self._mirrored


def test_track_exists_in_playlist_folder_finds_file(tmp_path):
    playlist_dir = tmp_path / 'My Playlist'
    playlist_dir.mkdir()
    track_file = playlist_dir / 'Artist A - Song One.flac'
    track_file.write_bytes(b'x')

    with patch('core.downloads.playlist_folder._get_config_manager') as cfg:
        cfg.return_value.get.return_value = str(tmp_path)
        with patch('core.downloads.playlist_folder.docker_resolve_path', side_effect=lambda p: p):
            with patch(
                'core.downloads.playlist_folder.get_file_path_from_template',
                return_value=('', ''),
            ):
                assert track_exists_in_playlist_folder('My Playlist', 'Artist A', 'Song One')


def test_track_exists_in_playlist_folder_case_insensitive(tmp_path):
    """File stored as 'HUGEL - Song.flac' must be detected when the lookup
    uses lowercase 'hugel' — providers often return artist names with different
    casing on different calls, which would cause spurious re-downloads."""
    playlist_dir = tmp_path / 'My Playlist'
    playlist_dir.mkdir()
    (playlist_dir / 'HUGEL - Song One.flac').write_bytes(b'x')

    with patch('core.downloads.playlist_folder._get_config_manager') as cfg:
        cfg.return_value.get.return_value = str(tmp_path)
        with patch('core.downloads.playlist_folder.docker_resolve_path', side_effect=lambda p: p):
            with patch(
                'core.downloads.playlist_folder.get_file_path_from_template',
                return_value=('', ''),
            ):
                # Lowercase artist lookup must still find the UPPER-CASE file
                assert track_exists_in_playlist_folder('My Playlist', 'hugel', 'Song One')


def test_track_exists_in_playlist_folder_missing(tmp_path):
    with patch('core.downloads.playlist_folder._get_config_manager') as cfg:
        cfg.return_value.get.return_value = str(tmp_path)
        with patch('core.downloads.playlist_folder.docker_resolve_path', side_effect=lambda p: p):
            with patch(
                'core.downloads.playlist_folder.get_file_path_from_template',
                return_value=('', ''),
            ):
                assert not track_exists_in_playlist_folder('My Playlist', 'Artist A', 'Song One')


def test_candidate_paths_template_layout(tmp_path):
    with patch('core.downloads.playlist_folder._get_config_manager') as cfg:
        cfg.return_value.get.return_value = str(tmp_path)
        with patch('core.downloads.playlist_folder.docker_resolve_path', side_effect=lambda p: p):
            with patch(
                'core.downloads.playlist_folder.get_file_path_from_template',
                return_value=('Cool Mix', 'Artist - Title'),
            ):
                paths = candidate_playlist_folder_paths('Cool Mix', 'Artist', 'Title')
                assert any(p.endswith('.flac') for p in paths)
                assert all('Cool Mix' in p for p in paths)


def test_resolve_playlist_folder_mode_from_mirrored():
    db = _FakeDB(mirrored={
        'id': 5,
        'name': 'Rekordbox Set',
        'organize_by_playlist': True,
    })
    enabled, name, keep = resolve_playlist_folder_mode_for_batch(
        db,
        playlist_id='37i9dQZF1',
        playlist_name='Other Name',
        batch_playlist_folder_mode=False,
    )
    assert enabled is True
    assert name == 'Rekordbox Set'
    assert keep is False


def test_resolve_playlist_folder_mode_batch_flag():
    db = _FakeDB()
    enabled, name, keep = resolve_playlist_folder_mode_for_batch(
        db,
        playlist_id='1',
        playlist_name='Batch Name',
        batch_playlist_folder_mode=True,
    )
    assert enabled is True
    assert name == 'Batch Name'
    assert keep is False


def test_resolve_playlist_folder_keep_copies_from_mirrored():
    db = _FakeDB(mirrored={
        'id': 5,
        'name': 'USB Set',
        'organize_by_playlist': True,
        'keep_playlist_folder_copies': True,
    })
    enabled, name, keep = resolve_playlist_folder_mode_for_batch(
        db,
        playlist_id='37i9dQZF1',
        playlist_name='Other',
        batch_playlist_folder_mode=False,
        active_server='soulsync',
    )
    assert enabled is True
    assert name == 'USB Set'
    assert keep is True


def test_standalone_defaults_keep_copies_when_organize_without_explicit_keep():
    mirrored = {
        'id': 5,
        'name': 'USB Set',
        'organize_by_playlist': True,
        'keep_playlist_folder_copies': False,
        'keep_playlist_folder_copies_opt_out': False,
    }
    assert effective_keep_playlist_folder_copies(mirrored, 'soulsync') is True
    assert effective_keep_playlist_folder_copies(mirrored, 'plex') is False


def test_standalone_keep_copies_opt_out_honored():
    mirrored = {
        'organize_by_playlist': True,
        'keep_playlist_folder_copies': False,
        'keep_playlist_folder_copies_opt_out': True,
    }
    assert effective_keep_playlist_folder_copies(mirrored, 'soulsync') is False


def test_wishlist_organize_flag_in_source_info_enables_folder_mode():
    enabled, name = resolve_wishlist_track_playlist_folder_mode(
        {
            'playlist_id': '37i9dQZF1DX',
            'playlist_name': 'Daily Mix',
            'organize_by_playlist': True,
            'playlist_source': 'spotify',
        },
        _FakeDB(),
    )
    assert enabled is True
    assert name == 'Daily Mix'


def test_wishlist_resolves_mirrored_playlist_via_playlist_source():
    db = _FakeDB(mirrored={
        'id': 9,
        'name': 'Summer Mix',
        'organize_by_playlist': True,
    })
    enabled, name = resolve_wishlist_track_playlist_folder_mode(
        {
            'playlist_id': '12345',
            'playlist_name': 'Summer Mix',
            'playlist_source': 'deezer',
        },
        db,
    )
    assert enabled is True
    assert name == 'Summer Mix'
