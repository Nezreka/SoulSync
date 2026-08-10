"""Run blocking clients in bounded pools independent of event loops."""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
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
    # wrap_future, not a poll loop: the loop is woken by the worker's own
    # completion callback instead of 20 times a second per in-flight call,
    # and there is no 50 ms latency floor on a fast worker. Not
    # loop.run_in_executor(None, ...) — that installs a loop-OWNED default
    # executor, which is exactly what these shared pools exist to avoid.
    return await asyncio.wrap_future(executor.submit(func, *args, **kwargs))


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
