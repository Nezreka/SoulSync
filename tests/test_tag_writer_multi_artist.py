"""Tests for the multi-value artist write path in tag_writer.

Issue #587 — the AcoustID scanner's "Apply Match" retag was bypassing
the user's `metadata_enhancement.tags.write_multi_artist` setting and
writing single-string TPE1 only. The repair-path retag now passes an
``artists_list`` to ``write_tags_to_file`` and the writer respects the
config flag the same way the post-download enrichment pipeline does.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
from mutagen.flac import FLAC, StreamInfo

from core.tag_writer import (
    _multi_artist_write_enabled,
    _resolve_artists_list_for_write,
    write_tags_to_file,
)


# ──────────────────────────────────────────────────────────────────────
# _resolve_artists_list_for_write — derives the multi-value list
# ──────────────────────────────────────────────────────────────────────

def test_resolves_artists_list_field():
    assert _resolve_artists_list_for_write({'artists_list': ['A', 'B']}) == ['A', 'B']


def test_resolves_artists_field_alias():
    assert _resolve_artists_list_for_write({'artists': ['A', 'B']}) == ['A', 'B']


def test_resolves_underscore_artists_list_field():
    # _artists_list — the post-process pipeline's internal field name
    assert _resolve_artists_list_for_write({'_artists_list': ['A', 'B']}) == ['A', 'B']


def test_returns_none_when_no_list_supplied():
    assert _resolve_artists_list_for_write({'artist_name': 'Solo'}) is None


def test_returns_none_for_non_list_input():
    assert _resolve_artists_list_for_write({'artists_list': 'A, B'}) is None
    assert _resolve_artists_list_for_write({'artists_list': {'A': 1}}) is None


def test_strips_empty_and_non_string_entries():
    out = _resolve_artists_list_for_write({'artists_list': ['A', '', None, '   ', 'B']})
    assert out == ['A', 'B']


def test_returns_none_when_list_only_has_empty_entries():
    assert _resolve_artists_list_for_write({'artists_list': ['', None, '   ']}) is None


# ──────────────────────────────────────────────────────────────────────
# _multi_artist_write_enabled — config gate
# ──────────────────────────────────────────────────────────────────────

def test_multi_artist_write_reads_config():
    with patch('config.settings.config_manager.get', return_value=True):
        assert _multi_artist_write_enabled() is True
    with patch('config.settings.config_manager.get', return_value=False):
        assert _multi_artist_write_enabled() is False


def test_multi_artist_write_swallows_config_error():
    with patch('config.settings.config_manager.get', side_effect=RuntimeError('boom')):
        assert _multi_artist_write_enabled() is False


# ──────────────────────────────────────────────────────────────────────
# Vorbis end-to-end — write multi-value to a real FLAC file
# ──────────────────────────────────────────────────────────────────────

def _make_minimal_flac(path):
    """Create a tiny but valid FLAC with mutagen's lowest-overhead
    stream info so we can write tags and read them back."""
    # Empty audio body — just enough to satisfy mutagen's parser.
    # 44-byte FLAC header + STREAMINFO block.
    minimal = (
        b'fLaC'
        + b'\x80\x00\x00\x22'           # last STREAMINFO block, length 34
        + b'\x00\x10\x00\x10'           # min/max block size
        + b'\x00\x00\x00\x00\x00\x00'   # min/max frame size
        + b'\x0a\xc4\x42\xf0\x00\x00\x00\x00'  # sample rate / channels / etc
        + b'\x00' * 16                   # MD5
    )
    with open(path, 'wb') as f:
        f.write(minimal)


@pytest.fixture
def flac_path():
    fd, path = tempfile.mkstemp(suffix='.flac')
    os.close(fd)
    _make_minimal_flac(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def test_multi_value_artists_key_written_when_setting_on(flac_path):
    with patch('config.settings.config_manager.get', return_value=True):
        result = write_tags_to_file(flac_path, {
            'title': 'Track',
            'artist_name': 'Artist A, Artist B',
            'artists_list': ['Artist A', 'Artist B'],
        }, embed_cover=False)

    assert result['success'] is True
    assert 'artists_multi' in result['written_fields']

    audio = FLAC(flac_path)
    # Display string preserved as `artist`
    assert audio.get('artist') == ['Artist A, Artist B']
    # Multi-value list written to `artists` key (Picard convention)
    assert audio.get('artists') == ['Artist A', 'Artist B']


def test_multi_value_artists_key_skipped_when_setting_off(flac_path):
    with patch('config.settings.config_manager.get', return_value=False):
        result = write_tags_to_file(flac_path, {
            'title': 'Track',
            'artist_name': 'Artist A, Artist B',
            'artists_list': ['Artist A', 'Artist B'],
        }, embed_cover=False)

    assert result['success'] is True
    assert 'artists_multi' not in result['written_fields']

    audio = FLAC(flac_path)
    assert audio.get('artist') == ['Artist A, Artist B']
    # No multi-value key when setting is off — backward compat
    assert audio.get('artists') is None


def test_single_artist_does_not_write_multi_value_key(flac_path):
    """When list has only one entry (or no list at all), don't
    write the multi-value key even if the setting is on. The point
    is to differentiate true multi-artist from single-artist tracks."""
    with patch('config.settings.config_manager.get', return_value=True):
        result = write_tags_to_file(flac_path, {
            'title': 'Track',
            'artist_name': 'Solo Artist',
            'artists_list': ['Solo Artist'],
        }, embed_cover=False)

    assert result['success'] is True
    assert 'artists_multi' not in result['written_fields']
    audio = FLAC(flac_path)
    assert audio.get('artists') is None


def test_no_artists_list_legacy_callers_unchanged(flac_path):
    """Backward compat — callers that don't supply artists_list get
    the same single-string write as before. No regression for the
    write_artist_image button or any other tag_writer caller."""
    with patch('config.settings.config_manager.get', return_value=True):
        result = write_tags_to_file(flac_path, {
            'title': 'Track',
            'artist_name': 'Solo Artist',
        }, embed_cover=False)

    assert result['success'] is True
    assert 'artists_multi' not in result['written_fields']
    audio = FLAC(flac_path)
    assert audio.get('artists') is None
