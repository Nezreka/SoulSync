"""Background profile context.

Work that runs OUTSIDE a web request — the automation engine, scheduled jobs —
has no Flask session, so ``get_current_profile_id()`` falls back to admin
(profile 1). That's wrong for an automation owned by a non-admin: their
playlist pull, their per-profile writes, should act as THEM.

This lets the engine declare "the work below is running for profile X" around a
unit of background work (set/reset in a try/finally). ``get_current_profile_id``
consults it only when there's no real request — so an actual logged-in session
always wins, and nothing changes for foreground/admin paths. Built on a
``ContextVar`` so the value is scoped to the running call and reset cleanly,
even on thread-pool reuse.
"""

from __future__ import annotations

import contextvars

_background_profile_id: "contextvars.ContextVar[int | None]" = contextvars.ContextVar(
    "background_profile_id", default=None
)


def set_background_profile(profile_id):
    """Declare the profile for the current background unit of work. Returns a
    token to pass to reset_background_profile (use in try/finally)."""
    return _background_profile_id.set(profile_id)


def reset_background_profile(token) -> None:
    """Restore the previous background profile (clears the override)."""
    try:
        _background_profile_id.reset(token)
    except Exception:
        # Token from a different context — clear to the default rather than leak.
        _background_profile_id.set(None)


def get_background_profile():
    """The background profile in effect, or None if none is set."""
    return _background_profile_id.get()


__all__ = ["set_background_profile", "reset_background_profile", "get_background_profile"]
