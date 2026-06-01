"""Tests for the extracted radio-selection logic (Phase 0a of the player revamp).

These pin the behavior that used to be inline + untestable inside
``database.music_database.get_radio_tracks``. They lock current behavior so
Phase 2 (smart ranking) can evolve it against a green baseline.
"""

from __future__ import annotations

from core.radio.selection import (
    RadioCollector,
    build_like_conditions,
    merge_tags,
    parse_tags,
    rank_candidates,
    same_artist_cap,
    score_candidate,
)


class TestParseTags:
    def test_json_array(self):
        assert parse_tags('["rock", "indie"]') == ["rock", "indie"]

    def test_comma_separated_legacy(self):
        assert parse_tags("rock, indie, folk") == ["rock", "indie", "folk"]

    def test_comma_separated_strips_whitespace_and_blanks(self):
        assert parse_tags("rock,  , indie ,") == ["rock", "indie"]

    def test_empty_and_none(self):
        assert parse_tags("") == []
        assert parse_tags(None) == []

    def test_non_list_json_scalar_wrapped(self):
        # A bare JSON scalar (e.g. a quoted string) becomes a single-item list.
        assert parse_tags('"rock"') == ["rock"]

    def test_garbage_falls_back_to_split(self):
        assert parse_tags("not json at all") == ["not json at all"]


class TestSameArtistCap:
    def test_thirty_percent(self):
        assert same_artist_cap(50) == 15   # 50 * 3 // 10
        assert same_artist_cap(20) == 6

    def test_floored_at_five(self):
        assert same_artist_cap(10) == 5     # 3, floored to 5
        assert same_artist_cap(1) == 5


class TestMergeTags:
    def test_dedupes_preserving_order(self):
        assert merge_tags(["rock", "indie"], ["indie", "folk"]) == ["rock", "indie", "folk"]

    def test_empty_groups(self):
        assert merge_tags([], []) == []


class TestBuildLikeConditions:
    def test_single_tag_two_columns(self):
        sql, params = build_like_conditions(["rock"], ("al.genres", "ar.genres"))
        assert sql == "al.genres LIKE ? OR ar.genres LIKE ?"
        assert params == ["%rock%", "%rock%"]

    def test_grouping_matches_original_order(self):
        # Original emitted all album-col LIKEs, then all artist-col LIKEs;
        # params were [%t%...] * 2. Reproduce that ordering exactly.
        sql, params = build_like_conditions(["rock", "indie"], ("al.genres", "ar.genres"))
        assert sql == (
            "al.genres LIKE ? OR al.genres LIKE ? OR "
            "ar.genres LIKE ? OR ar.genres LIKE ?"
        )
        assert params == ["%rock%", "%indie%", "%rock%", "%indie%"]

    def test_no_tags_returns_empty(self):
        assert build_like_conditions([], ("al.genres",)) == ("", [])

    def test_no_columns_returns_empty(self):
        assert build_like_conditions(["rock"], ()) == ("", [])


class TestRadioCollector:
    def _rows(self, *ids):
        return [{"id": i, "title": f"t{i}"} for i in ids]

    def test_collects_and_dedupes(self):
        c = RadioCollector(limit=10)
        c.collect(self._rows(1, 2, 2, 3))   # dup 2 ignored
        assert [t["id"] for t in c.tracks] == [1, 2, 3]

    def test_excludes_seed_and_caller_ids(self):
        c = RadioCollector(limit=10, exclude_ids=["1", "2"])
        c.collect(self._rows(1, 2, 3, 4))
        assert [t["id"] for t in c.tracks] == [3, 4]

    def test_exclude_ids_coerced_to_str(self):
        # Caller may pass ints; seen-set stores strings.
        c = RadioCollector(limit=10, exclude_ids=[1])
        c.collect(self._rows(1, 2))
        assert [t["id"] for t in c.tracks] == [2]

    def test_cap_bounds_a_single_tier(self):
        c = RadioCollector(limit=10)
        c.collect(self._rows(1, 2, 3, 4, 5), cap=2)   # only 2 from this tier
        assert [t["id"] for t in c.tracks] == [1, 2]
        assert not c.filled
        assert c.remaining() == 8

    def test_filled_at_limit(self):
        c = RadioCollector(limit=3)
        ret = c.collect(self._rows(1, 2, 3, 4))
        assert ret is True
        assert c.filled
        assert len(c.tracks) == 3
        assert c.remaining() == 0

    def test_capped_collect_returns_true_at_cap_target(self):
        # Faithful to the original _collect: it returns True once the
        # cap-bounded target is hit, even below the overall limit. The DB
        # method IGNORES tier 1's capped return and checks .filled instead, so
        # this never causes early exit — but the contract must match exactly.
        c = RadioCollector(limit=5)
        assert c.collect(self._rows(1, 2), cap=2) is True   # hit cap target (2)
        assert not c.filled                                  # but not at limit (5)

    def test_uncapped_collect_returns_true_only_at_limit(self):
        c = RadioCollector(limit=5)
        assert c.collect(self._rows(1, 2)) is False          # below limit
        assert c.collect(self._rows(3, 4, 5)) is True         # now at limit

    def test_exclude_placeholders_and_values_track_seen_set(self):
        c = RadioCollector(limit=10, exclude_ids=["a", "b"])
        assert c.exclude_placeholders() == "?,?"
        assert set(c.exclude_values()) == {"a", "b"}
        # After collecting, already-collected IDs join the NOT-IN set so the
        # next tier's SQL won't re-pull them.
        c.collect(self._rows("c"))
        assert c.exclude_placeholders() == "?,?,?"
        assert set(c.exclude_values()) == {"a", "b", "c"}

    def test_ranked_collect_prefers_high_play_count(self):
        # Pool given in worst-first order; rank=True should reorder so the
        # most-played track is collected first.
        c = RadioCollector(limit=2)
        pool = [
            {"id": 1, "play_count": 0},
            {"id": 2, "play_count": 500},
            {"id": 3, "play_count": 50},
        ]
        c.collect(pool, rank=True)
        assert [t["id"] for t in c.tracks] == [2, 3]   # 500 then 50, 0 dropped at limit


# ── Phase 2: smart ranking ─────────────────────────────────────────────────

class TestScoreCandidate:
    def test_missing_signals_score_is_pure_jitter(self):
        # No play data → score is just the stable jitter, in [0, 1).
        s = score_candidate({"id": "x"})
        assert 0.0 <= s < 1.0

    def test_higher_play_count_scores_higher(self):
        low = score_candidate({"id": "same", "play_count": 1})
        high = score_candidate({"id": "same", "play_count": 1000})
        assert high > low   # same id → same jitter, so play_count decides

    def test_lastfm_contributes(self):
        base = score_candidate({"id": "same"})
        with_lastfm = score_candidate({"id": "same", "lastfm_playcount": 100000})
        assert with_lastfm > base

    def test_recently_played_is_penalized(self):
        normal = score_candidate({"id": "same", "play_count": 10})
        recent = score_candidate({"id": "same", "play_count": 10, "_recently_played": True})
        assert recent < normal

    def test_invalid_counts_treated_as_zero(self):
        # Garbage values must not crash; they score as 0 (jitter only).
        s = score_candidate({"id": "x", "play_count": None, "lastfm_playcount": "n/a"})
        assert 0.0 <= s < 1.0

    def test_jitter_is_stable_per_id(self):
        a = score_candidate({"id": "track-42"})
        b = score_candidate({"id": "track-42"})
        assert a == b   # deterministic — reproducible runs/tests

    def test_jitter_differs_between_ids(self):
        a = score_candidate({"id": "track-1"})
        b = score_candidate({"id": "track-2"})
        assert a != b


class TestRankCandidates:
    def test_orders_best_first(self):
        rows = [
            {"id": 1, "play_count": 0},
            {"id": 2, "play_count": 1000},
            {"id": 3, "play_count": 100},
        ]
        ranked = rank_candidates(rows)
        assert [r["id"] for r in ranked] == [2, 3, 1]

    def test_does_not_mutate_input(self):
        rows = [{"id": 1, "play_count": 0}, {"id": 2, "play_count": 9}]
        original = list(rows)
        rank_candidates(rows)
        assert rows == original

    def test_empty(self):
        assert rank_candidates([]) == []

    def test_popularity_beats_jitter_at_scale(self):
        # A heavily-played track must always outrank an unplayed one regardless
        # of jitter (jitter is bounded to [0,1), play_count is log-scaled * 1.0).
        pool = [{"id": f"unplayed-{i}", "play_count": 0} for i in range(20)]
        pool.append({"id": "hit", "play_count": 5000})
        ranked = rank_candidates(pool)
        assert ranked[0]["id"] == "hit"
