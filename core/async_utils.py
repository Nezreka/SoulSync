"""Run blocking clients in bounded pools independent of event loops."""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import contextvars
from typing import Any, Callable, TypeVar


_T = TypeVar("_T")

_SLOW_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="soulsync-slow-io",
)
_CONTROL_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="soulsync-control-io",
)


async def _run(
    executor: concurrent.futures.ThreadPoolExecutor,
    func: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    # Two things have to hold at once here.
    #
    # OURS: Python 3.14.6 can lose the cross-thread selector wake-up used by
    # asyncio.wrap_future(), most visibly when the worker future completes with
    # an exception: the concurrent future is done but the awaiting task sleeps
    # forever. So completion is observed on the owner loop. A 1 ms interval is
    # bounded and remains far below the old 50 ms polling floor. We still use
    # our process-wide pools rather than a loop-owned default executor, and
    # cancellation attempts to remove work that has not started.
    #
    # UPSTREAM (ported, not dropped): executor threads do not inherit
    # ContextVars, so request-local source-quality intent was lost on every
    # blocking call. Copy the context explicitly and run the work inside it.
    # That fix is orthogonal to how completion is observed, so it survives the
    # workaround above.
    context = contextvars.copy_context()
    future = executor.submit(context.run, func, *args, **kwargs)
    try:
        while not future.done():
            await asyncio.sleep(0.001)
        return future.result()
    except asyncio.CancelledError:
        future.cancel()
        raise


async def run_blocking(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run slow/provider I/O without creating a loop-owned executor."""
    return await _run(_SLOW_IO_EXECUTOR, func, *args, **kwargs)


async def run_control(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run download-client control I/O isolated from slow provider calls."""
    return await _run(_CONTROL_IO_EXECUTOR, func, *args, **kwargs)


def _shutdown() -> None:
    for executor in (_SLOW_IO_EXECUTOR, _CONTROL_IO_EXECUTOR):
        executor.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown)


__all__ = ["run_blocking", "run_control"]
