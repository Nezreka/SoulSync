"""paging a discover rail deeper when hide-owned or the language filter thins it out.

the list route asks tmdb for pages in concurrent waves until the rail has
enough items. a slow or stuck tmdb page used to hold the whole response, so
the rail on the page sat on its skeleton with nothing to show (#2158). the
waves now share one time budget: past it the rail answers with what it has,
and next_page points at the first page it didn't use.
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Callable, Iterable

from utils.logging_config import get_logger

logger = get_logger("video.discover_paging")

DEFAULT_BUDGET_S = 10.0


def fill_pages(
    fetch: Callable[[int], Iterable[Any] | None],
    consume: Callable[[Any], bool],
    start_page: int,
    need_more: Callable[[], bool],
    *,
    max_pages: int = 20,
    wave: int = 5,
    budget_s: float = DEFAULT_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, int]:
    """fetch pages from start_page in waves until need_more() is false, a page
    comes back empty (consume returns False) or the budget runs out.

    pages are consumed in order. a page that isn't back when the budget runs
    out is dropped, along with every page after it, so the result never skips
    a page. returns (has_more, next_page).
    """
    deadline = clock() + budget_s
    offset, ran_out = 0, False
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=wave)
    try:
        while offset < max_pages and need_more() and not ran_out:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            futs = [ex.submit(fetch, start_page + offset + i) for i in range(wave)]
            concurrent.futures.wait(futs, timeout=remaining)
            used = 0
            for f in futs:
                if not f.done():
                    break   # out of time: stop at the first page still out
                try:
                    batch = f.result()
                except Exception:  # noqa: BLE001 - one bad page reads as an empty one
                    logger.debug("discover page fetch failed", exc_info=True)
                    batch = None
                used += 1
                if not consume(batch):
                    ran_out = True   # an empty page means tmdb has no more
            offset += used
            if used < wave:
                break
    finally:
        # don't wait on a page that missed the budget, its answer is dropped anyway
        ex.shutdown(wait=False, cancel_futures=True)
    return (not ran_out), start_page + offset
