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
    same_artist_cap,
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
