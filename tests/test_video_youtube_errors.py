"""YouTube's failures are not one failure, and the three-strike budget assumed they were.

Every case below is taken from Boulder's live database — 20 failed rows that turned
out to be 10 videos, each given three attempts before the drain gave up on it:

  · 4 members-only videos, each spending three attempts over several hours to learn
    what the first attempt already knew
  · 1 video that had not been released yet ("Premieres in 3 days"), which burned all
    three attempts inside two hours and is now permanently skipped — it premiered
    days later and nothing will ever fetch it. That is the bug these tests exist for.
  · 5 x "HTTP Error 403: Forbidden" across five different channels in two days, which
    is what YouTube's bot-detection looks like when yt-dlp has fallen behind

The strike weighting reads the STORED error text, so it re-judges history already on
disk. The premiere above un-sticks itself on the next drain with no migration — that
property is asserted at the bottom, because it is the whole reason for doing it this
way rather than adding a column.
"""

from __future__ import annotations

from core.video.youtube_errors import (
    BLOCKED,
    GONE,
    NOT_YET,
    TRANSIENT,
    classify,
    failure_weight,
    human_reason,
    looks_like_stale_ytdlp,
    strikes_for,
)

# The exact strings from the live DB, ANSI colour codes and all.
_MEMBERS = ("\x1b[0;31mERROR:\x1b[0m [youtube] h54BlzF2t0k: This video is available to "
            "this channel's members on level: Tested Premium (or any higher level). "
            "Join this channel to get access to members-only content.")
_PREMIERE = "\x1b[0;31mERROR:\x1b[0m [youtube] O4-YG3L8pog: Premieres in 3 days"
_403 = ("\x1b[0;31mERROR:\x1b[0m unable to download video data: "
        "HTTP Error 403: Forbidden")


# ── reading the error ────────────────────────────────────────────────────────

def test_the_live_members_only_error_reads_as_gone():
    assert classify(_MEMBERS) == GONE


def test_the_live_premiere_error_reads_as_not_yet():
    assert classify(_PREMIERE) == NOT_YET


def test_the_live_403_reads_as_blocked():
    assert classify(_403) == BLOCKED


def test_ansi_colour_codes_do_not_defeat_the_match():
    """yt-dlp colours its output and the escapes land in the DB verbatim. A naive
    matcher misses every one of these."""
    assert classify("\x1b[0;31mERROR:\x1b[0m Private video") == GONE


def test_permanent_wins_over_the_generic_download_failure():
    """A members-only video ALSO says 'unable to download', which is a blocked
    pattern. Read as blocked it would keep retrying a paywall forever."""
    assert classify(_MEMBERS + " unable to download video data") == GONE


def test_the_unknown_failure_stays_ordinary():
    for e in ("Connection reset by peer", "", None, "disk full", 12345):
        assert classify(e) == TRANSIENT


def test_the_other_permanent_shapes():
    for e in ("Private video. Sign in if you've been granted access",
              "Video unavailable. This video has been removed by the uploader",
              "The account associated with this video has been terminated",
              "This video is not available in your country",
              "who has blocked it on copyright grounds"):
        assert classify(e) == GONE, e


def test_the_other_not_yet_shapes():
    for e in ("This live event will begin in 2 hours",
              "Premieres in 45 minutes", "This video is upcoming"):
        assert classify(e) == NOT_YET, e


# ── what a failure costs ─────────────────────────────────────────────────────

def test_a_paywalled_video_spends_the_whole_budget_at_once():
    """Three attempts over three hours to re-learn that a paywall is a paywall."""
    assert failure_weight(_MEMBERS, max_fail=3) == 3


def test_a_video_that_is_not_out_yet_costs_nothing():
    """THE bug. 'Premieres in 3 days' is not a failed download — the video does not
    exist yet. Charging it three strikes in two hours permanently skipped a video
    that aired two days later."""
    assert failure_weight(_PREMIERE, max_fail=3) == 0


def test_a_403_costs_one_strike_like_any_other_retryable_failure():
    assert failure_weight(_403, max_fail=3) == 1


def test_an_unknown_failure_costs_one():
    assert failure_weight("something odd happened", max_fail=3) == 1


# ── the running total ────────────────────────────────────────────────────────

def test_one_paywalled_attempt_is_already_the_limit():
    assert strikes_for([{"error": _MEMBERS, "days_ago": 0}], max_fail=3) >= 3


def test_three_premiere_attempts_still_total_zero():
    """The live row: three attempts inside two hours, all 'Premieres in 3 days'.
    Under the old raw count that was 3 and the video was dead."""
    rows = [{"error": _PREMIERE, "days_ago": d} for d in (2.1, 2.0, 1.9)]
    assert strikes_for(rows, max_fail=3) == 0


def test_a_premiere_that_never_happens_eventually_gives_up():
    """Free retries cannot be unbounded — a cancelled or mis-detected premiere would
    search hourly forever. After the grace period it starts costing."""
    rows = [{"error": _PREMIERE, "days_ago": d} for d in (400, 380, 360)]
    assert strikes_for(rows, max_fail=3) == 3


def test_the_grace_boundary_is_the_configured_one():
    assert strikes_for([{"error": _PREMIERE, "days_ago": 29}], max_fail=3,
                       not_yet_grace_days=30) == 0
    assert strikes_for([{"error": _PREMIERE, "days_ago": 31}], max_fail=3,
                       not_yet_grace_days=30) == 1


def test_three_403s_still_reach_the_limit():
    """403 stays a normal retryable failure — it must not become free, or a genuinely
    broken video would be searched forever."""
    rows = [{"error": _403, "days_ago": d} for d in (0.2, 0.1, 0)]
    assert strikes_for(rows, max_fail=3) == 3


def test_a_mixed_history_adds_up():
    rows = [{"error": _PREMIERE, "days_ago": 1}, {"error": _403, "days_ago": 1},
            {"error": "timeout", "days_ago": 0}]
    assert strikes_for(rows, max_fail=3) == 2


def test_bare_strings_and_junk_are_tolerated():
    assert strikes_for(["timeout", None, {}, {"error": None}], max_fail=3) >= 0
    assert strikes_for(None, max_fail=3) == 0
    assert strikes_for([{"error": _PREMIERE, "days_ago": "n/a"}], max_fail=3) == 0


# ── what the user is told ────────────────────────────────────────────────────

def test_the_403_message_names_the_actual_fix():
    """Five identical 'Forbidden' rows across five channels tell you nothing. The
    cause is nearly always a stale yt-dlp, so the message says so and gives the
    command — otherwise this silently strikes out five videos while you wonder."""
    msg = human_reason(_403)
    assert "yt-dlp" in msg and "pip install -U yt-dlp" in msg


def test_the_members_message_says_it_needs_a_membership():
    assert "membership" in human_reason(_MEMBERS).lower()


def test_the_premiere_message_says_it_will_keep_checking():
    msg = human_reason(_PREMIERE).lower()
    assert "released" in msg and "keep checking" in msg


def test_an_ordinary_failure_gets_no_invented_explanation():
    """Making something up would be worse than the original text."""
    assert human_reason("Connection reset by peer") is None
    assert human_reason(None) is None


def test_a_cluster_of_403s_is_recognisable_as_one_problem():
    assert looks_like_stale_ytdlp([_403, _403, _403]) is True
    assert looks_like_stale_ytdlp([_403, _MEMBERS, _PREMIERE]) is False
    assert looks_like_stale_ytdlp([]) is False


# ── the point of reading stored text ─────────────────────────────────────────

def test_the_live_history_is_re_judged_without_a_migration():
    """Boulder's five struck-out videos, exactly as they sit in the DB today. Four
    stay struck out (correctly — they are paywalled). The premiere comes back, which
    is the outcome the whole change exists for."""
    live = {
        "NANuxb4Z_pw": [_MEMBERS] * 3,
        "h54BlzF2t0k": [_MEMBERS] * 3,
        "BStazeMbRSQ": [_MEMBERS] * 3,
        "5A6bhBxUABA": [_MEMBERS] * 3,
        "O4-YG3L8pog": [_PREMIERE] * 3,
    }
    strikes = {v: strikes_for([{"error": e, "days_ago": 2} for e in errs], max_fail=3)
               for v, errs in live.items()}
    revived = [v for v, n in strikes.items() if n < 3]
    assert revived == ["O4-YG3L8pog"]
