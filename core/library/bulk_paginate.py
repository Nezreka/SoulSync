"""Paginate a bulk media-server fetch while feeding the no-progress watchdog.

The library scan fetches every track/album by paging a server API. The DB-update
watchdog (``core/database_update_health.py``) kills a job that reports no progress
for 300s. The old Jellyfin fetch used a single 10 000-item page, so a whole
library came back in ONE request that emitted NO progress while it was in flight —
on a slow server that single request exceeded 300s and the watchdog declared the
job "stuck" even though it was alive, not hung (Discord: DXP4800 NAS, 7148 tracks,
"Fetching all tracks in bulk…").

``paginate_all_items`` pages at a size chosen so progress is emitted on a cadence
set by the PAGE SIZE, not the library size — the watchdog is fed every page, so it
can never starve mid-fetch regardless of how big the library is. It is pure: all
I/O lives in the injected ``fetch_page``, so the pagination + progress + failure-
shrink logic is unit-testable without a server.

This does NOT change WHAT is fetched (same query, same fields, same items) — only
how it's paged and that every page reports progress (the old loop skipped progress
on the final/only page, which is the entire bug for a sub-page-size library).
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

# Page size for bulk library fetches. Small enough that a single request stays
# well under the 300s no-progress watchdog even on a slow NAS, and that progress
# is reported every page. NOT a performance knob — a resilience/observability one.
DEFAULT_PAGE_SIZE = 1000

# Floor the failure-shrink can reach before giving up — a server that can't return
# even this many items in one request is genuinely struggling.
DEFAULT_MIN_PAGE_SIZE = 250


def paginate_all_items(
    fetch_page: Callable[[int, int], Optional[List[Any]]],
    *,
    report_progress: Optional[Callable[[str], None]] = None,
    label: str = "items",
    page_size: int = DEFAULT_PAGE_SIZE,
    min_page_size: int = DEFAULT_MIN_PAGE_SIZE,
    on_retry_wait: Optional[Callable[[], None]] = None,
) -> List[Any]:
    """Page through ``fetch_page(start_index, limit)`` until the server is drained.

    ``fetch_page`` returns the page's items (a list, possibly empty = end), or
    ``None`` to signal a FAILED request (timeout/error) — on failure the page size
    is halved down to ``min_page_size`` and retried, then abandoned after two
    consecutive failures at the floor.

    Progress is reported after EVERY non-empty page (including the final/only one),
    so a no-progress watchdog is fed on a cadence set by ``page_size`` — never by
    the total library size. Returns every item gathered.
    """
    items: List[Any] = []
    start_index = 0
    limit = page_size
    consecutive_failures = 0

    while True:
        batch = fetch_page(start_index, limit)

        if batch is None:  # failed request
            consecutive_failures += 1
            if on_retry_wait is not None:
                on_retry_wait()
            if limit > min_page_size:
                limit = max(min_page_size, limit // 2)
                consecutive_failures = 0  # give the smaller batch a fair chance
                continue
            if consecutive_failures >= 2:
                break  # struggling at the floor — stop with what we have
            continue

        consecutive_failures = 0
        if not batch:
            break  # drained

        items.extend(batch)
        # Feed the watchdog on EVERY page — this is the line the old loop only ran
        # when there was a *next* page, so a sub-page-size library reported nothing.
        if report_progress is not None:
            report_progress(f"Fetched {len(items)} {label} so far...")

        if len(batch) < limit:
            break  # last (partial) page
        start_index += limit

    return items


__all__ = ["paginate_all_items", "DEFAULT_PAGE_SIZE", "DEFAULT_MIN_PAGE_SIZE"]
