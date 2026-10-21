"""the "finish setting up" card on the dashboard.

each item is worked out from the config every time, so doing the thing
anywhere (settings, the setup flow, an import) ticks it off. slskd shares are read
from slskd itself and the extension is noticed the first time it calls the
api. the round button still ticks anything by hand (setup.checklist_done).
"""

from __future__ import annotations

from typing import Callable

# id, title, description, action label, where the action goes
_ITEMS = (
    ("slskd_shares", "Share some music in slskd",
     "Soulseek bans people who only download. Ticks itself once slskd shares some files.",
     "Open slskd"),
    ("video", "Finish movies and TV",
     "Add a TMDB key so SoulSync can follow shows and films.", "Set up"),
    ("torrent", "Add torrent and usenet",
     "Rare releases through Prowlarr, for music and video.", "Set up"),
    ("spotify", "Connect Spotify",
     "Mirror your playlists and Discover Weekly.", "Connect"),
    ("extension", "Get the browser extension",
     "See what you own while browsing Spotify or Bandcamp. Ticks itself once it connects.",
     "Get it"),
    ("invite", "Invite your household",
     "Everyone gets their own taste, playlists and history.", "Invite"),
)

EXTENSION_URL = "https://github.com/Nezreka/soulsync-extension"

# the companion extension calls /api/v1 from its own pages, so the browser
# stamps the request with the extension's origin. one such call with a valid
# key is proof it's installed and connected.
_EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
_extension_noted = False


def note_api_client(origin, get: Callable, set_: Callable) -> bool:
    """remember the first key-authed call from the extension. cheap after the
    first hit (a process flag), and never raises into the request"""
    global _extension_noted
    if _extension_noted or not str(origin or "").startswith(_EXTENSION_ORIGINS):
        return False
    try:
        if not get("setup.extension_seen", False):
            set_("setup.extension_seen", True)
        _extension_noted = True
        return True
    except Exception:  # noqa: BLE001 - a checklist tick must never break the api
        return False


def uses_soulseek(get) -> bool:
    mode = str(get("download_source.mode", "") or "")
    order = get("download_source.hybrid_order", []) or []
    return mode == "soulseek" or (mode == "hybrid" and "soulseek" in order)


def build(get: Callable, *, profile_count: int, tmdb_key: str = "",
          slskd_shared: bool = False) -> dict:
    """{"dismissed", "items": [{id, title, description, action, done, href?}]}.
    only items that apply to this install are listed"""
    purposes = get("setup.purposes", None)
    purposes = purposes if isinstance(purposes, list) else []
    manual = get("setup.checklist_done", []) or []
    manual = manual if isinstance(manual, list) else []

    applies = {
        "slskd_shares": uses_soulseek(get),
        "video": "video" in purposes,
        "torrent": True,
        "spotify": True,
        "extension": True,
        "invite": True,
    }
    detected = {
        "slskd_shares": bool(slskd_shared),
        "video": bool(str(tmdb_key or "").strip()),
        "torrent": bool(str(get("prowlarr.url", "") or "").strip()),
        "spotify": bool(str(get("spotify.client_id", "") or "").strip()),
        "extension": bool(get("setup.extension_seen", False)),
        "invite": profile_count > 1,
    }
    items = []
    for item_id, title, description, action in _ITEMS:
        if not applies[item_id]:
            continue
        entry = {
            "id": item_id, "title": title, "description": description, "action": action,
            "done": bool(detected[item_id] or item_id in manual),
        }
        if item_id == "extension":
            entry["href"] = EXTENSION_URL
        if item_id == "slskd_shares":
            url = str(get("soulseek.slskd_url", "") or "").rstrip("/")
            if url:
                entry["href"] = f"{url}/shares"
        items.append(entry)
    return {"dismissed": bool(get("setup.checklist_dismissed", False)), "items": items}
