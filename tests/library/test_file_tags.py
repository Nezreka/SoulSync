"""Pin `read_embedded_tags` — pure mutagen reader backing the audit
trail's "Embedded Tags" section.

Tests use mock mutagen objects to verify the extraction logic
without needing real audio fixtures checked in. The reader handles
three container families:

- ID3 (MP3): text frames keyed by 4-letter codes + TXXX user-defined
  frames keyed by `desc`.
- Vorbis-like (FLAC, OGG, OPUS): dict-like tags, lowercase keys,
  list-of-strings values.
- MP4: dict-like with weird atom keys including the iTunes
  ``----:com.apple.iTunes:`` freeform atoms.

Every test pins ONE behavior — easier to debug when one regresses.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Boundary cases — bad inputs, missing files, mutagen returns None
# ---------------------------------------------------------------------------


def test_returns_unavailable_for_empty_path():
    from core.library.file_tags import read_embedded_tags
    result = read_embedded_tags('')
    assert result['available'] is False
    assert 'No file path' in result['reason']


def test_returns_unavailable_for_none():
    from core.library.file_tags import read_embedded_tags
    result = read_embedded_tags(None)  # type: ignore[arg-type]
    assert result['available'] is False


def test_returns_unavailable_when_file_missing(tmp_path):
    from core.library.file_tags import read_embedded_tags
    fake = tmp_path / 'gone.mp3'
    result = read_embedded_tags(str(fake))
    assert result['available'] is False
    assert 'no longer exists' in result['reason']


def test_returns_unavailable_when_mutagen_returns_none(tmp_path):
    """File exists but mutagen can't recognise the format — should
    fall through to a clear `available: false` rather than raising."""
    real = tmp_path / 'garbage.txt'
    real.write_bytes(b'not audio')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.File.return_value = None
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    assert result['available'] is False
    assert 'not recognised' in result['reason']


def test_mutagen_open_exception_swallowed(tmp_path):
    """Mutagen raises on a malformed file — caller still gets a
    clean error dict, no propagated exception."""
    real = tmp_path / 'malformed.mp3'
    real.write_bytes(b'not really an mp3')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.File.side_effect = RuntimeError('mutagen blew up')
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    assert result['available'] is False
    assert 'Could not open file' in result['reason']
    assert 'mutagen blew up' in result['reason']


# ---------------------------------------------------------------------------
# ID3 path (MP3) — TIT2/TPE1/TALB + TXXX user-defined frames
# ---------------------------------------------------------------------------


def _build_id3_audio(symbols, frames, txxx_frames=None, pictures=False):
    """Helper to build a fake mutagen ID3 audio object.

    `frames` is a dict of {code: text}. `txxx_frames` is a list of
    (desc, text) tuples for user-defined ID3 frames.
    """
    tags = MagicMock()
    tags.__class__ = symbols.ID3
    frame_map = {}
    for code, text in frames.items():
        f = SimpleNamespace(text=[text])
        frame_map[code] = f
    tags.get.side_effect = lambda code: frame_map.get(code)

    def _getall(code):
        if code == 'TXXX':
            return [SimpleNamespace(desc=d, text=[t]) for d, t in (txxx_frames or [])]
        if code == 'USLT':
            return []
        if code == 'APIC':
            return [object()] if pictures else []
        return []

    tags.getall.side_effect = _getall
    audio = MagicMock()
    audio.tags = tags
    audio.info = SimpleNamespace(bitrate=320000, length=204.5)
    type(audio).__name__ = 'MP3'
    return audio


def test_id3_extracts_core_text_frames(tmp_path):
    real = tmp_path / 't.mp3'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = MagicMock  # isinstance check uses this
        audio = _build_id3_audio(symbols, frames={
            'TIT2': 'Without Me',
            'TPE1': 'Eminem',
            'TPE2': 'Eminem',
            'TALB': 'The Eminem Show',
            'TDRC': '2002',
            'TCON': 'Hip-Hop',
            'TRCK': '10/20',
            'TPOS': '1',
        })
        symbols.MP4 = type('MP4', (), {})  # not an MP4
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))

    assert result['available'] is True
    assert result['tags']['title'] == 'Without Me'
    assert result['tags']['artist'] == 'Eminem'
    assert result['tags']['album_artist'] == 'Eminem'
    assert result['tags']['album'] == 'The Eminem Show'
    assert result['tags']['date'] == '2002'
    assert result['tags']['genre'] == 'Hip-Hop'
    assert result['tags']['tracknumber'] == '10/20'
    assert result['tags']['discnumber'] == '1'


def test_id3_extracts_txxx_known_descriptions(tmp_path):
    """Source IDs land in TXXX frames keyed by description. Reader
    maps known descs to friendly snake_case keys."""
    real = tmp_path / 't.mp3'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = MagicMock
        symbols.MP4 = type('MP4', (), {})
        audio = _build_id3_audio(symbols, frames={'TIT2': 'X'}, txxx_frames=[
            ('Spotify Track Id', 'sp_abc'),
            ('MusicBrainz Release Group Id', 'mb_def'),
            ('replaygain_track_gain', '-9.90 dB'),
            ('replaygain_track_peak', '1.161449'),
        ])
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))

    assert result['tags']['spotify_track_id'] == 'sp_abc'
    assert result['tags']['musicbrainz_releasegroupid'] == 'mb_def'
    assert result['tags']['replaygain_track_gain'] == '-9.90 dB'
    assert result['tags']['replaygain_track_peak'] == '1.161449'


def test_id3_unknown_txxx_desc_falls_back_to_snake_case(tmp_path):
    real = tmp_path / 't.mp3'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = MagicMock
        symbols.MP4 = type('MP4', (), {})
        audio = _build_id3_audio(symbols, frames={'TIT2': 'X'}, txxx_frames=[
            ('Custom Vendor Field', 'foo'),
        ])
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    # Unknown desc → lowercased + underscored
    assert result['tags']['custom_vendor_field'] == 'foo'


def test_id3_detects_apic_cover_art(tmp_path):
    real = tmp_path / 't.mp3'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = MagicMock
        symbols.MP4 = type('MP4', (), {})
        audio = _build_id3_audio(symbols, frames={'TIT2': 'X'}, pictures=True)
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    assert result['has_picture'] is True


# ---------------------------------------------------------------------------
# Vorbis-like (FLAC) — dict-style lowercase keys, list values
# ---------------------------------------------------------------------------


def test_vorbis_passes_through_whitelisted_keys(tmp_path):
    real = tmp_path / 't.flac'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        # Not ID3, not MP4 — falls through to the vorbis branch.
        symbols.ID3 = type('ID3', (), {})
        symbols.MP4 = type('MP4', (), {})
        tags = {
            'title': ['Teenage Dream'],
            'artist': ['Katy Perry'],
            'album': ['Teenage Dream'],
            'date': ['2010'],
            'isrc': ['USCA21001255'],
            'musicbrainz_albumid': ['mb-album-id'],
            'tidal_track_id': ['14165831'],
            'unrelated_internal_key': ['skip-me'],
        }
        audio = MagicMock()
        audio.tags = tags
        audio.info = SimpleNamespace(bitrate=900000, length=180.0)
        audio.pictures = []
        type(audio).__name__ = 'FLAC'
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))

    assert result['tags']['title'] == 'Teenage Dream'
    assert result['tags']['artist'] == 'Katy Perry'
    assert result['tags']['isrc'] == 'USCA21001255'
    assert result['tags']['musicbrainz_albumid'] == 'mb-album-id'
    assert result['tags']['tidal_track_id'] == '14165831'
    # Non-whitelisted, non-_id/_url keys are dropped.
    assert 'unrelated_internal_key' not in result['tags']


def test_vorbis_pass_through_for_unknown_id_url_keys(tmp_path):
    """Vendor-prefixed `*_id` / `*_url` keys should pass through even
    if they're not in the explicit whitelist — covers future
    enrichment workers we haven't anticipated."""
    real = tmp_path / 't.flac'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = type('ID3', (), {})
        symbols.MP4 = type('MP4', (), {})
        tags = {
            'title': ['X'],
            'beatport_track_id': ['bp_xyz'],
            'songkick_url': ['https://...'],
        }
        audio = MagicMock()
        audio.tags = tags
        audio.info = SimpleNamespace(bitrate=900000, length=1.0)
        audio.pictures = []
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    assert result['tags']['beatport_track_id'] == 'bp_xyz'
    assert result['tags']['songkick_url'] == 'https://...'


def test_vorbis_detects_pictures(tmp_path):
    real = tmp_path / 't.flac'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = type('ID3', (), {})
        symbols.MP4 = type('MP4', (), {})
        audio = MagicMock()
        audio.tags = {'title': ['X']}
        audio.info = SimpleNamespace(bitrate=900000, length=1.0)
        audio.pictures = [object()]  # one embedded image
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    assert result['has_picture'] is True


# ---------------------------------------------------------------------------
# Format + bitrate metadata
# ---------------------------------------------------------------------------


def test_returns_format_and_bitrate(tmp_path):
    real = tmp_path / 't.mp3'
    real.write_bytes(b'\x00')
    from core.library import file_tags as ft
    with patch.object(ft, 'get_mutagen_symbols') as g:
        symbols = MagicMock()
        symbols.ID3 = MagicMock
        symbols.MP4 = type('MP4', (), {})
        audio = _build_id3_audio(symbols, frames={'TIT2': 'X'})
        type(audio).__name__ = 'MP3'  # mutagen exposes class name
        audio.info = SimpleNamespace(bitrate=320000, length=204.5)
        symbols.File.return_value = audio
        g.return_value = symbols
        result = ft.read_embedded_tags(str(real))
    assert result['format'] == 'MP3'
    assert result['bitrate'] == 320000
    assert result['duration'] == pytest.approx(204.5)


# ---------------------------------------------------------------------------
# Stringify defensive cases
# ---------------------------------------------------------------------------


class TestStringify:
    def test_list_of_strings_joined(self):
        from core.library.file_tags import _stringify
        assert _stringify(['a', 'b', 'c']) == 'a, b, c'

    def test_tuple_pair_joined_with_slash(self):
        """MP4 trkn / disk values come as (current, total) tuples."""
        from core.library.file_tags import _stringify
        assert _stringify([(10, 20)]) == '10/20'

    def test_int_coerced_to_string(self):
        from core.library.file_tags import _stringify
        assert _stringify(42) == '42'

    def test_none_returns_empty(self):
        from core.library.file_tags import _stringify
        assert _stringify(None) == ''

    def test_frame_with_text_attribute_unwrapped(self):
        """mutagen frames expose `.text` as a list of strings."""
        from core.library.file_tags import _stringify
        frame = SimpleNamespace(text=['Title Here'])
        assert _stringify(frame) == 'Title Here'

    def test_whitespace_stripped(self):
        from core.library.file_tags import _stringify
        assert _stringify('  spaced  ') == 'spaced'
