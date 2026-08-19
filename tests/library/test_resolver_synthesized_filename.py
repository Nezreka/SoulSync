"""#1127 — the DB filename is synthesized too, not just the album folder.

The first fix for this assumed the basename was real and only the album folder
was wrong, and its tests said so outright: "the artist folder matches, the
filename matches exactly, and only the ALBUM folder is wrong." That held for the
reporter's example as written, the issue was closed, and two more users came
back on 3.2.1 saying nothing had changed.

The second reporter supplied the missing piece: in the DB path
``Beck/Guero/01-01 - E-Pro.flac`` the leading ``01`` is the DISC NUMBER tag.
SoulSync never writes that — ``$track`` renders as ``01`` and the ``$disc``
token is deliberately blank on single-disc albums, "a single-disc album
shouldn't stamp '01-' on every filename" (#981, core/imports/paths.py). Guero is
single-disc, so SoulSync's own template puts ``01 - E-Pro.flac`` on disk.

So Navidrome synthesizes the whole Subsonic path from tags — album folder AND
filename — and the exact-basename fallback can never match. Hence a step that
compares the filename with its leading track / disc-track numbering removed.

Two things this must never do, both because Dead File Cleaner DELETES what the
resolver returns:
  * resolve to the wrong file when more than one candidate matches
  * mangle a title that simply starts with digits ("1979", "99 Luftballons")
"""

from __future__ import annotations

import os

import pytest

from core.library.path_resolver import (
    _strip_track_number,
    resolve_library_file_path,
    resolve_via_last_resort_fallbacks,
)


class _Config:
    def __init__(self, music_root: str):
        self._music_root = music_root

    def get(self, key, default=None):
        if key == "library.music_paths":
            return [self._music_root]
        return default


def _make(root, artist, album, *filenames):
    folder = os.path.join(root, artist, album)
    os.makedirs(folder, exist_ok=True)
    for name in filenames:
        open(os.path.join(folder, name), "w").close()
    return folder


@pytest.fixture()
def library(tmp_path):
    """The reporter's layout, with SoulSync's real single-disc filename."""
    root = str(tmp_path / "music")
    folder = _make(root, "Beck", "Beck - Guero", "01 - E-Pro.flac")
    return root, folder


# ── stripping the numbering ──────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("01-01 - E-Pro.flac", "e-pro.flac"),     # navidrome disc-track
    ("01 - E-Pro.flac", "e-pro.flac"),        # soulsync single-disc
    ("1-01 E-Pro.flac", "e-pro.flac"),        # disc-track, no dash
    ("01. E-Pro.flac", "e-pro.flac"),
    ("01_E-Pro.flac", "e-pro.flac"),
    ("01-01. E-Pro.flac", "e-pro.flac"),
    ("E-Pro.flac", "e-pro.flac"),             # no numbering at all
])
def test_leading_numbering_is_removed(name, expected):
    assert _strip_track_number(name) == expected


@pytest.mark.parametrize("name", [
    "1979.flac",              # the title IS the number
    "99 Luftballons.flac",
    "100 Years.flac",
    "7 Rings.flac",
])
def test_a_title_that_starts_with_digits_is_left_alone(name):
    """No separator after the number means it isn't numbering. Stripping here
    would let two unrelated songs collapse onto one identity."""
    assert _strip_track_number(name) == name.lower()


@pytest.mark.parametrize("name,expected", [
    ("07 - 1979.flac", "1979.flac"),
    ("01-07 - 1979.flac", "1979.flac"),
])
def test_numbering_in_front_of_a_numeric_title_still_goes(name, expected):
    assert _strip_track_number(name) == expected


# ── the reported case ────────────────────────────────────────────────────────

def test_the_navidrome_path_resolves(library):
    """Both segments wrong: album folder Guero vs 'Beck - Guero', filename
    '01-01 - E-Pro.flac' vs '01 - E-Pro.flac'."""
    root, folder = library
    got = resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(root))
    assert got == os.path.join(folder, "01 - E-Pro.flac")


def test_it_also_works_when_only_the_filename_is_wrong(tmp_path):
    """Album folder right, filename synthesized — the sibling step can't do this
    one either, since it needs an exact basename."""
    root = str(tmp_path / "music")
    folder = _make(root, "Beck", "Guero", "01 - E-Pro.flac")
    got = resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(root))
    assert got == os.path.join(folder, "01 - E-Pro.flac")


def test_an_absolute_media_server_path_still_resolves(library):
    root, folder = library
    got = resolve_library_file_path(
        "/music/Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(root))
    assert got == os.path.join(folder, "01 - E-Pro.flac")


# ── it must not disturb anything that already worked ─────────────────────────

def test_an_exact_path_is_returned_untouched(tmp_path):
    root = str(tmp_path / "music")
    folder = _make(root, "Beck", "Beck - Guero", "01 - E-Pro.flac")
    exact = os.path.join(folder, "01 - E-Pro.flac")
    assert resolve_library_file_path(exact, config_manager=_Config(root)) == exact


def test_a_genuinely_missing_file_still_returns_none(library):
    root, _ = library
    assert resolve_library_file_path(
        "Beck/Guero/01-01 - Nothing Here.flac", config_manager=_Config(root)) is None


def test_a_different_extension_is_not_a_match(library):
    """A .mp3 and a .flac of the same song are different files. Dead File
    Cleaner deleting the wrong one would be silent data loss."""
    root, _ = library
    assert resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.mp3", config_manager=_Config(root)) is None


def test_an_unknown_artist_folder_returns_none(library):
    root, _ = library
    assert resolve_library_file_path(
        "Nobody/Guero/01-01 - E-Pro.flac", config_manager=_Config(root)) is None


# ── ambiguity is refused, not guessed ────────────────────────────────────────

def test_two_albums_holding_the_same_song_refuse_to_resolve(tmp_path):
    """A studio album and a compilation both carrying E-Pro. Guessing between
    them is worse than failing, because the caller may delete the answer."""
    root = str(tmp_path / "music")
    _make(root, "Beck", "Beck - Guero", "01 - E-Pro.flac")
    _make(root, "Beck", "Beck - Best Of", "04 - E-Pro.flac")
    assert resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(root)) is None


def test_the_same_library_seen_through_two_base_dirs_still_resolves(tmp_path):
    """One album reached twice is not a conflict. This is the trap the earlier
    sibling-album fix had to be corrected for, so it is pinned here too."""
    root = str(tmp_path / "music")
    folder = _make(root, "Beck", "Beck - Guero", "01 - E-Pro.flac")

    class _TwoBases:
        def get(self, key, default=None):
            if key == "library.music_paths":
                return [root, root + os.sep]
            return default

    got = resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_TwoBases())
    assert got == os.path.join(folder, "01 - E-Pro.flac")


# ── the shared entry point, so the two resolvers cannot drift ────────────────

def test_shared_entry_point_covers_both_interior_fallbacks(tmp_path):
    """web_server.py has its own near-duplicate resolver that never had the
    sibling-album step at all — the same library resolved from a repair job and
    failed from the web server. Both now call this."""
    root = str(tmp_path / "music")
    folder = _make(root, "Beck", "Beck - Guero", "01 - E-Pro.flac")

    # wrong album folder only (the original #1127 shape)
    assert resolve_via_last_resort_fallbacks(
        "Beck/Guero/01 - E-Pro.flac", [root]) == os.path.join(folder, "01 - E-Pro.flac")
    # wrong album folder AND synthesized filename
    assert resolve_via_last_resort_fallbacks(
        "Beck/Guero/01-01 - E-Pro.flac", [root]) == os.path.join(folder, "01 - E-Pro.flac")


@pytest.mark.parametrize("bad", ["", None, "onlyfilename.flac", "Album/file.flac"])
def test_shared_entry_point_shrugs_off_unusable_input(bad, tmp_path):
    assert resolve_via_last_resort_fallbacks(bad, [str(tmp_path)]) is None


def test_shared_entry_point_needs_base_dirs():
    assert resolve_via_last_resort_fallbacks("Beck/Guero/01 - E-Pro.flac", []) is None


def test_web_server_delegates_to_the_shared_helper():
    """Pins the wiring: if web_server stops calling this, its resolver silently
    regresses to the state that made the second report possible.

    Asserts the IMPORT and the CALL, not just the name — a local stub that
    shadowed the helper would still contain the name and read as wired."""
    from pathlib import Path
    source = Path("web_server.py").read_text(encoding="utf-8")
    assert ("from core.library.path_resolver import "
            "resolve_via_last_resort_fallbacks") in source
    assert "resolve_via_last_resort_fallbacks(clean_rel, abs_bases)" in source
