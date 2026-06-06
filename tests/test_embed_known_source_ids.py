"""Seam test: embed_known_source_ids builds the right id_tags and routes them
through the canonical frame writer (no API re-fetch)."""

import sys
import types
from types import SimpleNamespace

# Stub spotipy/config so core.metadata.source imports cleanly in tests.
if 'spotipy' not in sys.modules:
    sp = types.ModuleType('spotipy'); oa = types.ModuleType('spotipy.oauth2')
    sp.Spotify = type('S', (), {}); oa.SpotifyOAuth = oa.SpotifyClientCredentials = type('O', (), {})
    sp.oauth2 = oa; sys.modules['spotipy'] = sp; sys.modules['spotipy.oauth2'] = oa
if 'config.settings' not in sys.modules:
    cm = types.ModuleType('config'); sm = types.ModuleType('config.settings')

    class _Cfg:
        def get(self, k, d=None): return d
        def get_active_media_server(self): return 'plex'
    sm.config_manager = _Cfg(); cm.settings = sm
    sys.modules['config'] = cm; sys.modules['config.settings'] = sm

from core.metadata import source as src


def test_builds_id_tags_from_flat_keys_and_calls_writer(monkeypatch):
    captured = {}
    monkeypatch.setattr(src, 'get_mutagen_symbols', lambda: SimpleNamespace())
    monkeypatch.setattr(src, 'get_config_manager', lambda: SimpleNamespace(get=lambda k, d=None: d))

    def _fake_write(audio, metadata, pp, cfg, symbols):
        captured['id_tags'] = dict(pp['id_tags'])
    monkeypatch.setattr(src, '_write_embedded_metadata', _fake_write)

    meta = {
        'spotify_track_id': 'sp_t', 'spotify_album_id': 'sp_a',
        'itunes_track_id': 'it_t',
        'musicbrainz_recording_id': 'mb_rec', 'musicbrainz_release_id': 'mb_rel',
    }
    written = src.embed_known_source_ids(object(), meta)

    assert captured['id_tags']['SPOTIFY_TRACK_ID'] == 'sp_t'
    assert captured['id_tags']['SPOTIFY_ALBUM_ID'] == 'sp_a'
    assert captured['id_tags']['ITUNES_TRACK_ID'] == 'it_t'
    assert captured['id_tags']['MUSICBRAINZ_RECORDING_ID'] == 'mb_rec'
    assert captured['id_tags']['MUSICBRAINZ_RELEASE_ID'] == 'mb_rel'
    assert set(written) >= {'SPOTIFY_TRACK_ID', 'MUSICBRAINZ_RECORDING_ID'}


def test_no_ids_writes_nothing(monkeypatch):
    called = {'n': 0}
    monkeypatch.setattr(src, 'get_mutagen_symbols', lambda: SimpleNamespace())
    monkeypatch.setattr(src, 'get_config_manager', lambda: SimpleNamespace(get=lambda k, d=None: d))
    monkeypatch.setattr(src, '_write_embedded_metadata',
                        lambda *a, **k: called.__setitem__('n', called['n'] + 1))
    assert src.embed_known_source_ids(object(), {'title': 'x'}) == []
    assert called['n'] == 0      # nothing to embed → writer never called


def test_musicbrainz_gated_off(monkeypatch):
    captured = {}
    monkeypatch.setattr(src, 'get_mutagen_symbols', lambda: SimpleNamespace())
    # mb embed disabled
    monkeypatch.setattr(src, 'get_config_manager',
                        lambda: SimpleNamespace(get=lambda k, d=None: False if k == 'musicbrainz.embed_tags' else d))
    monkeypatch.setattr(src, '_write_embedded_metadata',
                        lambda audio, m, pp, cfg, sym: captured.update(pp['id_tags']))
    src.embed_known_source_ids(object(), {'spotify_track_id': 'sp', 'musicbrainz_recording_id': 'mb'})
    assert 'SPOTIFY_TRACK_ID' in captured
    assert 'MUSICBRAINZ_RECORDING_ID' not in captured   # gated off
