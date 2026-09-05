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


def test_resolves_the_requested_profile_id(monkeypatch):
    seen = []
    monkeypatch.setattr(
        selection, "load_profile_by_id",
        lambda profile_id=None: (
            seen.append(profile_id) or {"search_mode": "best_quality"}
        ),
    )
    assert selection.load_search_mode(7) == "best_quality"
    assert seen == [7]


def test_specific_profile_controls_search_mode(monkeypatch):
    seen = []

    def _profile(profile_id):
        seen.append(profile_id)
        return {"version": 3, "search_mode": "best_quality"}

    monkeypatch.setattr(selection, "load_profile_by_id", _profile)

    assert selection.load_search_mode(27) == "best_quality"
    assert seen == [27]


# ── rank_candidates_by_quality toggle ───────────────────────────────────────
# Opt-in: order the priority-mode download walk by ranked-target quality
# instead of confidence-first. Default OFF = byte-for-byte old behaviour.


def test_rank_by_quality_defaults_false_when_key_absent(monkeypatch):
    _patch_profile(monkeypatch, {"version": 3, "ranked_targets": []})
    assert selection.load_rank_candidates_by_quality() is False


def test_rank_by_quality_true_when_enabled(monkeypatch):
    _patch_profile(monkeypatch, {"version": 3, "rank_candidates_by_quality": True})
    assert selection.load_rank_candidates_by_quality() is True


def test_rank_by_quality_false_when_disabled(monkeypatch):
    _patch_profile(monkeypatch, {"version": 3, "rank_candidates_by_quality": False})
    assert selection.load_rank_candidates_by_quality() is False


def test_rank_by_quality_false_on_db_error(monkeypatch):
    class _BoomDB:
        def get_quality_profile(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(music_database, "MusicDatabase", _BoomDB)
    assert selection.load_rank_candidates_by_quality() is False
