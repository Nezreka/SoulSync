"""`_audio_file_duration_ms` (#1509 duration gate input) must be fail-soft:
a missing mutagen, an unreadable file, or a nonsense length all mean
"unknown" (gate doesn't apply) — never an exception, never a bogus number.
"""

from __future__ import annotations

import types

import pytest

from core.metadata import source as ms


def _symbols(file_fn):
    return types.SimpleNamespace(File=file_fn)


def test_none_input_returns_none():
    assert ms._audio_file_duration_ms(None) is None


def test_missing_mutagen_returns_none(monkeypatch):
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: None)
    assert ms._audio_file_duration_ms("/music/song.flac") is None


def test_unreadable_file_returns_none(monkeypatch):
    def _boom(_path):
        raise OSError("cannot read file")

    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: _symbols(_boom))
    assert ms._audio_file_duration_ms("/music/song.flac") is None


def test_zero_or_negative_length_returns_none(monkeypatch):
    for length in (0.0, -3.2, None):
        audio = types.SimpleNamespace(info=types.SimpleNamespace(length=length))
        monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: _symbols(lambda _p: audio))
        assert ms._audio_file_duration_ms("/music/song.flac") is None


def test_valid_length_returns_milliseconds(monkeypatch):
    audio = types.SimpleNamespace(info=types.SimpleNamespace(length=270.5))
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: _symbols(lambda _p: audio))
    assert ms._audio_file_duration_ms("/music/song.flac") == 270500


def test_path_objects_are_accepted(monkeypatch):
    import pathlib

    audio = types.SimpleNamespace(info=types.SimpleNamespace(length=60.0))
    seen = {}

    def _file(path):
        seen["path"] = path
        return audio

    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: _symbols(_file))
    assert ms._audio_file_duration_ms(pathlib.Path("/music/song.mp3")) == 60000
    assert seen["path"] == "/music/song.mp3"
