"""Artwork obeys the shared Library-v2 path-resolution boundary."""

from core.library2.artwork import _resolve_abs


def test_artwork_delegates_to_shared_lib2_resolver(monkeypatch):
    calls = []
    config = object()

    def resolve(path, config_manager=None):
        calls.append((path, config_manager))
        return "/mapped/Artist/Album/cover-source.flac"

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", resolve)

    assert _resolve_abs("/server/music/Artist/Album/01.flac", config) == (
        "/mapped/Artist/Album/cover-source.flac"
    )
    assert calls == [("/server/music/Artist/Album/01.flac", config)]


def test_artwork_resolver_failure_remains_best_effort(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("mapping unavailable")

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", fail)
    assert _resolve_abs("/server/music/missing.flac", None) is None
