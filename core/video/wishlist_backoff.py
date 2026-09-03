"""When a stuck wishlist row is allowed to be searched again.

The drain used to re-search everything eligible on every hourly tick, forever.
On Boulder's install that meant a 1999 film nobody has seeded had been searched
959 times, and 142 of 172 wished episodes were past 20 attempts - each one a
~20s blocking Soulseek search, every hour, asking a question that had been
answered hundreds of times. The row already recorded ``search_attempts`` and
``last_search_at``; nothing read them.

The rule: the first few attempts are free (a release genuinely does show up a
day or two late), then the wait doubles - 1h, 2h, 4h ... - capped at a week. A
row that has failed 900 times is checked weekly instead of hourly, and the
attempt counter resets the moment it grabs, so a title that finally seeds
returns to full speed immediately.

A user asking for a search explicitly is never gated by this. Backoff is about
what the machine does on its own.
"""

from __future__ import annotations

FREE_ATTEMPTS = 3      # searched every tick, no waiting
MAX_DELAY_HOURS = 168  # one week, the longest we ever wait
_SHIFT_CAP = 20        # keeps 1<<n inside 64 bits; well past MAX_DELAY_HOURS


def retry_delay_hours(attempts: int) -> int:
    """Hours to wait after ``attempts`` fruitless searches before trying again."""
    try:
        n = int(attempts or 0)
    except (TypeError, ValueError):
        n = 0
    if n < FREE_ATTEMPTS:
        return 0
    return min(MAX_DELAY_HOURS, 1 << min(n - FREE_ATTEMPTS, _SHIFT_CAP))


# The same schedule as SQL, so the drain can filter in the query instead of
# reading the whole wishlist and discarding most of it. `{a}` is the row alias.
# The shift is clamped the same way: SQLite's 1<<n goes to 0 past 63, which
# would silently mean "no backoff at all" for exactly the rows that need it most.
DUE_FOR_SEARCH_SQL = (
    "(COALESCE({a}.search_attempts, 0) < %d "
    " OR {a}.last_search_at IS NULL "
    " OR {a}.last_search_at <= datetime('now', printf('-%%d hours', "
    "      MIN(%d, 1 << MIN(COALESCE({a}.search_attempts, 0) - %d, %d)))))"
) % (FREE_ATTEMPTS, MAX_DELAY_HOURS, FREE_ATTEMPTS, _SHIFT_CAP)


def due_sql(alias: str = "w") -> str:
    """The WHERE fragment gating a row to its next allowed search."""
    return DUE_FOR_SEARCH_SQL.format(a=alias)
