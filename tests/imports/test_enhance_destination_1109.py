"""#1109 — "Upgraded track lands in library root not artist/album".

A quality upgrade ("enhance") reuses the existing library file's location so the
better copy replaces the worse one in place. It got that location from
`source_info['original_file_path']` — the path as RECORDED IN THE DATABASE.

That recorded path is usually the MEDIA SERVER's view of the library
("/music/Artist/Album/…"), not a path the SoulSync container can reach. The old
code took `os.path.dirname` of it and ran `makedirs` unconditionally, which
either fabricated a container-local `/music` tree that nothing else could see,
or raised PermissionError and aborted post-processing outright — leaving the
finished download unsorted. The reporter's log showed exactly that: files "not
even copied due to no permission", while a plain (non-upgrade) download of the
same album sorted correctly via `resolve_existing_album_folder`.

The rule now: an unreachable original is not a destination. Only an existing,
reachable directory is reused; anything else falls through to the normal
template so the upgrade still lands in Artist/Album.
"""

from __future__ import annotations

import os

import pytest

from core.imports import paths as paths_mod
from core.imports.paths import _reachable_original_dir


# ── _reachable_original_dir ──────────────────────────────────────────────────

def test_returns_the_directory_when_the_original_really_is_there(tmp_path):
    album = tmp_path / "Billie Eilish" / "HIT ME HARD AND SOFT"
    album.mkdir(parents=True)
    original = album / "10 BLUE.mp3"
    original.write_bytes(b"x")

    assert _reachable_original_dir(str(original)) == str(album)


def test_returns_none_for_a_media_server_only_path(tmp_path, monkeypatch):
    """The /music/... case from the report — nothing on this filesystem."""
    monkeypatch.setattr(
        paths_mod, "_get_config_manager", lambda: _StubConfig(str(tmp_path)))
    assert _reachable_original_dir("/music/Billie Eilish/HIT ME HARD AND SOFT/10 BLUE.mp3") is None


def test_returns_none_when_the_folder_was_deleted(tmp_path):
    """The file's folder is gone — replacing in place is meaningless."""
    missing = tmp_path / "gone" / "track.flac"
    assert _reachable_original_dir(str(missing)) is None


@pytest.mark.parametrize("value", ["", "   ", None, 123, [], {}])
def test_returns_none_for_junk_input(value):
    assert _reachable_original_dir(value) is None


def test_never_creates_the_directory(tmp_path, monkeypatch):
    """The core of #1109: probing must have no filesystem side effects.

    If this regresses, an upgrade silently mkdir's the media server's path
    inside the container again.
    """
    monkeypatch.setattr(
        paths_mod, "_get_config_manager", lambda: _StubConfig(str(tmp_path)))
    target = tmp_path / "not-created-by-probing"

    called = []
    real_makedirs = os.makedirs
    monkeypatch.setattr(
        os, "makedirs",
        lambda *a, **k: (called.append(a), real_makedirs(*a, **k))[1])

    assert _reachable_original_dir(str(target / "track.flac")) is None
    assert called == [], f"probing created directories: {called}"
    assert not target.exists()


def test_falls_back_to_the_resolver_when_the_raw_path_is_unreachable(tmp_path, monkeypatch):
    """A DB path under the media server's root still resolves when the same
    album is reachable locally — that IS a valid in-place replacement."""
    real_album = tmp_path / "Transfer" / "Beck" / "Beck - Guero"
    real_album.mkdir(parents=True)
    real_file = real_album / "01-01 - E-Pro.flac"
    real_file.write_bytes(b"x")

    monkeypatch.setattr(
        paths_mod, "_get_config_manager", lambda: _StubConfig(str(tmp_path)))
    monkeypatch.setattr(
        "core.library.path_resolver.resolve_library_file_path",
        lambda p, **kw: str(real_file),
    )

    assert _reachable_original_dir("/music/Beck/Beck - Guero/01-01 - E-Pro.flac") == str(real_album)


def test_resolver_blowing_up_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        paths_mod, "_get_config_manager", lambda: _StubConfig(str(tmp_path)))
    monkeypatch.setattr(
        "core.library.path_resolver.resolve_library_file_path",
        lambda p, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert _reachable_original_dir("/music/whatever/track.flac") is None


# ── build_final_path_for_track: the behaviour users actually see ─────────────

def _enhance_context(original_file_path: str) -> dict:
    return {
        "artist": {"name": "Lenka"},
        "album": {"name": "Lenka", "id": "album-1", "release_date": "2008-01-01",
                  "total_tracks": 12, "album_type": "album",
                  "artists": [{"name": "Lenka"}]},
        "track_info": {
            "name": "The Show", "id": "t1", "track_number": 1, "disc_number": 1,
            "artists": [{"name": "Lenka"}],
            "source_info": {"enhance": True, "original_file_path": original_file_path},
        },
        "original_search_result": {"title": "The Show", "clean_title": "The Show",
                                   "clean_album": "Lenka", "clean_artist": "Lenka",
                                   "artists": [{"name": "Lenka"}]},
        "source": "deezer", "is_album_download": False,
        # reuse would short-circuit the template; this test is about the
        # enhance branch itself.
        "_no_album_folder_reuse": True,
    }


ALBUM_INFO = {"is_album": True, "album_name": "Lenka", "track_number": 1, "disc_number": 1}


def _patch_config(monkeypatch, tmp_path):
    cfg = _TemplateConfig(tmp_path)
    monkeypatch.setattr(paths_mod, "_get_config_manager", lambda: cfg)
    monkeypatch.setattr(paths_mod, "_get_album_tracks_for_source", lambda *a: None)


def test_unreachable_original_lands_in_artist_album_not_a_fabricated_tree(monkeypatch, tmp_path):
    """The #1109 regression.

    `original_file_path` points somewhere this process cannot see (the media
    server's view). Pre-fix, the builder took its dirname, `makedirs`'d it, and
    returned it — fabricating a tree nothing else could read. Now it falls
    through to the template destination under Transfer.
    """
    _patch_config(monkeypatch, tmp_path)
    media_server_only = tmp_path / "media-server-only" / "Lenka" / "Lenka"

    final_path, created = paths_mod.build_final_path_for_track(
        _enhance_context(str(media_server_only / "01 - The Show.mp3")),
        {"name": "Lenka"}, ALBUM_INFO, ".flac",
    )

    assert created is True
    assert final_path == str(
        tmp_path / "Transfer" / "Lenka" / "Lenka - Lenka" / "01 - The Show.flac")
    assert not media_server_only.exists(), (
        "the unreachable original's directory was created — #1109 is back")


def test_reachable_original_is_still_replaced_in_place(monkeypatch, tmp_path):
    """The fix must not break the normal case: when the existing library file
    IS reachable, the upgrade still lands right on top of it."""
    _patch_config(monkeypatch, tmp_path)
    album = tmp_path / "Transfer" / "Lenka" / "Lenka - Lenka"
    album.mkdir(parents=True)
    (album / "01 - The Show.mp3").write_bytes(b"x")

    final_path, created = paths_mod.build_final_path_for_track(
        _enhance_context(str(album / "01 - The Show.mp3")),
        {"name": "Lenka"}, ALBUM_INFO, ".flac",
    )

    assert created is True
    assert final_path == str(album / "01 - The Show.flac")


class _TemplateConfig:
    """config_manager stand-in carrying the default organization templates."""

    def __init__(self, tmp_path):
        self._values = {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "soulseek.download_path": str(tmp_path / "downloads"),
            "library.music_paths": [],
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$albumartist - $album/$track - $title",
                "single_path": "$artist/$artist - $title/$title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return None


class _StubConfig:
    """Minimal config_manager stand-in — keeps the resolver off the real config."""

    def __init__(self, root: str):
        self._root = root

    def get(self, key, default=None):
        if key == "soulseek.transfer_path":
            return os.path.join(self._root, "Transfer")
        if key == "soulseek.download_path":
            return os.path.join(self._root, "downloads")
        if key == "library.music_paths":
            return []
        return default

    def get_active_media_server(self):
        return None
