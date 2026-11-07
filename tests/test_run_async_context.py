"""run_async carries the caller's context variables into the coroutine.

The coroutine runs on the shared loop's thread, in the pump's own context, so
a ContextVar set by the caller was invisible inside it. candidate_binding is
one: every torrent/usenet token was minted for profile 1, and a household
member's pick from the download inspector or manual search was refused.
"""

import contextvars

from utils.async_helpers import run_async

_seen = contextvars.ContextVar("seen", default="unset")


async def _read():
    return _seen.get()


def test_the_coroutine_sees_the_callers_context():
    token = _seen.set("caller")
    try:
        assert run_async(_read()) == "caller"
    finally:
        _seen.reset(token)
    assert run_async(_read()) == "unset"


def test_a_candidate_binding_reaches_the_search():
    from core.download_plugins.candidate_store import candidate_binding, current_binding

    async def bound_profile():
        return current_binding().profile_id

    with candidate_binding(7):
        assert run_async(bound_profile()) == 7
