"""Sonarr-parity fixes from the '59 searched, 0 grabbed' wishlist run.

Two structural gaps that threw away real episodes:

1. DAILY SERIES — late night / soaps / House Hunters release by AIR DATE
   ('The.Daily.Show.2026.07.08...'), not SxxExx. Those parsed with no episode
   marker and died as 'Not a single episode'. Now: the parser extracts the date,
   a date match IS the episode identity (even over a numbering mismatch), and
   the retry ladder tries date-form queries after the SxxExx forms.

2. SIZE-INFERRED QUALITY — Soulseek files often carry no resolution token
   ('90.Day.Fiance.S12E09.HEVC.mkv') and died as 'Unknown / unsupported
   quality'. We always know the file size, so infer a conservative resolution
   from it (episode/movie only); ffprobe verifies the truth after download.
"""

from __future__ import annotations

from core.video.quality_eval import evaluate_release, _infer_resolution
from core.video.release_parse import parse_release
from core.video.retry import next_query
from core.video.release_parse import titles_match

_PROFILE = {"tiers": [{"key": k, "enabled": True} for k in
                      ("web-2160p", "web-1080p", "webrip-1080p", "hdtv-1080p",
                       "web-720p", "hdtv-720p", "dvd")]}


# ── date parsing ──────────────────────────────────────────────────────────────
def test_parse_release_extracts_air_dates():
    assert parse_release("The.Daily.Show.2026.07.08.Some.Guest.1080p.WEB.h264")["air_date"] == "2026-07-08"
    assert parse_release("Jimmy Kimmel Live 2026-07-08 720p")["air_date"] == "2026-07-08"
    assert parse_release("Beyond the Gates 2026 07 08 HDTV")["air_date"] == "2026-07-08"


def test_parse_release_no_false_date_positives():
    assert parse_release("Blade.Runner.2049.2017.1080p.BluRay")["air_date"] is None
    assert parse_release("Show.S02E03.1080p.WEB")["air_date"] is None
    assert parse_release("Movie.2026.13.40.fake")["air_date"] is None    # month 13 invalid


# ── the daily-series gate ─────────────────────────────────────────────────────
def test_date_named_release_accepted_when_air_date_matches():
    parsed = parse_release("The.Daily.Show.2026.07.08.Guest.1080p.WEB.h264-GRP")
    v = evaluate_release(parsed, _PROFILE, scope="episode", want_season=31, want_episode=85,
                         want_title="The Daily Show", want_date="2026-07-08")
    assert v["accepted"] is True


def test_date_named_release_rejected_on_wrong_date_or_no_wanted_date():
    parsed = parse_release("The.Daily.Show.2026.07.09.Guest.1080p.WEB.h264")
    v = evaluate_release(parsed, _PROFILE, scope="episode", want_season=31, want_episode=85,
                         want_title="The Daily Show", want_date="2026-07-08")
    assert v["accepted"] is False and "Not a single episode" in v["rejected"]
    # without a wanted date the old behavior stands (no SxxExx -> not an episode)
    v2 = evaluate_release(parsed, _PROFILE, scope="episode", want_season=31, want_episode=85,
                          want_title="The Daily Show")
    assert v2["accepted"] is False


def test_date_match_trumps_scene_numbering_mismatch():
    # scene numbering for dailies rarely agrees with TMDB's — the date is authoritative.
    # Also proves 3-digit seasons parse (House Hunters is genuinely on S277) and that
    # the title extractor cuts at the SxxExx, not at the date's year.
    parsed = parse_release("House.Hunters.S278E01.2026.07.08.1080p.WEB")
    assert parsed["season"] == 278 and parsed["air_date"] == "2026-07-08"
    v = evaluate_release(parsed, _PROFILE, scope="episode", want_season=277, want_episode=5,
                         want_title="House Hunters", want_date="2026-07-08")
    assert v["accepted"] is True


def test_retry_ladder_adds_date_queries_after_sxxexx():
    ctx = {"scope": "episode", "title": "The Daily Show", "season": 31, "episode": 85,
           "air_date": "2026-07-08"}
    q1 = next_query(ctx, [])
    q2 = next_query(ctx, [q1])
    q3 = next_query(ctx, [q1, q2])
    q4 = next_query(ctx, [q1, q2, q3])
    assert q1 == "The Daily Show S31E85"
    assert q2 == "The Daily Show 31x85"
    assert q3 == "The Daily Show 2026 07 08"
    assert q4 == "The Daily Show 2026.07.08"
    assert next_query(ctx, [q1, q2, q3, q4]) is None
    # no air date -> ladder unchanged (numbering forms only)
    assert next_query({"scope": "episode", "title": "X", "season": 1, "episode": 2},
                      ["X S01E02"]) == "X 1x02"


def test_search_context_carries_the_air_date(monkeypatch):
    import core.automation.handlers.video_process_wishlist as mod
    monkeypatch.setattr(mod, "_acceptable_titles", lambda p, k, t: [p])
    ctx = mod.search_context({"show_title": "The Daily Show", "season_number": 31,
                              "episode_number": 85, "air_date": "2026-07-08",
                              "show_tmdb_id": 2224}, "episode")
    assert ctx["air_date"] == "2026-07-08" and ctx["year"] == "2026"


# ── size-inferred quality ─────────────────────────────────────────────────────
def test_resolutionless_episode_is_inferred_from_size_and_accepted():
    # the reported case: '90 Day Fiance S12E09 ... HEVC' with no resolution token
    parsed = parse_release("90.Day.Fiance.S12E09.HEVC.x265-MeGusta")
    assert parsed["resolution"] is None
    v = evaluate_release(parsed, _PROFILE, scope="episode", want_season=12, want_episode=9,
                         want_title="90 Day Fiancé", size_gb=1.0)
    assert v["accepted"] is True
    assert v["tier"] == "web-1080p"          # 1.0 GB episode ≈ 1080p, sourceless -> web
    assert "1080p~" in v["quality_label"]    # honest 'inferred' marker


def test_size_inference_tiers():
    assert _infer_resolution("episode", 0.15) == "480p"
    assert _infer_resolution("episode", 0.6) == "720p"
    assert _infer_resolution("episode", 2.0) == "1080p"
    assert _infer_resolution("episode", 6.0) == "2160p"
    assert _infer_resolution("movie", 1.5) == "720p"
    assert _infer_resolution("movie", 5.0) == "1080p"
    assert _infer_resolution("movie", 20.0) == "2160p"
    assert _infer_resolution("movie", 0) is None


def test_no_inference_without_size_or_for_packs():
    parsed = parse_release("Some.Show.S01.Complete.HEVC")     # series pack, no res
    v = evaluate_release(parsed, _PROFILE, scope="series", size_gb=40.0)
    assert v["accepted"] is False and "Unknown" in v["rejected"]
    parsed2 = parse_release("Some.Movie.HEVC.mkv")
    v2 = evaluate_release(parsed2, _PROFILE, scope="movie")   # no size known
    assert v2["accepted"] is False and "Unknown" in v2["rejected"]


def test_named_resolution_still_wins_over_size():
    parsed = parse_release("Show.S01E01.720p.WEB.x264")       # explicit 720p
    v = evaluate_release(parsed, _PROFILE, scope="episode", size_gb=3.0)
    assert v["tier"] == "web-720p"                             # size does not override
    assert "~" not in v["quality_label"]


# ── Soulseek path-aware matching (the 'Wrong title (Season 12)' log lines) ────
def test_release_name_promotes_show_folder_over_generic_season_dir():
    from core.video.slskd_search import _release_name
    assert _release_name(r"@@x\TV\90 Day Fiancé\Season 12\90.Day.Fiance.S12E09.1080p.mkv") \
        == "90 Day Fiancé/Season 12"
    # scene-style folders keep their name (the existing behavior)
    assert _release_name(r"@@x\The.Wire.S02.1080p.BluRay.x265-GRP\the.wire.s02e01.mkv") \
        == "The.Wire.S02.1080p.BluRay.x265-GRP"
    # a share-root grandparent ('@@…') is never a show name
    assert _release_name(r"@@x\Season 12\ep.mkv") == "Season 12"


def test_different_shows_season_folders_no_longer_collide():
    from core.video.slskd_search import group_video_files
    hits = group_video_files([
        {"username": "a", "uploadSpeed": 1, "freeUploadSlots": 1, "files": [
            {"filename": r"@@x\90 Day Fiance\Season 12\ep.mkv", "size": 1}]},
        {"username": "b", "uploadSpeed": 1, "freeUploadSlots": 1, "files": [
            {"filename": r"@@y\Love Island\Season 12\ep.mkv", "size": 1}]},
    ])
    assert len(hits) == 2                     # used to merge into one 'Season 12' hit


def test_parse_text_joins_folder_title_and_filename():
    from api.video.downloads import _parse_text
    slskd_hit = {"title": "90 Day Fiancé/Season 12",
                 "filename": r"@@x\TV\90 Day Fiancé\Season 12\90.Day.Fiance.S12E09.1080p.HEVC.mkv"}
    assert _parse_text(slskd_hit) == "90 Day Fiancé/Season 12/90.Day.Fiance.S12E09.1080p.HEVC.mkv"
    torrent_hit = {"title": "90 Day Fiance S12E09 1080p HEVC x265-MeGusta", "filename": None}
    assert _parse_text(torrent_hit) == "90 Day Fiance S12E09 1080p HEVC x265-MeGusta"


def test_the_reported_library_share_now_matches_end_to_end():
    # the literal failing log line: 1 result for '90 Day Fiancé S12E09' rejected
    # 'Wrong title (Season 12 — wanted [aliases])'
    from api.video.downloads import _evaluate_hits
    hit = {"title": "90 Day Fiancé/Season 12",
           "filename": r"@@x\TV\90 Day Fiancé\Season 12\90.Day.Fiance.S12E09.1080p.HEVC.mkv",
           "size_bytes": 1_073_741_824, "username": "peer1"}
    out = _evaluate_hits([hit], _PROFILE, "episode", 12, 9,
                         blocked=set(), blocked_users=set(),
                         want_title=["90 Day Fiancé", "90 Day Fiance"])
    assert out[0]["accepted"] is True, out[0]["rejected"]


def test_wrong_show_library_share_still_rejected():
    from api.video.downloads import _evaluate_hits
    hit = {"title": "Little House On The Prairie/Season 3",
           "filename": r"@@x\TV\Little House On The Prairie\Season 3\LHOTP.S03E05.1080p.mkv",
           "size_bytes": 1_000_000_000, "username": "peer1"}
    out = _evaluate_hits([hit], _PROFILE, "episode", 277, 5,
                         blocked=set(), blocked_users=set(),
                         want_title=["House Hunters"])
    assert out[0]["accepted"] is False and "Wrong" in out[0]["rejected"]


def test_titles_match_squeezed_spacing_and_segments():
    assert titles_match("90DayFiance.S12E09.1080p.mkv", "90 Day Fiancé") is True
    assert titles_match(r"TV/90 Day Fiancé/Season 12/ep.mkv", ["90 Day Fiancé"]) is True
    assert titles_match(r"TV/Little House/Season 12/ep.mkv", ["House Hunters"]) is False
