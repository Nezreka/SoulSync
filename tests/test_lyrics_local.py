"""The player's local lyrics: sidecar, embedded tags, [offset:], sidecar cache."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from core import lyrics_local
from core.lyrics_client import STATUS_FOUND, LyricsFetchResult


def _make_mp3(path):
    """A few real MPEG1 Layer III frames (128 kbps, 44.1 kHz) mutagen can open."""
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    path.write_bytes((header + bytes(417 - 4)) * 8)


def _make_flac(path):
    from mutagen.flac import FLAC
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    path.write_bytes(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(si) + bytes(range(256)) * 8)
    FLAC(str(path)).save()


def _mp3_with(path, *frames):
    from mutagen.id3 import ID3
    _make_mp3(path)
    tags = ID3()
    for frame in frames:
        tags.add(frame)
    tags.save(str(path))


# ── [offset:] ──────────────────────────────────────────────────────────────

def test_positive_offset_shows_lines_sooner():
    out = lyrics_local.apply_lrc_offset("[offset:+500]\n[00:10.00]a\n[01:00.25]b")
    assert out == "[00:09.50]a\n[00:59.75]b"


def test_negative_offset_shows_lines_later_and_never_below_zero():
    assert lyrics_local.apply_lrc_offset("[offset:-1500]\n[00:01.00]a") == "[00:02.50]a"
    assert lyrics_local.apply_lrc_offset("[offset:3000]\n[00:01.00]a") == "[00:00.00]a"


def test_no_offset_is_unchanged():
    text = "[ti:Song]\n[00:01.00]a"
    assert lyrics_local.apply_lrc_offset(text) == text


# ── embedded tags ────────────────────────────────────────────────────────

def test_mp3_uslt_plain(tmp_path):
    from mutagen.id3 import USLT
    song = tmp_path / "song.mp3"
    _mp3_with(song, USLT(encoding=3, lang="eng", desc="", text="hello\nworld"))
    assert lyrics_local.read_embedded(str(song)) == (None, "hello\nworld")


def test_mp3_uslt_with_stamps_is_synced(tmp_path):
    from mutagen.id3 import USLT
    song = tmp_path / "song.mp3"
    _mp3_with(song, USLT(encoding=3, lang="eng", desc="", text="[00:01.00]hello"))
    assert lyrics_local.read_embedded(str(song)) == ("[00:01.00]hello", None)


def test_mp3_sylt_becomes_lrc_and_beats_uslt(tmp_path):
    from mutagen.id3 import SYLT, USLT
    song = tmp_path / "song.mp3"
    _mp3_with(song,
              USLT(encoding=3, lang="eng", desc="", text="plain words"),
              SYLT(encoding=3, lang="eng", format=2, type=1, desc="",
                   text=[("first", 1000), ("second", 61500)]))
    synced, plain = lyrics_local.read_embedded(str(song))
    assert synced == "[00:01.00]first\n[01:01.50]second"
    assert plain is None


def test_flac_lyrics_and_unsyncedlyrics(tmp_path):
    from mutagen.flac import FLAC
    song = tmp_path / "song.flac"
    _make_flac(song)
    audio = FLAC(str(song))
    audio["UNSYNCEDLYRICS"] = ["just words"]
    audio.save()
    assert lyrics_local.read_embedded(str(song)) == (None, "just words")
    audio = FLAC(str(song))
    audio["LYRICS"] = ["[00:02.00]timed"]
    audio.save()
    assert lyrics_local.read_embedded(str(song)) == ("[00:02.00]timed", None)


def test_mp4_lyr(monkeypatch):
    from mutagen.mp4 import MP4
    fake = MP4.__new__(MP4)
    fake.tags = {"\xa9lyr": ["mp4 words"]}
    monkeypatch.setattr("mutagen.File", lambda *_a, **_k: fake)
    assert lyrics_local.read_embedded("/x/song.m4a") == (None, "mp4 words")


def test_untagged_or_unreadable_file_has_none(tmp_path):
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio")
    assert lyrics_local.read_embedded(str(junk)) == (None, None)


# ── read_local order ─────────────────────────────────────────────────────

def test_sidecar_lrc_wins_and_offset_applies(tmp_path):
    from mutagen.id3 import USLT
    song = tmp_path / "song.mp3"
    _mp3_with(song, USLT(encoding=3, lang="eng", desc="", text="[00:05.00]tag"))
    (tmp_path / "song.lrc").write_text("[offset:1000]\n[00:05.00]side", encoding="utf-8")
    assert lyrics_local.read_local(str(song)) == ("[00:04.00]side", None, "sidecar")


def test_embedded_synced_beats_plain_txt_sidecar(tmp_path):
    from mutagen.id3 import USLT
    song = tmp_path / "song.mp3"
    _mp3_with(song, USLT(encoding=3, lang="eng", desc="", text="[00:05.00]tag"))
    (tmp_path / "song.txt").write_text("plain side", encoding="utf-8")
    assert lyrics_local.read_local(str(song)) == ("[00:05.00]tag", None, "embedded")


def test_plain_txt_sidecar_used_when_tags_have_nothing(tmp_path):
    song = tmp_path / "song.mp3"
    _make_mp3(song)
    (tmp_path / "song.txt").write_text("plain side", encoding="utf-8")
    assert lyrics_local.read_local(str(song)) == (None, "plain side", "sidecar")


# ── sidecar cache ─────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, root, **values):
        self.root = str(root)
        self.values = values

    def get(self, key, default=None):
        if key == "library.music_paths":
            return [self.root]
        return self.values.get(key, default)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / "Music"
    (root / "Artist" / "Album").mkdir(parents=True)
    song = root / "Artist" / "Album" / "01 Song.mp3"
    _make_mp3(song)
    monkeypatch.setattr("core.library.path_resolver.library_base_dirs",
                        lambda cfg, **k: [cfg.root])
    monkeypatch.setattr("core.library2.locks.blocks_file", lambda *a, **k: False)
    return root, song


def test_cache_writes_lrc_inside_library(lib):
    root, song = lib
    assert lyrics_local.cache_sidecar(str(song), "[00:01.00]hi", None, _Cfg(root)) is True
    assert (song.parent / "01 Song.lrc").read_text(encoding="utf-8") == "[00:01.00]hi\n"


def test_cache_writes_plain_as_txt(lib):
    root, song = lib
    assert lyrics_local.cache_sidecar(str(song), None, "words", _Cfg(root)) is True
    assert (song.parent / "01 Song.txt").exists()
    assert not (song.parent / "01 Song.lrc").exists()


def test_cache_respects_both_settings(lib):
    root, song = lib
    off = _Cfg(root, **{"metadata_enhancement.lrclib_cache_player": False})
    assert lyrics_local.cache_sidecar(str(song), "[00:01.00]hi", None, off) is False
    no_lrc = _Cfg(root, **{"metadata_enhancement.lrclib_enabled": False})
    assert lyrics_local.cache_sidecar(str(song), "[00:01.00]hi", None, no_lrc) is False
    assert not (song.parent / "01 Song.lrc").exists()


def test_cache_never_writes_outside_the_library(lib, tmp_path):
    root, _song = lib
    outside = tmp_path / "elsewhere.mp3"
    _make_mp3(outside)
    assert lyrics_local.cache_sidecar(str(outside), "[00:01.00]hi", None, _Cfg(root)) is False
    assert not (tmp_path / "elsewhere.lrc").exists()


def test_cache_never_writes_beside_a_non_audio_file(lib):
    root, song = lib
    other = song.parent / "notes.cfg"
    other.write_text("x")
    assert lyrics_local.cache_sidecar(str(other), "[00:01.00]hi", None, _Cfg(root)) is False
    assert not (song.parent / "notes.lrc").exists()


def test_cache_keeps_an_existing_sidecar(lib):
    root, song = lib
    existing = song.parent / "01 Song.txt"
    existing.write_text("mine", encoding="utf-8")
    assert lyrics_local.cache_sidecar(str(song), "[00:01.00]hi", None, _Cfg(root)) is False
    assert existing.read_text(encoding="utf-8") == "mine"
    assert not (song.parent / "01 Song.lrc").exists()


def test_cache_skips_a_locked_album(lib, monkeypatch):
    root, song = lib
    monkeypatch.setattr("core.library2.locks.blocks_file", lambda *a, **k: True)
    assert lyrics_local.cache_sidecar(str(song), "[00:01.00]hi", None, _Cfg(root)) is False


# ── endpoint ─────────────────────────────────────────────────────────────

def _post(monkeypatch, client, cfg, **body):
    from flask import Flask
    import api.stats as stats
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    monkeypatch.setattr(stats, "config_manager", cfg, raising=False)
    app = Flask(__name__)
    app.register_blueprint(stats.bp)
    payload = {"title": "Song", "artist": "Artist", "album": "Album", "duration": 200}
    payload.update(body)
    return app.test_client().post("/api/lyrics/fetch", json=payload).get_json()


def _never_called(**_k):
    raise AssertionError("LRCLib must not be asked when the file has lyrics")


def test_endpoint_uses_embedded_lyrics_before_lrclib(lib, monkeypatch):
    from mutagen.id3 import ID3, USLT
    root, song = lib
    tags = ID3()
    tags.add(USLT(encoding=3, lang="eng", desc="", text="[offset:+200]\n[00:03.00]from tags"))
    tags.save(str(song))
    client = SimpleNamespace(api=object(), lookup_remote_lyrics=_never_called)
    body = _post(monkeypatch, client, _Cfg(root), file_path=str(song))
    assert body == {"success": True, "synced": "[00:02.80]from tags",
                    "plain": None, "source": "embedded"}


def test_endpoint_caches_an_lrclib_hit_and_serves_it_locally_next_time(lib, monkeypatch):
    root, song = lib
    hit = SimpleNamespace(synced_lyrics="[00:01.00]net", plain_lyrics="net")
    client = SimpleNamespace(api=object(),
                             lookup_remote_lyrics=lambda **k: LyricsFetchResult(STATUS_FOUND, data=hit))
    body = _post(monkeypatch, client, _Cfg(root), file_path=str(song))
    assert body["source"] == "lrclib" and body["cached"] is True
    assert (song.parent / "01 Song.lrc").exists()
    client2 = SimpleNamespace(api=object(), lookup_remote_lyrics=_never_called)
    again = _post(monkeypatch, client2, _Cfg(root), file_path=str(song))
    assert again["source"] == "sidecar" and again["synced"] == "[00:01.00]net"


def test_endpoint_does_not_cache_when_switched_off(lib, monkeypatch):
    root, song = lib
    hit = SimpleNamespace(synced_lyrics="[00:01.00]net", plain_lyrics=None)
    client = SimpleNamespace(api=object(),
                             lookup_remote_lyrics=lambda **k: LyricsFetchResult(STATUS_FOUND, data=hit))
    cfg = _Cfg(root, **{"metadata_enhancement.lrclib_cache_player": False})
    body = _post(monkeypatch, client, cfg, file_path=str(song))
    assert body["success"] is True and body["cached"] is False
    assert not (song.parent / "01 Song.lrc").exists()


def test_endpoint_applies_offset_to_lrclib_lyrics(monkeypatch):
    hit = SimpleNamespace(synced_lyrics="[offset:1000]\n[00:05.00]x", plain_lyrics=None)
    client = SimpleNamespace(api=object(),
                             lookup_remote_lyrics=lambda **k: LyricsFetchResult(STATUS_FOUND, data=hit))
    body = _post(monkeypatch, client, None)
    assert body["synced"] == "[00:04.00]x"
