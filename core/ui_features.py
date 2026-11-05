"""what each profile chose to see (#1909).

soulsync does a lot. most people use a slice of it, and the rest is noise in
the sidebar, the dashboard and the tabs. this is the one list of the parts a
profile can switch off, and the two presets. it's a preference, not access:
a hidden page still opens from a link, and what a profile may open is still
allowed_pages (core.permissions). admins set that, everyone sets this.

the core never hides: dashboard, search, library, watchlist, wishlist,
downloads, settings, help, on both sides. without those soulsync isn't
soulsync.

stored per profile in the metadata table as a json list of the features that
are OFF, so a feature added later shows up for everyone by default.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

# id, label, one line, which side it's on, the pages it owns.
# pages are shell page ids (route-manifest.ts / data-page / data-video-page).
FEATURES: List[Dict[str, Any]] = [
    {"id": "discover", "label": "Discover",
     "desc": "Picks, mixes and new music made from what you play.",
     "group": "music", "pages": ["discover"]},
    {"id": "playlists", "label": "Playlist sync",
     "desc": "Mirror playlists from Spotify, Tidal, YouTube and more onto your server.",
     "group": "music", "pages": ["sync"]},
    {"id": "explorer", "label": "Playlist Explorer",
     "desc": "Every playlist from every source in one visual tree.",
     "group": "music", "pages": ["playlist-explorer"]},
    {"id": "import", "label": "Import",
     "desc": "Bring in music you already have on disk.",
     "group": "music", "pages": ["import"]},
    {"id": "stats", "label": "Listening stats",
     "desc": "Charts of what you play, plus the weekly card on the dashboard.",
     "group": "music", "pages": ["stats"]},
    {"id": "music_videos", "label": "Music videos",
     "desc": "The Videos tab in search and music videos on artist pages.",
     "group": "music", "pages": []},
    {"id": "sample_studio", "label": "Sample Studio",
     "desc": "Split songs into stems and chop samples.",
     "group": "music", "pages": ["sample-studio"]},
    {"id": "podcasts", "label": "Podcasts",
     "desc": "Subscribe to shows and get new episodes on their own.",
     "group": "more", "pages": ["podcasts"]},
    {"id": "audiobooks", "label": "Audiobooks",
     "desc": "Browse Audible, follow authors and fetch new books.",
     "group": "more", "pages": ["audiobooks"]},
    {"id": "video", "label": "Movies & TV",
     "desc": "The whole video side: shows, films and YouTube channels.",
     "group": "more", "pages": [
         "video-dashboard", "video-search", "video-discover", "video-calendar",
         "video-library", "video-watchlist", "video-wishlist", "video-requests",
         "video-downloads", "video-automations", "video-chat", "video-tools",
         "video-settings", "video-issues", "video-help"]},
    # inside the video side. only offered while the video side is on; the
    # video dashboard keeps its "what's airing next" either way, that's its job
    {"id": "video_discover", "label": "Video Discover",
     "desc": "Trending, upcoming and recommended movies and shows.",
     "group": "video", "pages": ["video-discover"]},
    {"id": "video_calendar", "label": "Calendar",
     "desc": "Episode air dates and movie releases, plus the iCal feed.",
     "group": "video", "pages": ["video-calendar"]},
    {"id": "chat", "label": "Chat & Arcade",
     "desc": "Talk with other SoulSync users and play games.",
     "group": "more", "pages": ["chat", "video-chat"]},
    {"id": "automations", "label": "Automations",
     "desc": "Build your own schedules and triggers. The built-in jobs keep running either way.",
     "group": "advanced", "pages": ["automations", "video-automations"]},
    {"id": "tools", "label": "Tools",
     "desc": "Library maintenance, repair jobs, backups and database tools.",
     "group": "advanced", "pages": ["tools", "video-tools"]},
]

FEATURE_IDS = tuple(f["id"] for f in FEATURES)

GROUPS = [
    {"id": "music", "label": "Music"},
    {"id": "more", "label": "More than music"},
    {"id": "video", "label": "Movies & TV"},
    {"id": "advanced", "label": "For tinkerers"},
]

# simple = the music core plus discover, playlist sync and import. that's the
# list people asked for in #1909 (download, library, follow artists,
# discover, playlists for plexamp). import stays because it's the inbox:
# downloads the auto-import can't identify wait there, hiding it strands them.
SIMPLE_KEEPS = ("discover", "playlists", "import")
PRESETS: Dict[str, List[str]] = {
    "full": [],
    "simple": [fid for fid in FEATURE_IDS if fid not in SIMPLE_KEEPS],
}

PRESET_LABELS = {
    "simple": {"label": "Simple", "desc": "Music, Discover, playlists and import. Everything else tucked away."},
    "full": {"label": "Everything", "desc": "Every part of SoulSync, the way it ships."},
}


def normalize_off(value: Any) -> List[str]:
    """a clean off list: known ids only, no dupes, catalog order. anything
    unreadable is 'nothing off', the way soulsync ships."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, (list, tuple, set)):
        return []
    wanted = {str(v) for v in value}
    return [fid for fid in FEATURE_IDS if fid in wanted]


def preset_for(off: Iterable[str]) -> str:
    """which preset this off list is, or 'custom'."""
    off_set = set(normalize_off(list(off)))
    for name, preset_off in PRESETS.items():
        if off_set == set(preset_off):
            return name
    return "custom"


def hidden_pages(off: Iterable[str]) -> List[str]:
    """every page id the off features own."""
    off_set = set(normalize_off(list(off)))
    pages: List[str] = []
    for f in FEATURES:
        if f["id"] in off_set:
            pages.extend(p for p in f["pages"] if p not in pages)
    return pages


def off_from_purposes(purposes: Iterable[str]) -> List[str]:
    """setup asks what soulsync is for. turn the answer into what's off, so a
    music-only install doesn't open on podcasts, audiobooks and a video side.
    only the 'more than music' parts follow the answer; the music extras stay
    as they ship and the profile can trim them later."""
    picked = {str(p) for p in purposes or []}
    off = []
    if "podcasts" not in picked:
        off.append("podcasts")
    if "audiobooks" not in picked:
        off.append("audiobooks")
    if "video" not in picked and "youtube" not in picked:
        off.append("video")
    if "music" not in picked:
        off.extend(["discover", "playlists", "explorer", "import", "stats",
                    "music_videos", "sample_studio"])
    return normalize_off(off)


def _key(profile_id: int) -> str:
    return f"ui_features_off:{int(profile_id)}"


def load_off(db, profile_id: Optional[int]) -> List[str]:
    if profile_id is None:
        return []
    try:
        return normalize_off(db.get_metadata(_key(profile_id)))
    except Exception:  # noqa: BLE001 - unreadable pref means show everything
        return []


def save_off(db, profile_id: int, off: Iterable[str]) -> List[str]:
    clean = normalize_off(list(off))
    db.set_metadata(_key(profile_id), json.dumps(clean))
    # set_metadata logs and swallows a failed write. read it back so the
    # page never says saved when it wasn't.
    if normalize_off(db.get_metadata(_key(profile_id))) != clean:
        raise RuntimeError("couldn't save which features are shown")
    return clean


def seed_from_purposes(db, profile_id: Optional[int], purposes: Iterable[str]) -> Optional[List[str]]:
    """first-run setup's answer, saved only when this profile never chose.
    running setup again (the dev preview, a reset) must not undo a choice
    someone made by hand afterwards."""
    if profile_id is None or db.get_metadata(_key(profile_id)) is not None:
        return None
    return save_off(db, profile_id, off_from_purposes(purposes))


def clear(db, profile_id: int) -> None:
    """a deleted profile takes its choice with it, so a new profile that gets
    the same id starts with everything on."""
    db.set_metadata(_key(profile_id), "[]")


def has_chosen(db, profile_id: Optional[int]) -> bool:
    """did this profile ever save a choice (or get one from setup)? the
    dashboard offers the picker once to everyone who hasn't."""
    if profile_id is None:
        return True
    try:
        return db.get_metadata(_key(profile_id)) is not None
    except Exception:  # noqa: BLE001 - can't tell, don't nag
        return True


# --- pausing what nobody uses (#1909) ---------------------------------------
#
# hiding is per profile, but background jobs are per install. so a part can
# only stop running once EVERY profile has it hidden, and only when an admin
# says so. the video side already has its own master switch for its jobs
# (and ships paused), so this covers the two spoken-word sides.

BACKGROUND_FEATURES = ("podcasts", "audiobooks")
_PAUSED_KEY = "ui_features_paused"


def feature_for_automation(auto: Optional[Dict[str, Any]]) -> Optional[str]:
    """which hideable part an automation works for, if any."""
    auto = auto or {}
    action = str(auto.get("action_type") or "")
    trigger = str(auto.get("trigger_type") or "")
    if action.startswith("audiobook_") or trigger.startswith("audiobook_"):
        return "audiobooks"
    if "podcast" in action or "podcast" in trigger:
        return "podcasts"
    return None


def _profile_hides(db, profile: Dict[str, Any], feature: str) -> bool:
    """this profile doesn't use the feature: hid it, or can't open it."""
    pages = next((f["pages"] for f in FEATURES if f["id"] == feature), [])
    allowed = profile.get("allowed_pages")
    if not profile.get("is_admin") and isinstance(allowed, list) and pages:
        if not any(p in allowed for p in pages):
            return True
    return feature in load_off(db, profile.get("id"))


def everyone_hid(db, feature: str) -> bool:
    """True only when every profile has this part hidden (or can't reach it).
    can't read the profiles, or the list came back as the error fallback
    (no allowed_pages key), then it's in use."""
    try:
        profiles = db.get_all_profiles() or []
    except Exception:  # noqa: BLE001 - unknown means someone might use it
        return False
    if not profiles or any("allowed_pages" not in p for p in profiles):
        return False
    return all(_profile_hides(db, p, feature) for p in profiles)


def load_paused(db) -> List[str]:
    try:
        raw = db.get_metadata(_PAUSED_KEY)
        wanted = set(json.loads(raw)) if raw else set()
    except Exception:  # noqa: BLE001 - unreadable means nothing paused
        return []
    return [f for f in BACKGROUND_FEATURES if f in wanted]


def set_paused(db, feature: str, paused: bool) -> List[str]:
    if feature not in BACKGROUND_FEATURES:
        raise ValueError(f"{feature} can't be paused")
    now = set(load_paused(db))
    if paused:
        now.add(feature)
    else:
        now.discard(feature)
    clean = [f for f in BACKGROUND_FEATURES if f in now]
    db.set_metadata(_PAUSED_KEY, json.dumps(clean))
    if load_paused(db) != clean:
        raise RuntimeError("couldn't save the background pause")
    return clean


def background_paused(db, feature: Optional[str]) -> bool:
    """the engine's question: skip this part's jobs right now? the admin asked
    AND nobody uses it. a profile switching it back on resumes it with no
    second step."""
    if feature not in BACKGROUND_FEATURES:
        return False
    return feature in load_paused(db) and everyone_hid(db, feature)


def background_state(db) -> Dict[str, Dict[str, bool]]:
    paused = set(load_paused(db))
    return {f: {"paused": f in paused, "everyone_hid": everyone_hid(db, f)}
            for f in BACKGROUND_FEATURES}


def payload(off: Iterable[str], chosen: bool = True) -> Dict[str, Any]:
    clean = normalize_off(list(off))
    return {
        "chosen": bool(chosen),
        "features": FEATURES,
        "groups": GROUPS,
        "presets": [{"id": k, **PRESET_LABELS[k], "off": v} for k, v in PRESETS.items()],
        "off": clean,
        "preset": preset_for(clean),
        "hidden_pages": hidden_pages(clean),
    }


__all__ = ["FEATURES", "FEATURE_IDS", "GROUPS", "PRESETS", "normalize_off", "preset_for",
           "hidden_pages", "off_from_purposes", "seed_from_purposes", "load_off", "save_off", "clear", "has_chosen",
           "BACKGROUND_FEATURES", "feature_for_automation", "everyone_hid", "load_paused",
           "set_paused", "background_paused", "background_state",
           "payload"]
