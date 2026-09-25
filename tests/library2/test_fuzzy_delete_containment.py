"""iss29-E04: a resolver GUESS must never be deleted from outside the library.

``resolve_library_file_path`` suffix-walks the configured base directories to
recover a file that moved, and it tries the **transfer folder first**. Imports
land under ``soulseek.transfer_path`` in the same ``Artist/Album/…`` layout, so
a destructive finding on a library file that has since vanished could resolve
onto a freshly downloaded replacement — and the fix then deleted the download
and recorded the finding as converged.

``core/library2/file_delete.py`` already enforces containment in
``library.music_paths`` for the V2 delete pipeline (ADR-05); these tests pin the
same rule for the repair worker's three destructive fixes.
"""

from __future__ import annotations

import os

from core.library2.file_delete import fuzzy_resolved_path_is_deletable


class _Config:
    def __init__(self, roots):
        self._roots = list(roots)

    def get(self, key, default=None):
        if key == "library.music_paths":
            return self._roots
        return default


def test_the_catalogue_path_itself_is_always_deletable(tmp_path):
    """Not a guess: the file is exactly where the catalogue says it is."""
    library = tmp_path / "library"
    library.mkdir()
    target = library / "Artist" / "Album" / "01 Song.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"audio")

    assert fuzzy_resolved_path_is_deletable(
        str(target), str(target), _Config([str(library)])
    ) is True


def test_a_guess_inside_a_library_root_is_deletable(tmp_path):
    """The file genuinely moved within the library — that is what the resolver
    exists for, and deleting it is the user's intent."""
    library = tmp_path / "library"
    moved = library / "Artist" / "Album (2019)" / "01 Song.flac"
    moved.parent.mkdir(parents=True)
    moved.write_bytes(b"audio")
    recorded = str(library / "Artist" / "Album" / "01 Song.flac")

    assert fuzzy_resolved_path_is_deletable(
        recorded, str(moved), _Config([str(library)])
    ) is True


def test_a_guess_that_landed_in_the_transfer_folder_is_refused(tmp_path):
    """The exact production hazard: a fresh download under the transfer path,
    matched by the suffix walk because it uses the same folder layout."""
    library = tmp_path / "library"
    transfer = tmp_path / "transfer"
    library.mkdir()
    fresh_download = transfer / "Artist" / "Album" / "01 Song.flac"
    fresh_download.parent.mkdir(parents=True)
    fresh_download.write_bytes(b"the replacement the user is waiting for")
    recorded = str(library / "Artist" / "Album" / "01 Song.flac")

    assert fuzzy_resolved_path_is_deletable(
        recorded, str(fresh_download), _Config([str(library)])
    ) is False
    assert fresh_download.exists()


def test_fails_closed_when_no_library_roots_are_configured(tmp_path):
    """With nothing to validate a guess against, the guess is not acted on."""
    recorded = str(tmp_path / "gone" / "01 Song.flac")
    guess = tmp_path / "elsewhere" / "01 Song.flac"
    guess.parent.mkdir(parents=True)
    guess.write_bytes(b"audio")

    assert fuzzy_resolved_path_is_deletable(recorded, str(guess), _Config([])) is False


def test_an_empty_resolution_is_never_deletable(tmp_path):
    assert fuzzy_resolved_path_is_deletable("/x/y.flac", "", _Config([str(tmp_path)])) is False
    assert fuzzy_resolved_path_is_deletable("/x/y.flac", None, _Config([str(tmp_path)])) is False


def test_a_root_itself_is_not_a_deletable_target(tmp_path):
    """``_containing_root`` fails closed on the root directory itself, so a
    degenerate resolution cannot authorise removing a whole library folder."""
    library = tmp_path / "library"
    library.mkdir()

    assert fuzzy_resolved_path_is_deletable(
        str(tmp_path / "gone.flac"), str(library), _Config([str(library)])
    ) is False
