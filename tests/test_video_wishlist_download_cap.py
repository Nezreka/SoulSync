"""Per-run download cap for the movie/episode wishlist drain.

The hourly drain used to hand EVERY found item to slskd / the torrent / usenet
client in one run — a 25-movie find bogged the download clients down. The
automation config now carries "Max downloads per run" (default 3): once that
many items have been grabbed this run, the rest stay on the wishlist and are
picked up on subsequent runs. (Deliberately NOT the YouTube drain's
"Max simultaneous downloads" label — that one bounds true concurrency; this is
a per-run grab budget.)

Repro-first: these tests fail on the uncapped drain (every found item
enqueued) and pass once the grab budget is enforced.
"""

from __future__ import annotations

import pytest

from core.automation.handlers.video_process_wishlist import (
    auto_video_process_wishlist,
    item_key,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _cand(fn, *, accepted=True, score=10):
    return {"filename": fn, "title": fn, "username": "u", "size_bytes": 1000,
            "quality_label": "WEBDL-1080p", "accepted": accepted, "score": score}


def _items(n):
    return [{"tmdb_id": i + 1, "title": "Movie %d" % (i + 1), "year": "2020"}
            for i in range(n)]


def _run(items, *, config=None, active=None, media_type="movie"):
    """Drive the handler with instant search/enqueue fakes; every item finds a
    release and every grab succeeds, so the ONLY limiter is the download cap."""
    enq = []
    seen = []

    def search(item, mt):
        seen.append(item_key(item, mt))
        return [_cand("rel-%s.mkv" % (item.get("tmdb_id") or item.get("show_tmdb_id")))]

    def enqueue(item, best, cands, mt, target):
        enq.append(item_key(item, mt))
        return True

    cfg = {"_automation_id": "cap-test"}
    cfg.update(config or {})
    res = auto_video_process_wishlist(
        cfg, _Deps(), media_type=media_type,
        fetch_items=lambda mt: items, active_keys=lambda mt: set(active or set()),
        target_dir=lambda mt: "/movies", search=search, enqueue=enqueue)
    return res, enq, seen


# ── the cap ──────────────────────────────────────────────────────────────────
def test_drain_caps_grabs_at_default_of_three():
    # repro: 10 found items, default config — uncapped code grabs all 10.
    res, enq, seen = _run(_items(10))
    assert res["status"] == "completed"
    assert res["grabbed"] == 3
    assert len(enq) == 3
    assert len(set(enq)) == 3            # no double-grab of one item
    assert res["searched"] == len(seen)  # accounting is honest
    assert res.get("deferred", 0) >= 10 - res["searched"]  # the rest wait


def test_max_simultaneous_downloads_of_one_is_respected():
    res, enq, _ = _run(_items(10), config={"max_simultaneous_downloads": 1})
    assert res["grabbed"] == 1 and len(enq) == 1


def test_max_simultaneous_downloads_of_five_is_respected():
    res, enq, _ = _run(_items(10), config={"max_simultaneous_downloads": 5})
    assert res["grabbed"] == 5 and len(enq) == 5


def test_zero_or_missing_value_falls_back_to_default():
    # a blank field in the builder must not mean "grab nothing" or "grab all"
    res, enq, _ = _run(_items(10), config={"max_simultaneous_downloads": 0})
    assert res["grabbed"] == 3 and len(enq) == 3


def test_garbage_value_fails_open_to_default():
    # a mis-typed config (e.g. "abc" pasted into the number field) must not
    # crash the drain or strand it — it falls back to the default 3.
    res, enq, _ = _run(_items(10), config={"max_simultaneous_downloads": "abc"})
    assert res["status"] == "completed"
    assert res["grabbed"] == 3 and len(enq) == 3


def test_refused_grab_hands_its_slot_back():
    # a refused grab is not a download: the slot is refunded so later items can
    # still grab this run, and the item is tallied as refused (not deferred).
    items = _items(4)
    enq = []

    def search(item, mt):
        return [_cand("rel-%s.mkv" % item["tmdb_id"])]

    def enqueue(item, best, cands, mt, target):
        enq.append(item_key(item, mt))
        if item["tmdb_id"] == 1:
            return {"ok": False, "error": "client refused it"}
        return True

    res = auto_video_process_wishlist(
        {"_automation_id": "cap-test", "max_concurrent": 1,
         "max_simultaneous_downloads": 2}, _Deps(),
        media_type="movie",
        fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        target_dir=lambda mt: "/movies", search=search, enqueue=enqueue)
    assert res["grabbed"] == 2       # items 2 and 3, despite item 1 failing
    assert res["refused"] == 1       # item 1's client refusal
    assert res["deferred"] == 1      # item 4 waited for the next run
    assert len(enq) == 3             # every attempted item really was attempted


# ── no starvation, no loss ───────────────────────────────────────────────────
def test_deferred_items_are_picked_up_on_the_next_run():
    items = _items(5)
    res1, enq1, _ = _run(items, config={"max_simultaneous_downloads": 3})
    assert res1["grabbed"] == 3 and len(enq1) == 3
    # next hourly tick: the 3 grabbed are in flight, so the other 2 go.
    res2, enq2, _ = _run(items, config={"max_simultaneous_downloads": 3},
                         active=set(enq1))
    assert res2["grabbed"] == 2 and len(enq2) == 2
    assert set(enq1) | set(enq2) == {item_key(it, "movie") for it in items}
    assert not (set(enq1) & set(enq2))   # nothing grabbed twice


def test_deferred_items_accrue_no_search_attempts():
    # the no-starvation property: deferral is a budget wait, NOT evidence the
    # release doesn't exist — so it must never be recorded as a fruitless
    # search, or backoff would strand the item forever. max_concurrent=1 keeps
    # the run deterministic: items 1-3 grab, items 4-5 defer pre-search.
    items = _items(5)
    outcomes = []

    def record_outcome(item, mt, grabbed, refusal=None):
        outcomes.append((item_key(item, mt), grabbed))

    res = auto_video_process_wishlist(
        {"_automation_id": "cap-test", "max_concurrent": 1,
         "max_simultaneous_downloads": 3}, _Deps(),
        media_type="movie",
        fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        target_dir=lambda mt: "/movies",
        search=lambda item, mt: [_cand("rel-%s.mkv" % item["tmdb_id"])],
        enqueue=lambda item, best, cands, mt, target: True,
        record_outcome=record_outcome)
    assert res["status"] == "completed"
    assert res["grabbed"] == 3
    assert res.get("deferred", 0) == 2
    assert len(outcomes) == 3               # the 3 grabbed items, nothing else
    assert all(grabbed for _, grabbed in outcomes)  # grabs reset search_attempts
    recorded = {key for key, _ in outcomes}
    deferred = {item_key(it, "movie") for it in items} - recorded
    assert len(deferred) == 2              # the 2 deferred items
    # …and neither deferred item was ever recorded: no search_attempts bump,
    # so backoff can never park them out of reach.


# ── the two knobs stay independent ───────────────────────────────────────────
def test_max_concurrent_still_only_sizes_search_parallelism():
    # searches are NOT the limiter: a wide search pool with the default
    # download cap still grabs exactly 3.
    res, enq, _ = _run(_items(6), config={"max_concurrent": 6})
    assert res["grabbed"] == 3 and len(enq) == 3


def test_download_cap_does_not_limit_search_parallelism():
    # and the download cap does not shrink the search pool: one narrow search
    # lane with a wide download cap still grabs everything found.
    res, enq, _ = _run(_items(4),
                       config={"max_concurrent": 1, "max_simultaneous_downloads": 10})
    assert res["grabbed"] == 4 and len(enq) == 4


# ── block config ─────────────────────────────────────────────────────────────
def _block(t):
    from core.automation.blocks import ACTIONS
    return next(b for b in ACTIONS if b["type"] == t)


def _field(block, key):
    return next(f for f in block["config_fields"] if f["key"] == key)


@pytest.mark.parametrize("block_type", ["video_process_movie_wishlist",
                                        "video_process_episode_wishlist"])
def test_blocks_expose_a_distinct_download_cap_field(block_type):
    block = _block(block_type)
    searches = _field(block, "max_concurrent")
    downloads = _field(block, "max_simultaneous_downloads")
    assert searches["label"] == "Max simultaneous searches"
    assert downloads["label"] == "Max downloads per run"
    assert downloads["default"] == 3 and downloads["min"] == 1
    # the hint names what the cap does and does not touch
    assert "RSS Sync" in downloads["description"] and "manual" in downloads["description"]
    # unmistakable in the builder: "searches" vs "downloads", never the same word
    assert "search" not in downloads["label"].lower()
    assert "download" in downloads["label"].lower()
    assert downloads["label"] != searches["label"]
