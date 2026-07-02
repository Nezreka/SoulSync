"""End-to-end: check_quality_target() honors a per-item quality_profile_id
(from context['track_info']) against a REAL quality_profiles row, instead of
always the global default — the core deliverable of the quality-profile
pipeline modularization (core/quality/migrate_to_profiles.py).
"""

from __future__ import annotations

import pytest

import core.imports.guards as guards
import core.imports.file_ops as file_ops
import database.music_database as music_database_module
from database.music_database import MusicDatabase
from core.quality.model import AudioQuality


@pytest.fixture()
def db(tmp_path, monkeypatch):
    instance = MusicDatabase(str(tmp_path / "m.db"))
    monkeypatch.setattr(music_database_module, "MusicDatabase", lambda *a, **k: instance)
    return instance


def test_per_item_profile_overrides_global_default(db, monkeypatch):
    # Global default (Balanced) accepts a 16-bit FLAC fine, but a per-item
    # "Strict FLAC-only-24bit" profile (fallback off) must reject the same
    # file when this download is tied to THAT profile via track_info.
    strict_id = db.create_quality_profile("Strict Hi-Res", {
        "ranked_targets": [
            {"label": "FLAC 24-bit", "format": "flac", "bit_depth": 24},
        ],
        "fallback_enabled": False,
    })

    monkeypatch.setattr(
        file_ops, "probe_audio_quality",
        lambda fp: AudioQuality("flac", sample_rate=44100, bit_depth=16),
    )

    # No quality_profile_id -> global default (Balanced, fallback on) -> accepted.
    assert guards.check_quality_target("/x/song.flac", {"track_info": {}}) is None

    # Same file, but tied to the strict per-item profile -> rejected.
    reason = guards.check_quality_target(
        "/x/song.flac", {"track_info": {"quality_profile_id": strict_id}}
    )
    assert reason is not None
    assert "FLAC 16-bit" in reason
    assert "FLAC 24-bit" in reason
