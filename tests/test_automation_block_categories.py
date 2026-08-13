"""Every builder block lands in a real drawer of the palette.

The sidebar groups the ~100 blocks by `category`. A block that matches no rule
falls into "Other", which is deliberately visible rather than silent — but it
should never be the state we ship in, because "Other" is where a block goes to
be un-findable, which is the exact problem the grouping was added to solve.
"""

from __future__ import annotations

import pytest

from core.automation.blocks import (
    ACTIONS,
    CATEGORY_ORDER,
    NOTIFICATIONS,
    TRIGGERS,
    block_category,
    blocks_for_scope,
)

ALL_BLOCKS = TRIGGERS + ACTIONS + NOTIFICATIONS


def test_no_block_falls_through_to_other():
    homeless = sorted(b['type'] for b in ALL_BLOCKS if block_category(b['type']) == 'Other')
    assert not homeless, f"no category rule matches: {homeless}"


def test_every_category_used_is_in_the_display_order():
    used = {block_category(b['type']) for b in ALL_BLOCKS}
    missing = sorted(used - set(CATEGORY_ORDER))
    assert not missing, f"category missing from CATEGORY_ORDER: {missing}"


def test_rules_are_stable_for_the_types_users_see_most():
    """Pins the handful whose drawer would be surprising if a rule reordered.

    Order matters in _CATEGORY_RULES — 'webhook_received' must not be caught by
    the Notify rule's bare 'webhook', and a repair scan must not be filed under
    Library just because its name contains 'scan'.
    """
    assert block_category('webhook_received') == 'External'
    assert block_category('discord_webhook') == 'Notify'
    assert block_category('music_repair_scan_completed') == 'Maintenance'
    assert block_category('scan_library') == 'Library'
    assert block_category('process_wishlist') == 'Wishlist'
    assert block_category('scan_watchlist') == 'Watchlist'
    assert block_category('daily_time') == 'Timing'


@pytest.mark.parametrize('scope', ['music', 'video'])
def test_the_served_payload_carries_a_category_on_every_block(scope):
    payload = blocks_for_scope(scope)
    for key in ('triggers', 'actions', 'notifications'):
        for block in payload[key]:
            assert block.get('category'), f"{scope}/{key}: {block.get('type')} has no category"
    assert payload['category_order'] == CATEGORY_ORDER


def test_tagging_does_not_mutate_the_module_level_definitions():
    """blocks_for_scope copies; a tag leaking back would make the second call
    for the other scope see stale data."""
    blocks_for_scope('music')
    assert all('category' not in b for b in TRIGGERS)
