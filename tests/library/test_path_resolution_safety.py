"""Physical roots retain identity when server paths need translating."""

from pathlib import Path

import pytest

from core.library.path_resolver import resolve_library_file_path


class _Config:
    def __init__(self, roots, transfer=""):
        self.values = {"library.music_paths": list(map(str, roots)),
                       "soulseek.transfer_path": str(transfer)}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _file(path: Path, content=b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_full_later_root_match_beats_an_earlier_flat_filename(tmp_path):
    flat = tmp_path / "transfer"
    wrong = _file(flat / "01 - Intro.flac", b"wrong artist")
    right = _file(tmp_path / "music/Artist/Album/01 - Intro.flac", b"right artist")

    resolved = resolve_library_file_path(
        "/server/Artist/Album/01 - Intro.flac",
        config_manager=_Config([right.parents[2]], transfer=flat),
    )

    assert resolved == str(right)
    assert wrong.read_bytes() == b"wrong artist"


@pytest.mark.parametrize("layout", ["exact", "sibling", "synthesized"])
def test_independent_files_with_the_same_suffix_are_ambiguous(tmp_path, layout):
    album = "Album" if layout == "exact" else "Artist - Album"
    filename = "01 - Song.flac" if layout != "synthesized" else "Song.flac"
    roots = [tmp_path / "one", tmp_path / "two"]
    for index, root in enumerate(roots):
        _file(root / "Artist" / album / filename, f"copy {index}".encode())

    assert resolve_library_file_path(
        "/server/Artist/Album/01 - Song.flac", config_manager=_Config(roots),
    ) is None


@pytest.mark.parametrize("layout", ["exact", "sibling", "synthesized"])
def test_proven_aliases_of_one_physical_file_still_resolve(tmp_path, layout):
    album = "Album" if layout == "exact" else "Artist - Album"
    filename = "01 - Song.flac" if layout != "synthesized" else "Song.flac"
    root = tmp_path / "music"
    target = _file(root / "Artist" / album / filename)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    resolved = resolve_library_file_path(
        "/server/Artist/Album/01 - Song.flac", config_manager=_Config([root, alias]),
    )

    assert resolved == str(target)


@pytest.mark.parametrize("root_present", [True, False])
def test_missing_known_root_path_cannot_become_another_roots_copy(tmp_path, root_present):
    original_root = tmp_path / "original"
    if root_present:
        original_root.mkdir()
        (original_root / ".mounted").write_bytes(b"mounted")
    other_root = tmp_path / "independent"
    other = _file(other_root / "Artist/Album/song.flac", b"independent")
    missing = original_root / "Artist/Album/song.flac"

    assert resolve_library_file_path(
        str(missing), config_manager=_Config([original_root, other_root]),
    ) is None
    assert other.read_bytes() == b"independent"


def test_scoped_lookup_does_not_translate_a_known_foreign_root(tmp_path):
    own = tmp_path / "own"
    shared = tmp_path / "shared"
    _file(own / "Artist/Album/song.flac")
    shared.mkdir()

    assert resolve_library_file_path(
        str(shared / "Artist/Album/song.flac"),
        config_manager=_Config([own, shared]), library_root=str(own),
    ) is None
    assert resolve_library_file_path(
        "/server/Artist/Album/song.flac", library_root=str(own),
    ) == str(own / "Artist/Album/song.flac")


def test_missing_windows_root_does_not_become_an_independent_local_copy(tmp_path, monkeypatch):
    import os

    # A host-style root can remain declared after moving to a native Linux
    # install. Its separators must still establish the stored path's root.
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda path: False if path == "/.dockerenv" else real_exists(path))
    other = tmp_path / "local"
    _file(other / "Artist/Album/song.flac", b"independent")

    assert resolve_library_file_path(
        r"H:\Music\Artist\Album\song.flac", config_manager=_Config([r"H:\Music", other]),
    ) is None
