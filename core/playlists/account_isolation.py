"""whose streaming account a playlist list comes from.

browsing "your playlists" on Spotify, Tidal, Deezer, Qobuz or YouTube Music
reads a connected account. a profile can connect its own Spotify or Tidal
(My Accounts); the rest come from the one account set up for the server.

a profile that had connected nothing used to fall back to the server's
account, so a brand-new member opening Add Playlist saw the admin's
playlists, liked songs included (#2301). now a member who hasn't connected
their own account for a service is asked to connect one, and the server's
account is only read for them when the admin has said so
(``security.share_connected_accounts``, off by default).

only the browsing lists ask this. a playlist that is already mirrored keeps
syncing as before, and downloads and metadata are untouched: they have
always run on the server's accounts.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SETTING = "security.share_connected_accounts"
# the services a member can connect for themselves
OWN_ACCOUNT_SERVICES = ("spotify", "tidal")
NAMES = {"spotify": "Spotify", "tidal": "Tidal", "deezer": "Deezer", "qobuz": "Qobuz",
         "ytmusic": "YouTube Music"}


def refusal(service: str, *, profile_id: Any, is_admin: bool, own_connected: bool,
            shared: bool) -> Optional[Dict[str, Any]]:
    """what to answer instead of the server account's playlists, or None when
    the list may be read. pure."""
    if is_admin or profile_id in (None, 1) or own_connected or shared:
        return None
    name = NAMES.get(service, service.title())
    can_connect = service in OWN_ACCOUNT_SERVICES
    return {
        "error": (f"Connect your own {name} account in My Accounts to see your playlists."
                  if can_connect else
                  f"{name} playlists come from the server's account, and it isn't shared with you. "
                  "An admin can share it in Settings."),
        "needs_own_account": True,
        "can_connect": can_connect,
        "service": service,
    }


def refusal_for_request(service: str, *, own_connected: bool = False) -> Optional[Dict[str, Any]]:
    """the same, for the profile making this request. a request with no
    profile context (a worker, a test) is never refused."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return None
        profile_id = getattr(g, "profile_id", None)
        is_admin = bool(getattr(g, "is_admin", False))
    except Exception:  # noqa: BLE001 - no context is the server itself
        return None
    try:
        from core.settings import config_manager
        shared = bool(config_manager.get(SETTING, False))
    except Exception:  # noqa: BLE001 - can't read the setting: not shared
        shared = False
    return refusal(service, profile_id=profile_id, is_admin=is_admin,
                   own_connected=own_connected, shared=shared)


__all__ = ["SETTING", "OWN_ACCOUNT_SERVICES", "refusal", "refusal_for_request"]
