"""profile permission decisions, one copy.

the download check lived three times (web_server, api/helpers, and not at all
in the background wishlist run) and every copy read a missing profile as the
admin: ``int(pid or 1)``. with no profile picked that waved the download
through. these take the lookups as arguments so they test without the app.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

DOWNLOADS_OFF = "Downloads are disabled for this profile."
NO_PROFILE = "Pick a profile first."


def profile_can_download(profile: Optional[Dict[str, Any]]) -> bool:
    """a profile row's own answer. the admin profile always can."""
    if not profile:
        return False
    try:
        if int(profile.get("id") or 0) == 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(profile.get("can_download", True))


def download_denied_reason(pid, get_profile: Callable[[int], Optional[Dict[str, Any]]]) -> Optional[str]:
    """why this profile may not start a download, or None when it may."""
    if pid is None:
        return NO_PROFILE
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return NO_PROFILE
    if pid == 1:
        return None
    try:
        profile = get_profile(pid)
    except Exception:
        # a check that can't read the profile row must not lock somebody out
        # of their own downloads over a db hiccup
        return None
    # no row is not a row that says no: get_profile answers None on a read
    # error too. a deleted profile never gets here, the session gate drops it.
    if profile and not profile.get("can_download", True):
        return DOWNLOADS_OFF
    return None


def may_manage_profile(actor_id, actor_is_admin: bool, target_id) -> bool:
    """may this caller change the target profile's name, pin, password or
    access? yourself, always. anyone else, only as an admin. profile 1 is the
    owner: a second admin can't reset its pin or password or demote it,
    because that is a takeover of the install."""
    try:
        actor_id = int(actor_id) if actor_id is not None else None
        target_id = int(target_id)
    except (TypeError, ValueError):
        return False
    if actor_id is None:
        return False
    if actor_id == target_id:
        return True
    if target_id == 1:
        return actor_id == 1
    return bool(actor_is_admin)


# what anyone may see about another profile: enough to draw the picker card
PUBLIC_PROFILE_FIELDS = ("id", "name", "avatar_color", "avatar_url", "is_admin",
                         "has_pin", "has_password", "disabled")


def profile_view_for(profile: Dict[str, Any], *, viewer_id, viewer_is_admin: bool) -> Dict[str, Any]:
    """a profile row as this viewer may see it. admins and the profile itself
    get everything; everyone else (including a locked lock screen) just the
    card, not recovery questions, library folders or linked usernames."""
    if viewer_is_admin:
        return profile
    try:
        if viewer_id is not None and int(viewer_id) == int(profile.get("id")):
            return profile
    except (TypeError, ValueError):
        pass
    return {k: profile.get(k) for k in PUBLIC_PROFILE_FIELDS if k in profile}


# api prefixes that belong to exactly one page. a profile whose page list
# leaves the page out gets a 403 from these too, not just a hidden button.
# shared apis (stats feed the dashboard, automations feed sync, podcasts feed
# the watchlist) stay out: gating them would break the pages that are allowed.
PAGE_EXCLUSIVE_APIS = (
    ("/api/import", "import"),
    ("/api/auto-import", "import"),
    ("/api/playlist-explorer", "playlist-explorer"),
    ("/api/listening-stats", "stats"),
    ("/api/audiobooks", "audiobooks"),
)


def page_for_path(path: str):
    path = path or ""
    for prefix, page in PAGE_EXCLUSIVE_APIS:
        if path == prefix or path.startswith(prefix + "/"):
            return page
    return None


def page_denied(path: str, allowed_pages, is_admin: bool) -> bool:
    """True when this api belongs to a page the profile isn't given.
    None = every page (the default), admins always pass."""
    if is_admin or allowed_pages is None:
        return False
    page = page_for_path(path)
    return page is not None and page not in set(allowed_pages)


__all__ = ["profile_can_download", "download_denied_reason", "DOWNLOADS_OFF", "NO_PROFILE",
           "may_manage_profile", "profile_view_for", "PUBLIC_PROFILE_FIELDS",
           "PAGE_EXCLUSIVE_APIS", "page_for_path", "page_denied"]
