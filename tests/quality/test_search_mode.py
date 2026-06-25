"""core.quality.selection.load_search_mode — reads the download search
strategy from the quality profile.

'priority'      → today's behaviour: first satisfying source wins.
'best_quality'  → pool all sources, work best→worst by actual quality.

Default and any unknown value resolve to 'priority' so every existing
install keeps its current behaviour.
"""

import core.quality.selection as selection
import database.music_database as music_database


def _patch_profile(monkeypatch, profile):
    class _FakeDB:
        def get_quality_profile(self):
            return profile

    monkeypatch.setattr(music_database, "MusicDatabase", _FakeDB)


def test_defaults_to_priority_when_key_absent(monkeypatch):
    _patch_profile(monkeypatch, {"version": 3, "ranked_targets": []})
    assert selection.load_search_mode() == "priority"


def test_returns_best_quality_when_set(monkeypatch):
    _patch_profile(monkeypatch, {"version": 3, "search_mode": "best_quality"})
    assert selection.load_search_mode() == "best_quality"


def test_unknown_value_falls_back_to_priority(monkeypatch):
    _patch_profile(monkeypatch, {"version": 3, "search_mode": "nonsense"})
    assert selection.load_search_mode() == "priority"
