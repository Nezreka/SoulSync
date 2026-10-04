"""Tests for the #1449 import-pipeline hook (`_maybe_write_artist_nfo`).

The hook is a thin, never-raises wrapper around
``core.library.artist_nfo.ensure_artist_nfo_for_track`` — these tests pin
the wiring: which path it passes, and that a sidecar failure can never
break an import.
"""
from types import SimpleNamespace


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _hook(monkeypatch, ensure_impl):
    import core.imports.pipeline as pipeline
    import core.library.artist_nfo as artist_nfo
    calls = []

    def fake_ensure(track_path, cfg):
        calls.append((track_path, cfg))
        return ensure_impl(track_path, cfg)

    monkeypatch.setattr(artist_nfo, "ensure_artist_nfo_for_track", fake_ensure)
    return pipeline._maybe_write_artist_nfo, calls


class TestMaybeWriteArtistNfo:
    def test_prefers_final_processed_path(self, monkeypatch, tmp_path):
        hook, calls = _hook(monkeypatch, lambda p, c: (True, p))
        ctx = {"_final_processed_path": "/lib/Artist/Album/processed.flac"}
        hook(ctx, "/lib/Artist/Album/original.flac", Config())
        assert calls and calls[0][0] == "/lib/Artist/Album/processed.flac"

    def test_falls_back_to_final_path(self, monkeypatch):
        hook, calls = _hook(monkeypatch, lambda p, c: (True, p))
        hook({}, "/lib/Artist/Album/f.flac", Config())
        assert calls and calls[0][0] == "/lib/Artist/Album/f.flac"

    def test_setting_off_is_a_quiet_noop(self, monkeypatch):
        hook, calls = _hook(monkeypatch, lambda p, c: (False, "setting disabled"))
        hook({}, "/lib/Artist/Album/f.flac", Config())  # must not raise
        assert len(calls) == 1

    def test_exception_in_ensure_never_propagates(self, monkeypatch):
        def boom(track_path, cfg):
            raise RuntimeError("disk on fire")

        hook, _ = _hook(monkeypatch, boom)
        hook({}, "/lib/Artist/Album/f.flac", Config())  # must not raise

    def test_import_error_of_helper_never_propagates(self, monkeypatch):
        import builtins
        import core.imports.pipeline as pipeline

        real_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "core.library.artist_nfo":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing_import)
        pipeline._maybe_write_artist_nfo({}, "/lib/x/f.flac", Config())  # must not raise
