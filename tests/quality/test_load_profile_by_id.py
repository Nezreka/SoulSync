"""`core.quality.selection.load_profile_by_id` — resolves a specific
`quality_profiles` row (falling back to the app-wide default), the seam that
lets a wishlist row's own `quality_profile_id` drive per-item download
ranking (`task_worker._candidate_ordering`) and import acceptance
(`core.imports.guards.check_quality_target`) instead of always the global
profile.
"""

from __future__ import annotations

import pytest

import database.music_database as music_database_module
from database.music_database import MusicDatabase
from core.quality.selection import load_profile_by_id


@pytest.fixture()
def db(tmp_path, monkeypatch):
    instance = MusicDatabase(str(tmp_path / "m.db"))
    # load_profile_by_id constructs its own MusicDatabase() with no args —
    # patch the class in its home module so that resolves to this same
    # already-initialized, tmp_path-backed instance instead of the real
    # default database path.
    monkeypatch.setattr(music_database_module, "MusicDatabase", lambda *a, **k: instance)
    return instance


def test_none_id_resolves_default_profile(db):
    profile = load_profile_by_id(None)
    assert profile["preset"] == "balanced"
    assert profile["ranked_targets"]


def test_explicit_id_resolves_that_profile(db):
    pid = db.create_quality_profile("Strict", {
        "ranked_targets": [{"label": "FLAC only", "format": "flac"}],
        "fallback_enabled": False,
    })
    profile = load_profile_by_id(pid)
    assert profile["ranked_targets"] == [{"label": "FLAC only", "format": "flac"}]
    assert profile["fallback_enabled"] is False


def test_unknown_id_falls_back_to_default(db):
    profile = load_profile_by_id(999999)
    assert profile["preset"] == "balanced"
