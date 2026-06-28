"""Bulk-fetch pagination seam — pages a server fetch while feeding the no-progress
watchdog every page (the DXP4800 "Fetching all tracks in bulk… stuck 300s" bug).
Pure: a fake fetch_page stands in for the server, a list records progress calls."""

import math

from core.library.bulk_paginate import (
    paginate_all_items,
    DEFAULT_PAGE_SIZE,
)


def _server(total, *, fail_at=None, fail_times=0):
    """A fake server holding `total` items. Returns the right page slice for
    (start_index, limit). Optionally returns None (a failed request) the first
    `fail_times` times it's called at offset `fail_at`."""
    items = list(range(total))
    state = {"fails": 0}

    def fetch_page(start_index, limit):
        if fail_at is not None and start_index == fail_at and state["fails"] < fail_times:
            state["fails"] += 1
            return None
        return items[start_index:start_index + limit]

    return fetch_page


def test_returns_every_item_across_pages():
    out = paginate_all_items(_server(7148), page_size=1000)
    assert out == list(range(7148))


def test_progress_fed_every_page_not_once_for_whole_library():
    # The watchdog-feed invariant: progress count scales with N/page_size, so the
    # gap between progress beats is one page — never the whole library. A single
    # 7148-track library used to emit ZERO progress (one 10k page) and stall.
    calls = []
    paginate_all_items(_server(7148), page_size=1000, report_progress=calls.append)
    assert len(calls) == math.ceil(7148 / 1000) == 8


def test_sub_page_library_still_reports_progress():
    # Regression: the old loop skipped progress on the final/only page, so a
    # library smaller than one page reported nothing → watchdog starved.
    calls = []
    out = paginate_all_items(_server(500), page_size=1000, report_progress=calls.append)
    assert out == list(range(500))
    assert len(calls) == 1            # was 0 before the fix


def test_exact_multiple_of_page_size():
    calls = []
    out = paginate_all_items(_server(2000), page_size=1000, report_progress=calls.append)
    assert out == list(range(2000))
    assert len(calls) == 2            # two full pages, both reported


def test_empty_library():
    calls = []
    out = paginate_all_items(_server(0), page_size=1000, report_progress=calls.append)
    assert out == []
    assert calls == []


def test_no_progress_callback_is_safe():
    assert paginate_all_items(_server(2500), page_size=1000) == list(range(2500))


def test_failed_page_shrinks_then_succeeds():
    # First request at offset 0 fails once; the helper halves the page size, retries,
    # and still returns everything — the slow-server resilience path.
    waits = []
    out = paginate_all_items(
        _server(1500, fail_at=0, fail_times=1),
        page_size=1000, min_page_size=250,
        on_retry_wait=lambda: waits.append(1),
    )
    assert out == list(range(1500))
    assert waits == [1]               # waited once before the retry


def test_gives_up_after_repeated_failures_at_floor():
    # A page that always fails at the floor must terminate (not loop forever) and
    # return what was gathered — here nothing, since it fails on the first page.
    def always_fail(_start, _limit):
        return None
    out = paginate_all_items(always_fail, page_size=250, min_page_size=250)
    assert out == []


def test_default_page_size_is_watchdog_safe():
    # A guard on the constant itself: the default must be far below a library size
    # that would fit in one request, so progress is always paged.
    assert DEFAULT_PAGE_SIZE <= 1000
