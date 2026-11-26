"""keep a plain dict cache from growing for the life of the process.

a ttl memo keyed by an id, a search or a label replaces an entry when it is
refreshed and never drops one, so every key ever asked for stays (#2253).
"""

from __future__ import annotations

from typing import Any, MutableMapping


def put_bounded(cache: MutableMapping[Any, Any], key: Any, value: Any, max_entries: int) -> None:
    """``cache[key] = value``, then drop the oldest entries past ``max_entries``.

    oldest by insertion: a refreshed key moves to the end. safe to call
    without a lock; a race only drops one entry more or less."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > max_entries:
        try:
            cache.pop(next(iter(cache)), None)
        except (StopIteration, RuntimeError):
            break
