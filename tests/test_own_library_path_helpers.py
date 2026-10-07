"""Unit tests for profile_id_for_path() and library_containing() (#1504).

These are the reverse-lookup helpers maintenance/repair tools use to route
operations to the owning profile instead of assuming the shared folder.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.imports import paths as paths_mod


def _profiles(rows):
    """Build a fake get_database() returning the given own-library profiles."""
    db = SimpleNamespace(
        get_own_library_profiles=lambda: rows,
    )
    return SimpleNamespace(get_database=lambda: db)


@pytest.fixture
def two_libs(monkeypatch):
    rows = [
        {"id": 2, "name": "user1", "root": "/app/libraries/user1"},
        {"id": 3, "name": "user2", "root": "/app/libraries/user2"},
    ]
    monkeypatch.setattr(
        "database.music_database.get_database", _profiles(rows).get_database
    )


def test_path_under_own_root_returns_profile(two_libs):
    assert paths_mod.profile_id_for_path("/app/libraries/user1/Daft Punk/track.flac") == 2
    assert paths_mod.profile_id_for_path("/app/libraries/user2/track.flac") == 3


def test_path_under_shared_returns_none(two_libs):
    assert paths_mod.profile_id_for_path("/app/Transfer/Artist/track.flac") is None
    assert paths_mod.profile_id_for_path("/somewhere/else/track.flac") is None


def test_library_root_itself_matches(two_libs):
    assert paths_mod.profile_id_for_path("/app/libraries/user1") == 2


def test_trailing_slash_on_path_still_matches(two_libs):
    assert paths_mod.profile_id_for_path("/app/libraries/user1/") == 2


def test_prefix_without_directory_boundary_does_not_match(two_libs):
    # /app/libraries/user1-backup is NOT inside /app/libraries/user1
    assert paths_mod.profile_id_for_path("/app/libraries/user1-backup/track.flac") is None


def test_nested_roots_longest_match_wins(monkeypatch):
    rows = [
        {"id": 2, "name": "outer", "root": "/app/libraries"},
        {"id": 3, "name": "inner", "root": "/app/libraries/user1"},
    ]
    monkeypatch.setattr(
        "database.music_database.get_database", _profiles(rows).get_database
    )
    assert paths_mod.profile_id_for_path("/app/libraries/user1/track.flac") == 3
    assert paths_mod.profile_id_for_path("/app/libraries/other/track.flac") == 2


def test_no_own_libraries_returns_none(monkeypatch):
    monkeypatch.setattr(
        "database.music_database.get_database", _profiles([]).get_database
    )
    assert paths_mod.profile_id_for_path("/app/libraries/user1/track.flac") is None


def test_db_error_fails_closed_to_shared(monkeypatch):
    def boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("database.music_database.get_database", boom)
    assert paths_mod.profile_id_for_path("/app/libraries/user1/track.flac") is None


def test_empty_path_returns_none(two_libs):
    assert paths_mod.profile_id_for_path("") is None
    assert paths_mod.profile_id_for_path(None) is None


def test_library_containing_returns_root_string(two_libs):
    root = paths_mod.library_containing("/app/libraries/user1/a/b.flac")
    assert root == os.path.normpath("/app/libraries/user1")


def test_library_containing_shared_returns_none(two_libs):
    assert paths_mod.library_containing("/app/Transfer/a.flac") is None


def test_move_guard_comparison_semantics(two_libs):
    # the guard: library_containing(src) != library_containing(dst) -> refuse
    src = "/app/libraries/user1/Artist/track.flac"
    same_lib = "/app/libraries/user1/Artist/track2.flac"
    other_lib = "/app/libraries/user2/Artist/track.flac"
    shared = "/app/Transfer/Artist/track.flac"
    assert paths_mod.library_containing(src) == paths_mod.library_containing(same_lib)
    assert paths_mod.library_containing(src) != paths_mod.library_containing(other_lib)
    assert paths_mod.library_containing(src) != paths_mod.library_containing(shared)
    # shared-to-shared passes the guard (None == None)
    assert paths_mod.library_containing(shared) == paths_mod.library_containing("/app/Transfer/x.flac")
