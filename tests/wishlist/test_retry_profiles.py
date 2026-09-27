"""Configurable wishlist retry profiles (area F).

The standard profile is byte-identical to the backoff ladder that shipped
with javiavid; aggressive and patient change the cadence, and a validated
custom profile is API-only. The active profile lives under the
wishlist_retry_profile metadata key; anything unreadable falls back to
standard so backoff can never strand a track.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.wishlist.retry_backoff import (
    AGGRESSIVE_PROFILE,
    PATIENT_PROFILE,
    STANDARD_PROFILE,
    RETRY_PROFILE_METADATA_KEY,
    builtin_retry_profiles,
    cooldown_for,
    cooldown_seconds,
    get_active_retry_profile,
    is_due,
    list_retry_profiles,
    profile_to_json,
    set_active_retry_profile,
    split_due_for_retry,
)


class _Meta:
    """Just get/set_metadata — the only surface profiles touch."""

    def __init__(self):
        self.store = {}

    def get_metadata(self, key, default=None):
        return self.store.get(key, default)

    def set_metadata(self, key, value):
        self.store[key] = value


@pytest.fixture()
def db():
    return _Meta()


# ── the profiles ─────────────────────────────────────────────────────────────

def test_standard_profile_is_byte_identical_to_the_current_ladder():
    for n in (0, 1, 2, 3, 4, 5, 25, None, 'nope'):
        assert cooldown_for(STANDARD_PROFILE, n) == cooldown_seconds(n)
    assert cooldown_seconds(2) == 4 * 3600
    assert cooldown_seconds(3) == 24 * 3600
    assert cooldown_seconds(4) == 7 * 24 * 3600


def test_aggressive_is_sooner_and_patient_is_later_than_standard():
    for n in (2, 3, 4, 10):
        assert (cooldown_for(AGGRESSIVE_PROFILE, n)
                < cooldown_seconds(n)
                < cooldown_for(PATIENT_PROFILE, n))
    assert cooldown_for(AGGRESSIVE_PROFILE, 2) == 3600
    assert cooldown_for(PATIENT_PROFILE, 4) == 14 * 24 * 3600


def test_is_due_and_split_honor_the_profile():
    now = datetime(2026, 9, 26, 12, 0, 0)
    track = {'retry_count': 2,
             'last_attempted': (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')}
    assert not is_due(track, now)                                    # standard: 4h
    assert is_due(track, now, AGGRESSIVE_PROFILE)                    # aggressive: 1h
    assert not is_due(track, now, PATIENT_PROFILE)                   # patient: 24h
    due, cooling = split_due_for_retry([track], now, AGGRESSIVE_PROFILE)
    assert due == [track] and cooling == []
    due, cooling = split_due_for_retry([track], now)
    assert due == [] and cooling == [track]


# ── custom validation ────────────────────────────────────────────────────────

def test_valid_custom_profile_roundtrips(db):
    payload = {'profile': 'custom', 'ladder': {'2': 1800, '5': 7200},
               'max_cooldown': 86400, 'label': 'Mine'}
    profile = set_active_retry_profile(db, payload)
    assert profile['name'] == 'custom'
    assert cooldown_for(get_active_retry_profile(db), 2) == 1800
    assert cooldown_for(get_active_retry_profile(db), 5) == 7200
    assert cooldown_for(get_active_retry_profile(db), 9) == 86400


@pytest.mark.parametrize('payload', [
    {'profile': 'custom', 'ladder': {}, 'max_cooldown': 60},          # empty ladder
    {'profile': 'custom', 'ladder': 'nope', 'max_cooldown': 60},      # not a dict
    {'profile': 'custom', 'ladder': {'1': 60}, 'max_cooldown': 60},   # attempt < 2
    {'profile': 'custom', 'ladder': {'2': -5}, 'max_cooldown': 60},   # negative
    {'profile': 'custom', 'ladder': {'2': 'soon'}, 'max_cooldown': 60},
    {'profile': 'custom', 'ladder': {'2': 60}},                       # no max_cooldown
    {'profile': 'custom', 'ladder': {'2': 60}, 'max_cooldown': -1},
    {'profile': 'turbo'},                                            # unknown name
    {'profile': ''},
    'not a dict',
])
def test_invalid_profiles_are_rejected(db, payload):
    with pytest.raises(ValueError):
        set_active_retry_profile(db, payload)
    assert RETRY_PROFILE_METADATA_KEY not in db.store


# ── resolution ───────────────────────────────────────────────────────────────

def test_default_is_standard_when_nothing_is_stored(db):
    assert get_active_retry_profile(db)['name'] == 'standard'


def test_unreadable_or_unknown_stored_values_fall_back_to_standard(db):
    db.set_metadata(RETRY_PROFILE_METADATA_KEY, 'not json{{{')
    assert get_active_retry_profile(db)['name'] == 'standard'
    db.set_metadata(RETRY_PROFILE_METADATA_KEY, '{"name": "turbo"}')
    assert get_active_retry_profile(db)['name'] == 'standard'


def test_set_and_get_builtin_roundtrip(db):
    profile = set_active_retry_profile(db, {'profile': 'aggressive'})
    assert profile['name'] == 'aggressive'
    assert RETRY_PROFILE_METADATA_KEY in db.store
    assert get_active_retry_profile(db)['name'] == 'aggressive'
    set_active_retry_profile(db, {'profile': 'standard'})
    assert get_active_retry_profile(db)['name'] == 'standard'


def test_list_shows_builtins_then_the_active_custom(db):
    names = [p['name'] for p in list_retry_profiles(db)]
    assert names == ['standard', 'aggressive', 'patient']
    set_active_retry_profile(db, {'profile': 'custom', 'ladder': {'2': 60},
                                  'max_cooldown': 120})
    assert [p['name'] for p in list_retry_profiles(db)] == [
        'standard', 'aggressive', 'patient', 'custom']


def test_profile_json_shape_survives_metadata(db):
    set_active_retry_profile(db, {'profile': 'patient'})
    stored = db.store[RETRY_PROFILE_METADATA_KEY]
    assert profile_to_json(get_active_retry_profile(db))['ladder'] == {'2': 86400, '3': 259200}
    assert '"ladder"' in stored  # ladder keys are JSON strings
