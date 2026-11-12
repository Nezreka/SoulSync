"""Shelf Life scoring model (core/video/shelf_life.py) — pure, no DB."""

from __future__ import annotations

from datetime import datetime

import pytest

from core.video import shelf_life as sl

NOW = datetime(2026, 11, 11, 12, 0, 0)
GB = 1024 ** 3


def _facts(**over):
    base = {"kind": "movie", "title": "T", "added_at": "2023-01-01",
            "median_gb_per_hour": 4.0}
    base.update(over)
    return base


# ── presets ──────────────────────────────────────────────────────────────────
def test_every_preset_sums_to_100():
    for name, w in sl.PRESETS.items():
        assert sum(w.values()) == 100, name
        assert set(w) == set(sl.SIGNALS), name


def test_balanced_defaults():
    assert sl.PRESETS["balanced"] == {"watch": 40, "replaceability": 20, "cultural": 20,
                                      "size": 10, "requests": 10}
    assert sl.PRESETS["space_first"]["size"] == 25
    assert sl.DEFAULT_THRESHOLD == 30


def test_unknown_preset_falls_back_to_balanced():
    assert sl.weights_for("nope") == sl.PRESETS["balanced"]
    assert sl.weights_for(None) == sl.PRESETS["balanced"]


# ── gates ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("over,expected", [
    ({"in_progress": True}, "in_progress"),
    ({"keep": True}, "keep"),
    ({"request": {"approved": True, "arrived_at": "2026-10-01"}}, "requested"),
    ({"kind": "show", "followed": True, "airing": True}, "following"),
    ({"added_at": "2026-11-01"}, "new_arrival"),
])
def test_each_gate_protects(over, expected):
    r = sl.score(_facts(**over), now=NOW)
    assert r["gate"] == expected
    assert r["score"] is None and r["candidate"] is False
    assert r["reasons"] == [{"signal": "gate", "text": sl.GATE_TEXT[expected]}]


def test_in_progress_wins_over_every_other_gate():
    r = sl.score(_facts(in_progress=True, keep=True, added_at="2026-11-10"), now=NOW)
    assert r["gate"] == "in_progress"


def test_request_gate_ends_after_grace_or_once_watched():
    old = {"approved": True, "arrived_at": "2026-01-01"}         # ~314 days, past 90
    assert sl.score(_facts(request=old), now=NOW)["gate"] is None
    watched = {"approved": True, "arrived_at": "2026-10-01", "requester_watched": True}
    assert sl.score(_facts(request=watched), now=NOW)["gate"] is None
    # a pending (not approved) request is no gate
    assert sl.score(_facts(request={"approved": False, "arrived_at": "2026-10-01"}), now=NOW)["gate"] is None


def test_request_with_no_arrival_date_stays_protected():
    assert sl.gate(_facts(request={"approved": True}), sl.DEFAULT_SETTINGS, NOW) == "requested"


def test_following_gate_is_shows_still_airing_only():
    assert sl.gate(_facts(kind="show", followed=True, airing=False), sl.DEFAULT_SETTINGS, NOW) is None
    assert sl.gate(_facts(kind="movie", followed=True, airing=True), sl.DEFAULT_SETTINGS, NOW) is None


def test_gate_windows_follow_settings():
    s = {**sl.DEFAULT_SETTINGS, "new_arrival_days": 5}
    assert sl.gate(_facts(added_at="2026-11-01"), s, NOW) is None
    s = {**sl.DEFAULT_SETTINGS, "request_grace_days": 400}
    assert sl.gate(_facts(request={"approved": True, "arrived_at": "2026-01-01"}), s, NOW) == "requested"


# ── watch ────────────────────────────────────────────────────────────────────
def test_watch_no_active_viewers_is_no_data():
    value, _ = sl.watch_signal(_facts(watch={"active": 0}), NOW)
    assert value is None


def test_watch_never_watched_decays_with_tenure():
    fresh, _ = sl.watch_signal(_facts(added_at="2026-10-01", watch={"active": 3}), NOW)
    old, text = sl.watch_signal(_facts(added_at="2023-11-11", watch={"active": 3}), NOW)
    assert 40 < fresh < 50 and old < 7 and "never watched" in text


def test_watch_says_when_it_counts_from_soulsyncs_first_sight():
    _, server = sl.watch_signal(_facts(added_at="2026-10-01", added_from="server", watch={"active": 3}), NOW)
    _, seen = sl.watch_signal(_facts(added_at="2026-10-01", added_from="first_seen", watch={"active": 3}), NOW)
    assert server.startswith("never watched in ")
    assert seen.startswith("never watched since SoulSync first saw it ") and seen.endswith(" days ago")


def test_watch_rewards_people_recency_and_plays():
    def v(**w):
        return sl.watch_signal(_facts(watch={"active": 4, **w}), NOW)[0]
    four_recent = v(watched=4, last_viewed_at="2026-11-01", plays=8)
    one_recent = v(watched=1, last_viewed_at="2026-11-01", plays=1)
    four_old = v(watched=4, last_viewed_at="2024-11-01", plays=8)
    assert four_recent > one_recent and four_recent > four_old
    assert four_recent > 90


def test_watch_counts_people_not_a_share_of_the_server():
    # two people who watched it score the same on a server of 2 or of 9
    small, text = sl.watch_signal(_facts(watch={"active": 2, "watched": 2, "last_viewed_at": "2026-11-01"}), NOW)
    big, _ = sl.watch_signal(_facts(watch={"active": 9, "watched": 2, "last_viewed_at": "2026-11-01"}), NOW)
    assert small == big and "watched by 2 people" in text
    # each extra person adds half of what's left: 50, 75, 87.5 for this part
    def audience(n):
        return sl.watch_signal(_facts(watch={"active": 9, "watched": n, "last_viewed_at": None}), NOW)[0] / 0.5
    assert [round(audience(n), 1) for n in (1, 2, 3)] == [50.0, 75.0, 87.5]


def test_watch_undated_watch_still_counts_as_watched():
    value, text = sl.watch_signal(_facts(watch={"active": 2, "watched": 1, "plays": 1}), NOW)
    assert value >= 25 and "date unknown" in text and "1 person" in text


def test_watch_show_counts_each_person_by_how_much_they_watched():
    half, _ = sl.watch_signal(_facts(watch={"active": 2, "watched": 1, "people": 0.5,
                                            "last_viewed_at": "2026-11-01"}), NOW)
    full, _ = sl.watch_signal(_facts(watch={"active": 2, "watched": 1, "people": 1.0,
                                            "last_viewed_at": "2026-11-01"}), NOW)
    # 1 - 0.5**0.5 against 1 - 0.5**1, at half weight
    assert full - half == pytest.approx(50 * ((1 - 0.5) - (1 - 0.5 ** 0.5)), abs=0.01)


def test_watch_unwatched_counts_only_from_where_the_history_starts():
    # in the library three years, the server's history only goes back a year
    value, text = sl.watch_signal(_facts(added_at="2023-11-11",
                                         watch={"active": 5, "history_since": "2025-11-11 10:00:00"}), NOW)
    assert text == "no plays on record since Nov 2025"
    assert value == pytest.approx(25.0, abs=0.5)
    # arrived after the history starts: the whole stay is known
    _, text = sl.watch_signal(_facts(added_at="2026-10-01", added_from="server",
                                     watch={"active": 5, "history_since": "2025-11-11"}), NOW)
    assert text.startswith("never watched in ")


def test_gates_for_playing_now_and_a_show_being_watched():
    assert sl.gate(_facts(playing=True, keep=True), sl.DEFAULT_SETTINGS, NOW) == "playing"
    assert sl.gate(_facts(kind="show", watching=True), sl.DEFAULT_SETTINGS, NOW) == "watching"
    # a movie has no 'watching' gate: part-way is in_progress, finished is just watched
    assert sl.gate(_facts(kind="movie", watching=True), sl.DEFAULT_SETTINGS, NOW) is None


# ── replaceability ───────────────────────────────────────────────────────────
def test_replaceability_fewer_services_scores_higher():
    def v(status, count=None):
        return sl.replaceability_signal(_facts(streaming_status=status, streaming_count=count))[0]
    assert v("not_found") == 100
    assert v("ok", 1) > v("ok", 2) > v("ok", 3) > v("ok", 6)
    assert v("ok", None) == v("ok", 1)          # found before the count existed


def test_replaceability_failed_grabs_raise_it():
    easy, _ = sl.replaceability_signal(_facts(failed_grabs=0))
    one, _ = sl.replaceability_signal(_facts(failed_grabs=1))
    hard, text = sl.replaceability_signal(_facts(failed_grabs=7))
    assert easy == 0 and one == pytest.approx(30.0) and hard == pytest.approx(90.0)
    assert "7 failed grabs" in text


def test_replaceability_mixes_rarity_and_difficulty():
    value, _ = sl.replaceability_signal(_facts(streaming_status="ok", streaming_count=1, failed_grabs=3))
    assert value == pytest.approx(0.6 * 60 + 0.4 * 60)


def test_replaceability_no_data():
    assert sl.replaceability_signal(_facts())[0] is None
    assert sl.replaceability_signal(_facts(streaming_status="error"))[0] is None


# ── cultural ─────────────────────────────────────────────────────────────────
def test_cultural_blends_scales():
    value, _ = sl.cultural_signal(_facts(ratings={"imdb": 8.0, "rt": 90, "metacritic": 70}), NOW)
    assert value == pytest.approx(80.0)


def test_cultural_few_votes_pull_toward_neutral():
    few, _ = sl.cultural_signal(_facts(ratings={"imdb": 9.0, "trakt_votes": 3}), NOW)
    many, _ = sl.cultural_signal(_facts(ratings={"imdb": 9.0, "trakt_votes": 50000}), NOW)
    assert 50 < few < many == pytest.approx(90.0)


def test_cultural_awards_and_classic_age():
    plain, _ = sl.cultural_signal(_facts(ratings={"imdb": 7.0}, year=2020), NOW)
    oscar, text = sl.cultural_signal(_facts(ratings={"imdb": 7.0}, year=2020, awards="oscar"), NOW)
    winner, _ = sl.cultural_signal(_facts(ratings={"imdb": 7.0}, year=2020, awards="winner"), NOW)
    classic, ctext = sl.cultural_signal(_facts(ratings={"imdb": 7.0}, year=1975), NOW)
    assert oscar - plain == 20 and winner - plain == 10 and "Oscar" in text
    assert classic - plain == 10 and "51-year-old" in ctext
    # an old title that isn't well rated earns no classic bonus
    poor_old, _ = sl.cultural_signal(_facts(ratings={"imdb": 4.0}, year=1975), NOW)
    assert poor_old == pytest.approx(40.0)


def test_cultural_anime_uses_anilist():
    value, _ = sl.cultural_signal(_facts(kind="show", ratings={"anilist": 84}), NOW)
    assert value == pytest.approx(84.0)


def test_cultural_no_data_but_award_alone_answers():
    assert sl.cultural_signal(_facts(), NOW)[0] is None
    assert sl.cultural_signal(_facts(awards="winner"), NOW)[0] == pytest.approx(60.0)


# ── size ─────────────────────────────────────────────────────────────────────
def test_size_at_median_is_neutral_and_bloat_scores_low():
    at = sl.size_signal(_facts(size_bytes=8 * GB, runtime_minutes=120))[0]       # 4 GB/h
    bloated = sl.size_signal(_facts(size_bytes=32 * GB, runtime_minutes=120))[0]  # 16 GB/h
    lean = sl.size_signal(_facts(size_bytes=4 * GB, runtime_minutes=120))[0]      # 2 GB/h
    assert at == pytest.approx(50.0) and bloated == pytest.approx(20.0) and lean > 60


def test_size_no_data():
    assert sl.size_signal(_facts(size_bytes=8 * GB))[0] is None
    assert sl.size_signal(_facts(size_bytes=8 * GB, runtime_minutes=120, median_gb_per_hour=None))[0] is None


def test_median_gb_per_hour():
    rows = [{"size_bytes": 2 * GB, "runtime_minutes": 60}, {"size_bytes": 4 * GB, "runtime_minutes": 60},
            {"size_bytes": 9 * GB, "runtime_minutes": 60}, {"size_bytes": None, "runtime_minutes": 60}]
    assert sl.median_gb_per_hour(rows) == pytest.approx(4.0)
    assert sl.median_gb_per_hour(rows[:2]) == pytest.approx(3.0)
    assert sl.median_gb_per_hour([]) is None


# ── requests ─────────────────────────────────────────────────────────────────
def test_requests_signal():
    s = sl.DEFAULT_SETTINGS
    assert sl.requests_signal(_facts(), s, NOW)[0] == 50
    watched = {"approved": True, "arrived_at": "2025-01-01", "requester_watched": True}
    assert sl.requests_signal(_facts(request=watched), s, NOW)[0] == 80
    just_past = {"approved": True, "arrived_at": "2026-08-10"}            # ~93 days
    year_past = {"approved": True, "arrived_at": "2025-08-13"}            # ~455 days
    a = sl.requests_signal(_facts(request=just_past), s, NOW)[0]
    b = sl.requests_signal(_facts(request=year_past), s, NOW)[0]
    assert 34 < a <= 35 and b == pytest.approx(17.5, abs=0.2)


def test_requests_unknown_requester_watching_is_neutral_not_a_penalty():
    s = sl.DEFAULT_SETTINGS
    unknown = {"approved": True, "arrived_at": "2025-01-01", "requester_watched": None}
    value, text = sl.requests_signal(_facts(request=unknown), s, NOW)
    assert value == 50 and "isn't known" in text
    # still protected inside the grace window
    fresh = {"approved": True, "arrived_at": "2026-10-20", "requester_watched": None}
    assert sl.gate(_facts(request=fresh), s, NOW) == "requested"


# ── the whole score ──────────────────────────────────────────────────────────
def test_no_data_at_all_is_neutral_not_a_candidate():
    r = sl.score(_facts(), now=NOW)
    assert r["score"] == 50 and r["candidate"] is False
    assert {x["signal"] for x in r["reasons"]} == {"watch", "replaceability", "cultural", "size"}


def test_dead_weight_is_a_candidate_with_reasons():
    r = sl.score(_facts(watch={"active": 5, "watched": 0}, streaming_status="ok", streaming_count=5,
                        ratings={"imdb": 5.5}, year=2019, size_bytes=60 * GB, runtime_minutes=120), now=NOW)
    assert r["candidate"] is True and r["score"] < 30
    assert r["reasons"][0]["signal"] == "watch"
    assert len([x for x in r["reasons"] if x["signal"] in sl.SIGNALS]) <= 3


def test_unwatched_classic_not_streaming_is_kept():
    r = sl.score(_facts(added_at="2024-11-01", watch={"active": 5, "watched": 0},
                        streaming_status="not_found", ratings={"imdb": 8.3, "rt": 95, "trakt_votes": 30000},
                        awards="oscar", year=1962, size_bytes=12 * GB, runtime_minutes=130), now=NOW)
    assert r["candidate"] is False and r["score"] >= 50


def test_preset_changes_the_answer():
    f = _facts(watch={"active": 2, "watched": 2, "last_viewed_at": "2026-10-01", "plays": 2},
               streaming_status="ok", streaming_count=6, ratings={"imdb": 7.0},
               size_bytes=80 * GB, runtime_minutes=120)
    balanced = sl.score(f, {"preset": "balanced"}, NOW)["score"]
    space = sl.score(f, {"preset": "space_first"}, NOW)["score"]
    assert space < balanced


def test_threshold_setting_moves_the_line():
    f = _facts(watch={"active": 5, "watched": 1, "last_viewed_at": "2024-11-01", "plays": 1},
               streaming_status="ok", streaming_count=4, ratings={"imdb": 6.2},
               size_bytes=4 * GB, runtime_minutes=100)
    r = sl.score(f, now=NOW)
    assert sl.score(f, {"threshold": r["score"] + 1}, NOW)["candidate"] is True
    assert sl.score(f, {"threshold": r["score"]}, NOW)["candidate"] is False


def test_neutral_signal_takes_no_reason_slot():
    r = sl.score(_facts(watch={"active": 2, "watched": 0}, streaming_status="ok", streaming_count=1,
                        ratings={"imdb": 6.0}, size_bytes=8 * GB, runtime_minutes=120), now=NOW)
    assert "requests" not in {x["signal"] for x in r["reasons"]}
