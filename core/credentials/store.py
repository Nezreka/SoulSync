"""Named, switchable service-credential sets — pure logic (Phase 0 foundation).

Today every auth service (Spotify, Tidal, Deezer, Qobuz, Plex, Jellyfin,
Navidrome) holds ONE credential set in config, and clients are global singletons
built from that single slot. This module is the groundwork for letting an admin
save MULTIPLE named credential sets per service ("pills") that each profile can
switch between, without anyone but the admin creating them.

Kept PURE — service registry, payload validation, and active-set selection,
free of DB/Flask so it's unit-testable. Encrypted storage lives in MusicDatabase
(service_credentials / profile_service_credentials tables); runtime client
resolution + UI come in later phases. Nothing here changes existing behaviour;
it's dormant capability until wired.
"""

from __future__ import annotations

# Services that support multiple named credential sets, mapped to the payload
# keys that MUST be present for a set to be usable. Extra keys (OAuth tokens,
# redirect URIs, quality prefs) are allowed and preserved — these are only the
# minimum required to validate a set the admin is saving.
SERVICE_CREDENTIAL_SCHEMA = {
    'spotify':   ('client_id', 'client_secret'),
    'tidal':     ('access_token', 'refresh_token'),
    'deezer':    ('arl',),
    'qobuz':     ('user_auth_token',),
    'plex':      ('base_url', 'token'),
    'jellyfin':  ('base_url', 'api_key'),
    'navidrome': ('base_url', 'username', 'password'),
}

SUPPORTED_SERVICES = frozenset(SERVICE_CREDENTIAL_SCHEMA)


def is_supported_service(service: str) -> bool:
    """True when the service supports named credential sets."""
    return service in SERVICE_CREDENTIAL_SCHEMA


def validate_credential_payload(service: str, payload):
    """Return ``(ok, missing_keys)`` for a credential set.

    Valid when every required key for the service is present and truthy. An
    unknown service is invalid with no missing list (caller should reject it
    as unsupported, not as "incomplete").
    """
    required = SERVICE_CREDENTIAL_SCHEMA.get(service)
    if required is None:
        return False, []
    if not isinstance(payload, dict):
        return False, list(required)
    missing = [k for k in required if not payload.get(k)]
    return (not missing), missing


def pick_active_credential(credentials, selected_id):
    """From ``credentials`` (a list of dicts each carrying ``id``), return the
    one whose id == ``selected_id``.

    Returns None when there's no selection OR the selected id isn't present —
    i.e. a stale pointer whose credential set was deleted. The caller then
    falls back to the global/admin default, so a deleted set never breaks a
    profile. Pure + stale-safe.
    """
    if not selected_id:
        return None
    for cred in credentials or []:
        if cred.get('id') == selected_id:
            return cred
    return None


__all__ = [
    'SERVICE_CREDENTIAL_SCHEMA',
    'SUPPORTED_SERVICES',
    'is_supported_service',
    'validate_credential_payload',
    'pick_active_credential',
]
