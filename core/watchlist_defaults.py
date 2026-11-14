"""Watchlist defaults and the artists that follow them (#1822).

the old model was a global override: the global release rules did nothing
until you flipped a switch, and then they beat every artist's own rules.
now they are defaults. an artist follows them (``follow_defaults``) or has
its own. the legacy switch still counts as "everyone follows" so nothing an
api client set stops working.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# the release rules a default covers, with the value a fresh install uses
INCLUDE_DEFAULTS: Dict[str, bool] = {
    'include_albums': True,
    'include_eps': True,
    'include_singles': True,
    # live albums. a live version on any other release is the next rule (#2082)
    'include_live': False,
    'include_live_tracks': False,
    'include_remixes': False,
    'include_acoustic': False,
    'include_compilations': False,
    'include_instrumentals': False,
    'include_other_artists': False,
}
INCLUDE_KEYS = tuple(INCLUDE_DEFAULTS)


# a rule that came later takes the value of the one it was split from until it
# is set itself: one Live switch covered live albums and live tracks (#2082)
SPLIT_FROM: Dict[str, str] = {'include_live_tracks': 'include_live'}


def default_values(config_manager) -> Dict[str, bool]:
    """the default release rules, from watchlist.global_include_*."""
    values = {}
    for key, fallback in INCLUDE_DEFAULTS.items():
        unset = None if key in SPLIT_FROM else fallback
        try:
            raw = config_manager.get(f'watchlist.global_{key}', unset)
        except Exception:  # noqa: BLE001 - a config hiccup reads as the fallback
            raw = unset
        if raw is None:
            raw = values.get(SPLIT_FROM.get(key, ''), fallback)
        values[key] = bool(raw)
    return values


def stored_rule(artist: Any, key: str):
    """one of an artist's own rules as stored, None when it was never set. a
    split rule never set reads as the rule it was split from."""
    def read(name):
        return artist.get(name) if isinstance(artist, dict) else getattr(artist, name, None)
    raw = read(key)
    if raw is None and key in SPLIT_FROM:
        raw = read(SPLIT_FROM[key])
    return raw


def legacy_override_on(config_manager) -> bool:
    try:
        return bool(config_manager.get('watchlist.global_override_enabled', False))
    except Exception:  # noqa: BLE001
        return False


def follows_defaults(artist: Any, override_on: bool = False) -> bool:
    """does this artist use the defaults? a row or object with follow_defaults.

    a missing or null flag means its own rules: that's what every artist had
    before defaults existed."""
    if override_on:
        return True
    flag = artist.get('follow_defaults') if isinstance(artist, dict) else getattr(
        artist, 'follow_defaults', None)
    return bool(flag) if flag is not None else False


def stamp_value(row: Dict[str, Any], defaults: Dict[str, bool], override_on: bool) -> int:
    """what the migration stores for an existing artist: 1 when it already
    gets the defaults (override on, or its rules equal them), else 0. either
    way the rules a scan applies to it are the same the day after."""
    if override_on:
        return 1
    for key, value in defaults.items():
        stored = stored_rule(row, key)
        stored = INCLUDE_DEFAULTS[key] if stored is None else bool(stored)
        if stored != value:
            return 0
    return 1


def effective_rules(artist: Any, config_manager, override_on: Optional[bool] = None) -> Dict[str, bool]:
    """the rules a scan applies to this artist."""
    if override_on is None:
        override_on = legacy_override_on(config_manager)
    if follows_defaults(artist, override_on):
        return default_values(config_manager)
    out = {}
    for key, fallback in INCLUDE_DEFAULTS.items():
        raw = stored_rule(artist, key)
        out[key] = fallback if raw is None else bool(raw)
    return out


def retire_legacy_override(database, config_manager) -> bool:
    """turn a still-on global override into "every artist follows the defaults".

    same rules for everyone before and after, but from then on an artist can
    be given its own rules without the switch silently beating them. runs
    from the api and the scanner, never from the db migration: config lives in
    the same database file and the migration holds a write lock on it.
    returns True when it retired the switch."""
    if not legacy_override_on(config_manager):
        return False
    conn = database._get_connection()
    try:
        conn.execute("UPDATE watchlist_artists SET follow_defaults = 1")
        conn.commit()
    finally:
        conn.close()
    config_manager.set('watchlist.global_override_enabled', False)
    return True


def reset_all_to_defaults(database, profile_id: int) -> int:
    """every artist on this profile's watchlist follows the defaults again."""
    conn = database._get_connection()
    try:
        cur = conn.execute(
            "UPDATE watchlist_artists SET follow_defaults = 1 "
            "WHERE profile_id = ? AND COALESCE(follow_defaults, 0) = 0", (int(profile_id),))
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


__all__ = [
    'INCLUDE_DEFAULTS', 'INCLUDE_KEYS', 'SPLIT_FROM', 'default_values', 'stored_rule', 'legacy_override_on',
    'follows_defaults', 'stamp_value', 'effective_rules', 'retire_legacy_override',
    'reset_all_to_defaults',
]
