"""Artwork obeys the shared Library-v2 path-resolution boundary."""

from types import SimpleNamespace

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


def test_embedded_lookup_does_not_repeat_resolver_existence_stat(monkeypatch):
    """resolve_lib2_path already guarantees existence; NAS paths must not get
    another explicit exists() round trip before the canonical extractor."""
    from core.library2 import artwork

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(
                fetchall=lambda: [{"path": "/server/music/album.flac"}]
            )

    monkeypatch.setattr(artwork, "_resolve_abs", lambda *_args: "/nas/music/album.flac")
    monkeypatch.setattr(
        "core.metadata.art_apply.extract_embedded_art", lambda _path: b"cover"
    )
    monkeypatch.setattr(
        artwork.os.path,
        "exists",
        lambda _path: (_ for _ in ()).throw(AssertionError("duplicate stat")),
    )

    assert artwork._embedded_art_for_album(_Conn(), None, 7) == b"cover"
