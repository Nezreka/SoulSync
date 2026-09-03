"""#youtube-drain: back off instead of giving up, and SAY what happened.

Boulder's report: "17 undownloaded items but when I start the automation,
nothing." The drain was working exactly as written - all 17 had spent a
three-strike budget, so every one was filtered out and the run reported "No
wished YouTube videos to download". Two things were wrong:

* Ten of the seventeen had only ever hit *transient* errors. Three retryable
  failures on a bad afternoon disqualified a video permanently, with no decay
  and nothing in the app able to un-stick it. Only a GONE failure (deleted,
  private, members-only) deserves that.
* Whatever the reason, the run said the wishlist was empty. A user cannot act
  on that.

Now: GONE is permanent, everything else waits on the same doubling backoff the
movie/TV wishlist uses, and the completion line names the split.
"""

from __future__ import annotations

import pytest

from core.automation.handlers.video_process_youtube_wishlist import (
    _retry_verdict, auto_video_process_youtube_wishlist, skip_tally, videos_to_enqueue)


def _v(vid):
    return {"video_id": vid, "video_title": "Video " + vid, "channel_id": "c1"}


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _lines(deps):
    return [p.get("log_line") for p in deps.progress if p.get("log_line")]


# ── the verdict ──────────────────────────────────────────────────────────────

def test_a_deleted_video_is_never_retried():
    assert _retry_verdict({"permanent": True, "strikes": 3}, 3) == "permanent"


def test_transient_failures_only_buy_a_wait():
    """The ten videos on Boulder's install that were written off for good."""
    assert _retry_verdict({"strikes": 3, "hours_since_last": 0.5}, 3) == "waiting"
    assert _retry_verdict({"strikes": 3, "hours_since_last": 2}, 3) == "go"


def test_the_wait_grows_with_the_strikes():
    """Same doubling schedule as the movie/TV wishlist — one standard."""
    assert _retry_verdict({"strikes": 6, "hours_since_last": 4}, 3) == "waiting"   # needs 8h
    assert _retry_verdict({"strikes": 6, "hours_since_last": 9}, 3) == "go"


def test_a_video_under_the_cap_is_always_ready():
    assert _retry_verdict({"strikes": 1, "hours_since_last": 0}, 3) == "go"


def test_aggressive_policy_retries_without_waiting():
    assert _retry_verdict({"strikes": 9, "hours_since_last": 0.1}, 3, "aggressive") == "go"


def test_manual_policy_stops_automation_after_one_failure():
    assert _retry_verdict({"strikes": 1, "hours_since_last": 99}, 3, "manual") == "waiting"


def test_no_history_at_all_is_ready():
    assert _retry_verdict(None, 3) == "go"
    assert _retry_verdict({}, 3) == "go"


def test_a_never_dated_failure_does_not_wedge_the_video():
    """hours_since_last is None when history has no usable timestamp. Treating
    that as 'still waiting' would be the old permanent skip by another name."""
    assert _retry_verdict({"strikes": 9, "hours_since_last": None}, 3) == "go"


# ── the tally that the run reports ───────────────────────────────────────────

def test_the_tally_accounts_for_every_wished_video():
    wanted = [_v("gone"), _v("waiting"), _v("ready"), _v("busy"), {"video_title": "no id"}]
    state = {"gone": {"permanent": True}, "waiting": {"strikes": 3, "hours_since_last": 0.1}}
    tally = skip_tally(wanted, already_ids=["busy"], retry_state=state)
    assert tally == {"ready": 1, "permanent": 1, "waiting": 1, "in_flight": 1, "no_id": 1}
    assert sum(tally.values()) == len(wanted), "a wished video went unaccounted for"


def test_the_tally_matches_what_actually_gets_queued():
    wanted = [_v("gone"), _v("waiting"), _v("ready")]
    state = {"gone": {"permanent": True}, "waiting": {"strikes": 3, "hours_since_last": 0.1}}
    assert len(videos_to_enqueue(wanted, [], state)) == skip_tally(wanted, [], state)["ready"]


def test_source_retry_policy_changes_automation_selection():
    wanted = [_v("again"), {**_v("manual"), "channel_id": "c2"}]
    state = {"again": {"strikes": 5, "hours_since_last": 0.1},
             "manual": {"strikes": 1, "hours_since_last": 99}}

    def settings(cid):
        return {"retry_policy": "aggressive"} if cid == "c1" else {"retry_policy": "manual"}

    ready = videos_to_enqueue(wanted, [], state, source_settings=settings)
    tally = skip_tally(wanted, [], state, source_settings=settings)
    assert [v["video_id"] for v in ready] == ["again"]
    assert tally["ready"] == 1 and tally["waiting"] == 1


# ── what the user is told ────────────────────────────────────────────────────

def _run(wanted, state, **kw):
    deps = _Deps()
    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "x", "max_concurrent": 3}, deps,
        youtube_root=lambda: "/yt", fetch_wanted=lambda: wanted, active_ids=lambda: [],
        running_count=lambda: 0, enqueue=kw.get("enqueue", lambda v, r: 1),
        start_next=lambda: None, reap=lambda: 0,
        retry_state=lambda: state, source_settings=kw.get("source_settings", lambda cid: {}),
        recent_errors=lambda: [])
    return res, deps


def test_a_wishlist_of_skipped_videos_is_not_reported_as_empty():
    """THE bug report. Seventeen wished, nothing queued, and the run said there
    was nothing to download."""
    wanted = [_v("g%d" % i) for i in range(6)] + [_v("t%d" % i) for i in range(11)]
    state = {"g%d" % i: {"permanent": True} for i in range(6)}
    state.update({"t%d" % i: {"strikes": 3, "hours_since_last": 0.1} for i in range(11)})

    res, deps = _run(wanted, state)
    done = _lines(deps)[-1]
    assert "17 wished video(s)" in done
    assert "6 unavailable" in done
    assert "11 waiting out a retry backoff" in done
    assert res["skipped"]["permanent"] == 6


def test_a_genuinely_empty_wishlist_still_says_so():
    _, deps = _run([], {})
    assert _lines(deps)[-1] == "No wished YouTube videos to download"


def test_the_disk_guard_is_no_longer_silent():
    """enqueue returning None is the disk guard, which only ever logged to
    app.log — the run reported the same 'nothing to download'."""
    _, deps = _run([_v("a")], {}, enqueue=lambda v, r: None)
    done = _lines(deps)[-1]
    assert "disk-space guard" in done


def test_a_normal_run_is_unchanged():
    res, deps = _run([_v("a"), _v("b")], {})
    assert res["queued"] == 2
    assert "Queued 2 new" in _lines(deps)[-1]



def test_the_drain_clears_already_downloaded_wishlist_rows_before_counting():
    deps = _Deps()
    wanted = [_v("still")]
    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "x", "max_concurrent": 3}, deps,
        youtube_root=lambda: "/yt", fetch_wanted=lambda: wanted,
        clear_completed_wishlist=lambda: 2,
        active_ids=lambda: [], running_count=lambda: 0,
        enqueue=lambda v, r: 1, start_next=lambda: None, reap=lambda: 0,
        retry_state=lambda: {}, recent_errors=lambda: [])
    assert res["queued"] == 1
    assert any("Cleared 2 already-downloaded" in line for line in _lines(deps))
