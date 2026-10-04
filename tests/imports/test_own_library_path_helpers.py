"""#1504 — shared path→profile helpers for maintenance tools.

``own_library_roots()`` lists (profile_id, docker-resolved root) for every
profile with an active own library; ``owning_profile_for_path()`` resolves
which of those profiles owns a given on-disk path (longest root wins, None
otherwise). Fail-open: any lookup failure behaves like "no own libraries".

Hermetic: the db and the per-profile root lookup are both faked.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as paths


class _FakeDb:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_own_library_profiles(self):
        return self._profiles


@pytest.fixture
def own_libs(monkeypatch):
    """profiles 2 (own /lib/u2) and 3 (own /lib/u2/nested); profile 1 shared."""
    monkeypatch.setattr(
        "database.music_database.get_database",
        lambda: _FakeDb([
            {"id": 2, "name": "user2", "root": "/lib/u2"},
            {"id": 3, "name": "user3", "root": "/lib/u2/nested"},
        ]),
    )
    monkeypatch.setattr(
        paths, "library_root_for_profile",
        lambda pid, announce=True: {2: "/lib/u2", 3: "/lib/u2/nested"}.get(int(pid)),
    )


# ── own_library_roots ────────────────────────────────────────────────────────

def test_own_library_roots_lists_active_profiles(own_libs):
    assert paths.own_library_roots() == [(2, os.path.normpath(os.path.realpath("/lib/u2"))),
                                         (3, os.path.normpath(os.path.realpath("/lib/u2/nested")))]


def test_own_library_roots_empty_when_no_db(monkeypatch):
    def _boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("database.music_database.get_database", _boom)
    assert paths.own_library_roots() == []


def test_own_library_roots_skips_profiles_without_active_root(monkeypatch):
    monkeypatch.setattr(
        "database.music_database.get_database",
        lambda: _FakeDb([{"id": 4, "name": "user4", "root": "/lib/u4"}]),
    )
    monkeypatch.setattr(paths, "library_root_for_profile",
                        lambda pid, announce=True: None)
    assert paths.own_library_roots() == []


# ── owning_profile_for_path ──────────────────────────────────────────────────

def test_owning_profile_matches_root(own_libs):
    assert paths.owning_profile_for_path("/lib/u2/Daft Punk/x.flac") == 2


def test_owning_profile_longest_root_wins(own_libs):
    assert paths.owning_profile_for_path("/lib/u2/nested/Artist/x.flac") == 3


def test_owning_profile_none_for_shared_folder(own_libs):
    assert paths.owning_profile_for_path("/Transfer/Artist/x.flac") is None


def test_owning_profile_path_boundary_is_exact(own_libs):
    # /lib/u22 is NOT under /lib/u2
    assert paths.owning_profile_for_path("/lib/u22/Artist/x.flac") is None


def test_owning_profile_none_for_blank_input(own_libs):
    assert paths.owning_profile_for_path(None) is None
    assert paths.owning_profile_for_path("") is None


def test_owning_profile_none_when_no_db(monkeypatch):
    def _boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("database.music_database.get_database", _boom)
    assert paths.owning_profile_for_path("/lib/u2/x.flac") is None
