"""Tests for re-embedding lyrics from an existing sidecar file.

Discord report (Netti93): retag was clearing the LYRICS / USLT tag
without rewriting it. Cause was two-fold:

1. `core/library/retag.py:execute_retag` never called
   `generate_lrc_file` after `enhance_file_metadata`. The download
   pipeline does — retag was inconsistent.
2. Even with the call added, `lyrics_client.create_lrc_file` used to
   short-circuit when an .lrc / .txt sidecar already existed (the
   typical retag case — sidecar moved alongside the audio file).
   Pre-fix: returned True without re-embedding USLT. Post-fix: reads
   the existing sidecar and re-embeds the USLT tag.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def fake_audio_file(tmp_path):
    """Build a minimal FLAC file with no LYRICS tag."""
    fd, path = tempfile.mkstemp(suffix='.flac', dir=str(tmp_path))
    os.close(fd)
    minimal = (
        b'fLaC'
        + b'\x80\x00\x00\x22'
        + b'\x00\x10\x00\x10'
        + b'\x00\x00\x00\x00\x00\x00'
        + b'\x0a\xc4\x42\xf0\x00\x00\x00\x00'
        + b'\x00' * 16
    )
    with open(path, 'wb') as f:
        f.write(minimal)
    yield path


# ──────────────────────────────────────────────────────────────────────
# create_lrc_file — re-embed when sidecar present
# ──────────────────────────────────────────────────────────────────────

def test_existing_lrc_sidecar_triggers_reembed(fake_audio_file):
    """The exact retag scenario — sidecar already exists alongside the
    audio file (moved during retag), USLT got cleared by enrichment.
    Helper should read the sidecar and re-embed without hitting LRClib."""
    from core.lyrics_client import LyricsClient

    sidecar_path = os.path.splitext(fake_audio_file)[0] + '.lrc'
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        f.write('[00:01.00]Test lyric line\n[00:05.00]Second line')

    client = LyricsClient()
    client.api = MagicMock()  # API stub — should NOT be called
    client._embed_lyrics = MagicMock()

    result = client.create_lrc_file(
        audio_file_path=fake_audio_file,
        track_name='Test',
        artist_name='Artist',
    )

    assert result is True
    # API never hit — sidecar shortcut
    client.api.get_lyrics.assert_not_called()
    client.api.search_lyrics.assert_not_called()
    # USLT was re-embedded
    client._embed_lyrics.assert_called_once()
    call_args = client._embed_lyrics.call_args
    assert call_args.args[0] == fake_audio_file
    assert 'Test lyric line' in call_args.args[1]


def test_existing_txt_sidecar_also_triggers_reembed(fake_audio_file):
    """Same shape with .txt sidecar (plain lyrics, no timestamps)."""
    from core.lyrics_client import LyricsClient

    sidecar_path = os.path.splitext(fake_audio_file)[0] + '.txt'
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        f.write('Just plain lyrics no timestamps')

    client = LyricsClient()
    client.api = MagicMock()
    client._embed_lyrics = MagicMock()

    result = client.create_lrc_file(
        audio_file_path=fake_audio_file,
        track_name='T', artist_name='A',
    )

    assert result is True
    client._embed_lyrics.assert_called_once_with(
        fake_audio_file, 'Just plain lyrics no timestamps'
    )


def test_empty_sidecar_does_not_embed(fake_audio_file):
    """Defensive — if the sidecar exists but is empty, don't write an
    empty USLT tag."""
    from core.lyrics_client import LyricsClient

    sidecar_path = os.path.splitext(fake_audio_file)[0] + '.lrc'
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        f.write('   \n  ')  # whitespace only

    client = LyricsClient()
    client.api = MagicMock()
    client._embed_lyrics = MagicMock()

    result = client.create_lrc_file(
        audio_file_path=fake_audio_file,
        track_name='T', artist_name='A',
    )

    assert result is True
    client._embed_lyrics.assert_not_called()


def test_unreadable_sidecar_swallows_error_returns_true(fake_audio_file):
    """If the sidecar is somehow unreadable, return True (don't try
    LRClib again — the early-return contract holds), just skip the
    embed silently."""
    from core.lyrics_client import LyricsClient

    sidecar_path = os.path.splitext(fake_audio_file)[0] + '.lrc'
    with open(sidecar_path, 'wb') as f:
        f.write(b'\xff\xfe\x00\x00')  # invalid UTF-8

    client = LyricsClient()
    client.api = MagicMock()
    client._embed_lyrics = MagicMock()

    result = client.create_lrc_file(
        audio_file_path=fake_audio_file,
        track_name='T', artist_name='A',
    )

    assert result is True
    client.api.get_lyrics.assert_not_called()


def test_no_sidecar_falls_through_to_lrclib(fake_audio_file):
    """No sidecar → original LRClib fetch path runs (download flow)."""
    from core.lyrics_client import LyricsClient

    client = LyricsClient()
    fake_lyrics = MagicMock()
    fake_lyrics.synced_lyrics = '[00:01.00]synced from api'
    fake_lyrics.plain_lyrics = None
    client.api = MagicMock()
    client.api.get_lyrics.return_value = None
    client.api.search_lyrics.return_value = [fake_lyrics]
    client._embed_lyrics = MagicMock()

    result = client.create_lrc_file(
        audio_file_path=fake_audio_file,
        track_name='T', artist_name='A',
    )

    assert result is True
    client.api.search_lyrics.assert_called_once()
    # Sidecar was created
    lrc = os.path.splitext(fake_audio_file)[0] + '.lrc'
    assert os.path.exists(lrc)
    # And USLT was embedded
    client._embed_lyrics.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# RetagDeps integration — generate_lrc_file is now wired
# ──────────────────────────────────────────────────────────────────────

def test_retagdeps_accepts_generate_lrc_file_field():
    from core.library.retag import RetagDeps

    # Mock the required + optional deps with do-nothing callables
    deps = RetagDeps(
        config_manager=MagicMock(),
        retag_lock=MagicMock(),
        spotify_client=MagicMock(),
        get_audio_quality_string=lambda *a: '',
        enhance_file_metadata=lambda *a: True,
        build_final_path_for_track=lambda *a: ('', ''),
        safe_move_file=lambda *a: None,
        cleanup_empty_directories=lambda *a: None,
        download_cover_art=lambda *a: None,
        docker_resolve_path=lambda x: x,
        _get_retag_state=lambda: {},
        _set_retag_state=lambda v: None,
        get_database=lambda: MagicMock(),
        generate_lrc_file=lambda *a: True,
    )
    assert callable(deps.generate_lrc_file)


def test_retagdeps_generate_lrc_file_optional_for_backward_compat():
    """Tests that built RetagDeps without the new field don't break."""
    from core.library.retag import RetagDeps

    deps = RetagDeps(
        config_manager=MagicMock(),
        retag_lock=MagicMock(),
        spotify_client=MagicMock(),
        get_audio_quality_string=lambda *a: '',
        enhance_file_metadata=lambda *a: True,
        build_final_path_for_track=lambda *a: ('', ''),
        safe_move_file=lambda *a: None,
        cleanup_empty_directories=lambda *a: None,
        download_cover_art=lambda *a: None,
        docker_resolve_path=lambda x: x,
        _get_retag_state=lambda: {},
        _set_retag_state=lambda v: None,
        get_database=lambda: MagicMock(),
    )
    # Field defaults to None — no crash on construction.
    assert deps.generate_lrc_file is None
