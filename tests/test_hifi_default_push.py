"""One-time auto-push of NEW default HiFi instances to existing installs.

A working instance added to DEFAULT_INSTANCES should reach everyone — but a
default a user deliberately removed must NOT come back. compute_new_default_pushes
is the pure decision; these pin both guarantees.
"""

from __future__ import annotations

from core.hifi_client import compute_new_default_pushes

LEGACY = ['https://a.tf', 'https://b.tf']            # shipped before tracking
DEFAULTS = ['https://a.tf', 'https://b.tf', 'https://new.tf']  # 'new.tf' added later


def test_first_run_pushes_only_the_new_default():
    """offered=None → baseline to legacy, so existing user gets ONLY new.tf,
    not a re-seed of a.tf/b.tf (which they may have curated)."""
    existing = ['https://a.tf']  # user removed b.tf at some point
    to_add, new_offered = compute_new_default_pushes(DEFAULTS, None, LEGACY, existing)
    assert to_add == ['https://new.tf']               # only the genuinely-new one
    assert 'https://b.tf' not in to_add               # removed default NOT resurrected
    assert set(new_offered) == {'https://a.tf', 'https://b.tf', 'https://new.tf'}


def test_already_offered_new_default_not_re_added():
    """Once new.tf has been offered, removing it must stick (no re-add)."""
    offered = ['https://a.tf', 'https://b.tf', 'https://new.tf']
    existing = ['https://a.tf', 'https://b.tf']        # user removed new.tf
    to_add, new_offered = compute_new_default_pushes(DEFAULTS, offered, LEGACY, existing)
    assert to_add == []
    assert set(new_offered) == set(offered)


def test_present_new_default_recorded_not_duplicated():
    """If the new default is already present (fresh install / user added it),
    record it as offered but don't add a duplicate."""
    existing = ['https://a.tf', 'https://b.tf', 'https://new.tf']
    to_add, new_offered = compute_new_default_pushes(DEFAULTS, None, LEGACY, existing)
    assert to_add == []
    assert 'https://new.tf' in new_offered


def test_trailing_slash_insensitive():
    existing = ['https://a.tf/', 'https://new.tf']     # already has new.tf (slash variant)
    to_add, _ = compute_new_default_pushes(DEFAULTS, None, LEGACY, existing)
    assert to_add == []                                # new.tf seen as present


def test_no_new_defaults_is_noop():
    to_add, new_offered = compute_new_default_pushes(LEGACY, None, LEGACY, ['https://a.tf'])
    assert to_add == []
    assert set(new_offered) == set(LEGACY)
