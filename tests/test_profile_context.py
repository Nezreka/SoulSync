"""Background profile context (per-profile automations).

Lets background work (the automation engine) declare which profile it's acting
for, so get_current_profile_id() resolves to the automation's OWNER instead of
admin when there's no web request. A real request must still win; admin/system
(profile 1) and no-override must stay admin.
"""

from __future__ import annotations

from core.profile_context import (
    set_background_profile, reset_background_profile, get_background_profile,
)


def test_set_reset_roundtrip():
    assert get_background_profile() is None
    tok = set_background_profile(5)
    assert get_background_profile() == 5
    reset_background_profile(tok)
    assert get_background_profile() is None


def test_nested_set_reset():
    t1 = set_background_profile(3)
    t2 = set_background_profile(1)          # e.g. an admin/system sub-run
    assert get_background_profile() == 1
    reset_background_profile(t2)
    assert get_background_profile() == 3    # back to the outer profile
    reset_background_profile(t1)
    assert get_background_profile() is None  # fully cleared (no leak)
