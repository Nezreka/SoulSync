"""the media-server accounts profiles are linked to, for code outside the
music side.

a profile linked to its own Plex account (Connect with Plex, or a linked Plex
Home user) has that account's server token stored on its profile row. the
video side reads it to keep each member's own watch state (#1941); it reaches
it through here, the same way it reaches profiles through core.profile_context,
so core/video never imports the music database itself.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("media_accounts")


def _db():
    from database.music_database import get_database
    return get_database()


def plex_link(profile_id) -> Optional[dict]:
    """the profile's linked Plex account ({'id', 'title', 'token'}) or None.
    a link that can't be read counts as no link."""
    try:
        return _db().get_profile_plex_home_user(int(profile_id))
    except Exception:   # noqa: BLE001
        logger.debug("reading the plex link for profile %s failed", profile_id, exc_info=True)
        return None


def plex_linked_profiles() -> List[Tuple[int, str]]:
    """(profile id, server token) for every profile linked to its own Plex
    account. a profile whose link can't be read is skipped, never guessed."""
    try:
        db = _db()
        profiles = db.get_all_profiles() or []
    except Exception:   # noqa: BLE001
        logger.debug("listing profiles failed", exc_info=True)
        return []
    out = []
    for p in profiles:
        pid = p.get("id")
        if pid is None:
            continue
        try:
            link = db.get_profile_plex_home_user(int(pid))
        except Exception:   # noqa: BLE001
            link = None
        if link and link.get("token"):
            out.append((int(pid), link["token"]))
    return out


def profile_names() -> Tuple[dict, Optional[str]]:
    """({profile id: name}, the admin profile's name) -- labels for jobs that
    say WHO did something. Empty and None when the profiles can't be read."""
    try:
        profiles = _db().get_all_profiles() or []
    except Exception:   # noqa: BLE001
        logger.debug("listing profiles failed", exc_info=True)
        return {}, None
    names = {int(p["id"]): p.get("name") or f"profile {p['id']}"
             for p in profiles if p.get("id") is not None}
    admins = sorted(int(p["id"]) for p in profiles if p.get("id") is not None and p.get("is_admin"))
    return names, (names.get(admins[0]) if admins else None)
