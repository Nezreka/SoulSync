"""Progressive retry backoff for failing wishlist tracks (javiavid).

Every scheduled wishlist cycle used to retry EVERY failing track — a track
that's been unavailable for months got a full search burned on it every hour,
forever, out of the shared slskd search budget. Once the per-track attempt
counter is real (retry_count + last_attempted, stamped after every failed
cycle), each track earns a cooldown that grows with its failure count:

    attempts 0-1  →  no cooldown (every cycle, as before)
    attempts 2    →  4 hours
    attempts 3    →  24 hours
    attempts 4+   →  7 days

Tracks never auto-abandon — a 7-day cadence keeps watching indefinitely, and
the Failing filter + manual search stay the escalation path. The MANUAL
"Process Wishlist Now" button bypasses backoff entirely (the click is the
override, like Sonarr's manual search); only scheduled cycles apply it.

Timestamps: wishlist_tracks.last_attempted is SQLite CURRENT_TIMESTAMP — UTC,
'YYYY-MM-DD HH:MM:SS'. Comparisons here are UTC-to-UTC. Everything fails OPEN
(unparseable/missing timestamp → due) — backoff must never strand a track.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

_HOUR = 3600
_LADDER = {2: 4 * _HOUR, 3: 24 * _HOUR}
_MAX_COOLDOWN = 7 * 24 * _HOUR

RETRY_PROFILE_METADATA_KEY = 'wishlist_retry_profile'
"""Metadata key holding the active retry profile (JSON)."""


def _profile(name: str, label: str, description: str,
             ladder: Dict[int, int], max_cooldown: int) -> Dict[str, Any]:
    return {'name': name, 'label': label, 'description': description,
            'ladder': dict(ladder), 'max_cooldown': max_cooldown}


STANDARD_PROFILE = _profile(
    'standard', 'Standard',
    'The default cadence: no wait for the first two failures, then 4 hours, '
    'a day, and a week between retries.',
    {2: 4 * _HOUR, 3: 24 * _HOUR}, 7 * 24 * _HOUR)
AGGRESSIVE_PROFILE = _profile(
    'aggressive', 'Aggressive',
    'Retries failing tracks sooner: an hour, then 4 hours, then daily.',
    {2: 1 * _HOUR, 3: 4 * _HOUR}, 24 * _HOUR)
PATIENT_PROFILE = _profile(
    'patient', 'Patient',
    'Gives failing tracks more room: a day, then 3 days, then every two weeks.',
    {2: 24 * _HOUR, 3: 3 * 24 * _HOUR}, 14 * 24 * _HOUR)

BUILTIN_PROFILES = (STANDARD_PROFILE, AGGRESSIVE_PROFILE, PATIENT_PROFILE)


def builtin_retry_profiles() -> List[Dict[str, Any]]:
    """Copies of the three built-in profiles, in UI order."""
    return [dict(p, ladder=dict(p['ladder'])) for p in BUILTIN_PROFILES]


def _builtin(name: str) -> Dict[str, Any] | None:
    for p in BUILTIN_PROFILES:
        if p['name'] == name:
            return dict(p, ladder=dict(p['ladder']))
    return None


def cooldown_for(profile: Dict[str, Any] | None, retry_count: Any) -> int:
    """Cooldown a track has earned from its failure count under a profile."""
    try:
        n = int(retry_count or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 1:
        return 0
    profile = profile or {}
    ladder: Dict[int, int] = {}
    for key, value in (profile.get('ladder') or {}).items():
        try:
            ladder[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    if n in ladder:
        return ladder[n]
    try:
        return int(profile.get('max_cooldown', _MAX_COOLDOWN))
    except (TypeError, ValueError):
        return _MAX_COOLDOWN


def cooldown_seconds(retry_count: Any) -> int:
    """Cooldown a track has earned from its failure count.

    Signature and behavior unchanged: the standard profile, byte-identical
    to the ladder that shipped with backoff."""
    return cooldown_for(STANDARD_PROFILE, retry_count)


def validate_custom_retry_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """A user-defined profile: {ladder: {attempt: cooldown_seconds},
    max_cooldown}. Raises ValueError on anything malformed."""
    if not isinstance(data, dict):
        raise ValueError('a custom retry profile needs a ladder and a max_cooldown')
    ladder = data.get('ladder')
    if not isinstance(ladder, dict) or not ladder:
        raise ValueError('a custom retry profile needs a non-empty ladder '
                         '{attempt: cooldown_seconds}')
    clean: Dict[int, int] = {}
    for key, value in ladder.items():
        try:
            attempt = int(key)
        except (TypeError, ValueError):
            raise ValueError(f'ladder key {key!r} is not an attempt count') from None
        if attempt < 2:
            raise ValueError('ladder attempts start at 2 (attempts 0-1 never wait)')
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'ladder cooldown for attempt {attempt} is not seconds') from None
        if seconds < 0:
            raise ValueError('ladder cooldowns cannot be negative')
        clean[attempt] = seconds
    try:
        max_cooldown = int(data.get('max_cooldown'))
    except (TypeError, ValueError):
        raise ValueError('max_cooldown must be seconds (an integer >= 0)') from None
    if max_cooldown < 0:
        raise ValueError('max_cooldown cannot be negative')
    label = str(data.get('label') or 'Custom').strip() or 'Custom'
    return _profile('custom', label, str(data.get('description') or '').strip(),
                    clean, max_cooldown)


def profile_to_json(profile: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe shape for the API and metadata."""
    return {'name': profile.get('name'), 'label': profile.get('label'),
            'description': profile.get('description') or '',
            'ladder': {str(k): int(v) for k, v in (profile.get('ladder') or {}).items()},
            'max_cooldown': int(profile.get('max_cooldown') or 0)}


def _profile_from_json(data: Any) -> Dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    name = str(data.get('name') or '').strip().lower()
    if name == 'custom':
        try:
            return validate_custom_retry_profile(data)
        except ValueError:
            return None
    return _builtin(name)


def get_active_retry_profile(database) -> Dict[str, Any]:
    """The profile scheduled wishlist cycles use. Anything unreadable or
    invalid falls back to standard — backoff must never strand a track."""
    try:
        raw = database.get_metadata(RETRY_PROFILE_METADATA_KEY)
        profile = _profile_from_json(json.loads(raw) if raw else None)
    except Exception:  # noqa: BLE001 - fail open to standard
        profile = None
    return profile or _builtin('standard')


def set_active_retry_profile(database, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and store the active profile. ``payload`` is
    {"profile": "standard" | "aggressive" | "patient"} or
    {"profile": "custom", "ladder": {...}, "max_cooldown": n, ...}.
    Raises ValueError on anything invalid."""
    if not isinstance(payload, dict):
        raise ValueError('a profile body needs {"profile": name}')
    name = str(payload.get('profile') or payload.get('name') or '').strip().lower()
    profile = _profile_from_json({**payload, 'name': name})
    if profile is None:
        raise ValueError(f'unknown retry profile {name!r}: expected standard, '
                         'aggressive, patient, or a custom ladder')
    database.set_metadata(RETRY_PROFILE_METADATA_KEY, json.dumps(profile_to_json(profile)))
    return profile


def list_retry_profiles(database) -> List[Dict[str, Any]]:
    """The built-ins, plus the active custom profile when one is set. The custom
    entry is display-only: it can be set through the API, not picked in the
    UI."""
    profiles = builtin_retry_profiles()
    active = get_active_retry_profile(database)
    if active.get('name') == 'custom' and not any(p['name'] == 'custom' for p in profiles):
        profiles.append(active)
    return profiles


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def is_due(track: Dict[str, Any], now_utc: datetime,
           profile: Dict[str, Any] | None = None) -> bool:
    """Is this track eligible for the current scheduled cycle? Fail-open."""
    cd = cooldown_for(profile or STANDARD_PROFILE, track.get('retry_count'))
    if cd <= 0:
        return True
    last = _parse_ts(track.get('last_attempted'))
    if last is None:
        return True
    return (now_utc - last).total_seconds() >= cd


def split_due_for_retry(
    tracks: Iterable[Dict[str, Any]], now_utc: datetime,
    profile: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(due, cooling) — cooling tracks sit this scheduled cycle out."""
    due: List[Dict[str, Any]] = []
    cooling: List[Dict[str, Any]] = []
    for t in tracks:
        (due if is_due(t, now_utc, profile) else cooling).append(t)
    return due, cooling
