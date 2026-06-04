"""Tests for core.metadata.art_apply — on-disk art detection + applying art."""

import sys
import types
from types import SimpleNamespace

# Stubs so core.metadata.artwork (pulled in transitively) imports without the
# real Spotify / config dependencies (mirrors test_missing_cover_art.py).
if 'spotipy' not in sys.modules:
    spotipy = types.ModuleType('spotipy')
    oauth2 = types.ModuleType('spotipy.oauth2')
    spotipy.Spotify = type('S', (), {})
    oauth2.SpotifyOAuth = oauth2.SpotifyClientCredentials = type('O', (), {})
    spotipy.oauth2 = oauth2
    sys.modules['spotipy'] = spotipy
    sys.modules['spotipy.oauth2'] = oauth2

if 'config.settings' not in sys.modules:
    config_mod = types.ModuleType('config')
    settings_mod = types.ModuleType('config.settings')

    class _Cfg:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return 'plex'

    settings_mod.config_manager = _Cfg()
    config_mod.settings = settings_mod
    sys.modules['config'] = config_mod
    sys.modules['config.settings'] = settings_mod

from core.metadata import art_apply as aa


# ── sidecar detection ──

def test_folder_has_cover_sidecar(tmp_path):
    assert aa.folder_has_cover_sidecar(str(tmp_path)) is False
    (tmp_path / 'cover.jpg').write_bytes(b'x')
    assert aa.folder_has_cover_sidecar(str(tmp_path)) is True


def test_album_has_art_on_disk_no_local_file_is_true():
    # No representative file (e.g. media-server-only album) → not flagged.
    assert aa.album_has_art_on_disk('') is True
    assert aa.album_has_art_on_disk(None) is True


def test_album_has_art_on_disk_sidecar_short_circuits(tmp_path, monkeypatch):
    (tmp_path / 'cover.jpg').write_bytes(b'x')
    track = tmp_path / '01 song.flac'
    track.write_bytes(b'')
    # Sidecar present → True without ever opening the audio file.
    called = {'n': 0}
    monkeypatch.setattr(aa, 'file_has_embedded_art', lambda p: called.__setitem__('n', called['n'] + 1) or False)
    assert aa.album_has_art_on_disk(str(track)) is True
    assert called['n'] == 0


def test_album_has_art_on_disk_no_sidecar_checks_file(tmp_path, monkeypatch):
    track = tmp_path / '01 song.flac'
    track.write_bytes(b'')
    monkeypatch.setattr(aa, 'file_has_embedded_art', lambda p: False)
    assert aa.album_has_art_on_disk(str(track)) is False
    monkeypatch.setattr(aa, 'file_has_embedded_art', lambda p: True)
    assert aa.album_has_art_on_disk(str(track)) is True


# ── embedded-art detection ──

def _fake_symbols(audio):
    return SimpleNamespace(
        File=lambda path: audio,
        ID3=type('ID3', (), {}),
        MP4=type('MP4', (), {}),
        FLAC=type('FLAC', (), {}),
    )


def test_file_has_embedded_art_flac_picture(tmp_path, monkeypatch):
    f = tmp_path / 'a.flac'
    f.write_bytes(b'')
    audio = SimpleNamespace(pictures=['pic'], tags=None)
    monkeypatch.setattr(aa, 'get_mutagen_symbols', lambda: _fake_symbols(audio))
    assert aa.file_has_embedded_art(str(f)) is True


def test_file_has_embedded_art_none(tmp_path, monkeypatch):
    f = tmp_path / 'a.flac'
    f.write_bytes(b'')
    audio = SimpleNamespace(pictures=[], tags=None)
    monkeypatch.setattr(aa, 'get_mutagen_symbols', lambda: _fake_symbols(audio))
    assert aa.file_has_embedded_art(str(f)) is False


# ── applying art ──

def test_apply_embeds_into_each_file_and_writes_cover(tmp_path, monkeypatch):
    f1 = tmp_path / '01.flac'; f1.write_bytes(b'')
    f2 = tmp_path / '02.flac'; f2.write_bytes(b'')

    saved = []
    audio = SimpleNamespace(tags=None, add_tags=lambda: None, save=lambda: saved.append(True))
    monkeypatch.setattr(aa, 'get_mutagen_symbols', lambda: _fake_symbols(audio))

    embed_calls = []
    monkeypatch.setattr(aa, 'embed_album_art_metadata', lambda a, m: embed_calls.append(m) or True)
    # download_cover_art is the standard cover.jpg writer — stub it to drop one.
    monkeypatch.setattr(aa, 'download_cover_art',
                        lambda album_info, folder, ctx=None: open(f"{folder}/cover.jpg", 'wb').close())

    meta = {'artist': 'A', 'album': 'B', 'album_art_url': 'http://x/y.jpg'}
    res = aa.apply_art_to_album_files([str(f1), str(f2)], meta, {'album_name': 'B'}, folder=str(tmp_path))

    assert res['embedded'] == 2
    assert len(saved) == 2          # each file saved after embed
    assert res['cover_written'] is True


def test_apply_counts_failures_without_raising(tmp_path, monkeypatch):
    f1 = tmp_path / '01.flac'; f1.write_bytes(b'')
    audio = SimpleNamespace(tags=object(), save=lambda: (_ for _ in ()).throw(OSError('read-only')))
    monkeypatch.setattr(aa, 'get_mutagen_symbols', lambda: _fake_symbols(audio))
    monkeypatch.setattr(aa, 'embed_album_art_metadata', lambda a, m: True)
    monkeypatch.setattr(aa, 'download_cover_art', lambda *a, **k: None)

    res = aa.apply_art_to_album_files([str(f1)], {'album': 'B'}, {'album_name': 'B'}, folder=str(tmp_path))
    assert res['embedded'] == 0
    assert res['failed'] == 1       # save() raised (read-only) — counted, not crashed
