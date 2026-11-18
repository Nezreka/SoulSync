"""what each profile chose to see (#1909).

soulsync does a lot. most people use a slice of it, and the rest is noise in
the sidebar, the dashboard and the tabs. this is the one list of the parts a
profile can switch off, and the two presets. it's a preference, not access:
a hidden page still opens from a link, and what a profile may open is still
allowed_pages (core.permissions). this layer never grants anything.

an admin can set it for someone else and lock it (#2157), and set what a new
profile starts with. a profile that never chose follows that default, so
changing it reaches everyone who didn't pick their own. admins themselves are
never locked and never follow the default.

two layers per profile (#2164): what the admin set for it, and the member's
own pick. unlocked, the member's pick wins (it's their sidebar). locked, only
the admin's set counts (or the default when the admin never set this
profile), the member's pick never does. an admin saving this profile's set
or lock drops the member's old pick, so unlocking later keeps what the admin
set instead of bringing back something hidden from before.

the core never hides: dashboard, search, library, watchlist, wishlist,
downloads, settings, help, on both sides. without those soulsync isn't
soulsync.

stored per profile in the metadata table as a json list of the features that
are OFF, so a feature added later shows up for everyone by default.
ui_features_off:<id> is the member's own pick, ui_features_admin:<id> what an
admin set for them.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

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


def _lock_key(profile_id: int) -> str:
    return f"ui_features_locked:{int(profile_id)}"


def _admin_key(profile_id: int) -> str:
    return f"ui_features_admin:{int(profile_id)}"


_DEFAULT_KEY = "ui_features_default"
_CARRIED_KEY = "ui_features_admin_carried_2164"


def carry_over_2157(db) -> bool:
    """once: #2157 kept the admin's set in the member key. a profile locked by
    hand couldn't write that key itself, so for one with lock "1", a stored
    member key and no admin key yet, that list is what the admin last saved
    and what they see today. move it to the admin key so nothing changes for
    them and the editor shows it as the admin's. unlocked profiles keep theirs.
    a failure logs and tries again on the next read. True once it has run."""
    try:
        if db.get_metadata(_CARRIED_KEY) == "1":
            return True
        getter = getattr(db, "get_all_profiles", None)
        if getter is None:
            return False
        for profile in getter() or []:
            if not admin_sets(profile):
                continue
            pid = int(profile["id"])
            if db.get_metadata(_lock_key(pid)) != "1" or db.get_metadata(_admin_key(pid)) is not None:
                continue
            raw = db.get_metadata(_key(pid))
            if raw is None:
                continue
            clean = json.dumps(normalize_off(raw))
            db.set_metadata(_admin_key(pid), clean)
            if db.get_metadata(_admin_key(pid)) != clean:
                raise RuntimeError(f"couldn't carry over profile {pid}")
            db.delete_metadata(_key(pid))
            logger.info("features: carried profile %s's locked set over to the admin layer (#2164)", pid)
        db.set_metadata(_CARRIED_KEY, "1")
        return True
    except Exception as e:  # noqa: BLE001 - nothing lost, try again next read
        logger.warning("features: carrying #2157 locked sets over failed, will retry: %s", e)
        return False


def _profile(db, profile_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if profile_id is None:
        return None
    getter = getattr(db, "get_profile", None)
    if getter is None:
        return None
    try:
        return getter(int(profile_id))
    except Exception as e:  # noqa: BLE001 - no profile means no admin rules
        logger.debug("features: profile %s unreadable: %s", profile_id, e)
        return None


def admin_sets(profile: Optional[Dict[str, Any]]) -> bool:
    """does the admin's default and lock reach this profile? everyone but
    admins (and the owner)."""
    if not profile or profile.get("is_admin"):
        return False
    try:
        return int(profile.get("id") or 0) != 1
    except (TypeError, ValueError):
        return False


def _sides(profile: Optional[Dict[str, Any]]) -> str:
    if not admin_sets(profile):
        return "both"
    s = profile.get("allowed_sides")
    return s if s in ("music", "video", "both") else "music"


def fixed_on(profile: Optional[Dict[str, Any]]) -> List[str]:
    """parts this profile can't hide. a video-only profile hiding the video
    side was left on a music side it can't open, with no way back (#2157)."""
    return ["video"] if _sides(profile) == "video" else []


def offered(profile: Optional[Dict[str, Any]]) -> List[str]:
    """the switches worth showing this profile: parts it can open, minus the
    ones it can't hide. no profile (the new-profile default) gets them all."""
    if profile is None:
        return list(FEATURE_IDS)
    sides = _sides(profile)
    allowed = profile.get("allowed_pages") if admin_sets(profile) else None
    fixed = set(fixed_on(profile))
    out = []
    for f in FEATURES:
        if f["id"] in fixed:
            continue
        # only the pages on a side this profile has
        pages = [p for p in f["pages"]
                 if sides == "both" or ("video" if p.startswith("video-") else "music") == sides]
        if f["pages"] and not pages:
            continue
        # no pages = lives inside music pages (music videos)
        if not f["pages"] and sides == "video":
            continue
        if isinstance(allowed, list) and pages and not any(p in allowed for p in pages):
            continue
        out.append(f["id"])
    return out


def load_default(db) -> Dict[str, Any]:
    """what a new profile starts with: {off, locked, set}. nothing saved is
    everything on and unlocked, the way soulsync ships."""
    try:
        raw = db.get_metadata(_DEFAULT_KEY)
        data = json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001 - unreadable default is the shipped app
        logger.debug("features: default unreadable: %s", e)
        data = None
    if not isinstance(data, dict):
        return {"off": [], "locked": False, "set": False}
    return {"off": normalize_off(data.get("off")), "locked": bool(data.get("locked")), "set": True}


def template_default(db, profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """the Sidebar & look of the template this member follows (#2166):
    {off, locked, set, template_id, template}, or None when it follows none
    (an admin, a member on its own old settings) or the template has no
    sidebar saved. a database without templates (old test doubles) has none."""
    if not admin_sets(profile):
        return None
    inputs = getattr(db, "get_permission_inputs", None)
    getter = getattr(db, "get_permission_template", None)
    if inputs is None or getter is None:
        return None
    try:
        tid = inputs(int(profile["id"])).get("template_id")
        if tid is None:
            return None
        row = getter(int(tid))
        raw = row.get("sidebar") if row else None
        data = json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001 - unreadable: the install's default stands
        logger.debug("features: template sidebar for %s unreadable: %s", profile.get("id"), e)
        return None
    if not isinstance(data, dict):
        return None
    return {"off": normalize_off(data.get("off")), "locked": bool(data.get("locked")), "set": True,
            "template_id": int(tid), "template": row.get("name") or ""}


def default_for(db, profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """what this profile shows when nobody set its own: its template's
    Sidebar & look (#2166 follow-ups 2 and 8), else what new profiles see."""
    return template_default(db, profile) or load_default(db)


def save_default(db, off: Iterable[str], locked: bool) -> Dict[str, Any]:
    clean = {"off": normalize_off(list(off)), "locked": bool(locked)}
    db.set_metadata(_DEFAULT_KEY, json.dumps(clean))
    back = load_default(db)
    if not back["set"] or back["off"] != clean["off"] or back["locked"] != clean["locked"]:
        raise RuntimeError("couldn't save what new profiles see")
    return back


def _read_off(db, key: str, profile_id: int) -> Optional[List[str]]:
    try:
        raw = db.get_metadata(key)
    except Exception as e:  # noqa: BLE001 - unreadable pref means show everything
        logger.debug("features: %s for %s unreadable: %s", key, profile_id, e)
        return []
    return None if raw is None else normalize_off(raw)


def _stored_off(db, profile_id: int) -> Optional[List[str]]:
    """the member's own pick, None when they never made one."""
    return _read_off(db, _key(profile_id), profile_id)


def _admin_off(db, profile_id: int) -> Optional[List[str]]:
    """what an admin set for this profile, None when they never did."""
    return _read_off(db, _admin_key(profile_id), profile_id)


def admin_set_off(db, profile_id: Optional[int], profile: Optional[Dict[str, Any]] = None) -> List[str]:
    """what the admin wants this profile to see: the set they saved for it,
    or its template's Sidebar & look (else what new profiles see) when they
    never did. this is what a lock shows."""
    if profile_id is None:
        return []
    profile = profile if profile is not None else _profile(db, profile_id)
    if not admin_sets(profile):
        return []
    mine = _admin_off(db, profile_id)
    return default_for(db, profile)["off"] if mine is None else mine


def load_off(db, profile_id: Optional[int], profile: Optional[Dict[str, Any]] = None) -> List[str]:
    """what's off for this profile. unlocked: its own pick, else what the
    admin set (or the default). locked: only what the admin set, the
    member's own pick never wins (#2164). never a part it can't hide."""
    if profile_id is None:
        return []
    carry_over_2157(db)
    profile = profile if profile is not None else _profile(db, profile_id)
    if not admin_sets(profile):
        stored = _stored_off(db, profile_id) or []
    else:
        own = None if is_locked(db, profile_id, profile) else _stored_off(db, profile_id)
        stored = admin_set_off(db, profile_id, profile) if own is None else own
    fixed = set(fixed_on(profile))
    return [f for f in stored if f not in fixed]


def own_off(db, profile_id: Optional[int], profile: Optional[Dict[str, Any]] = None) -> Optional[List[str]]:
    """the member's own pick, for the admin's editor. None when they never
    made one (or they're an admin, whose pick is just what they see)."""
    if profile_id is None:
        return None
    profile = profile if profile is not None else _profile(db, profile_id)
    return _stored_off(db, profile_id) if admin_sets(profile) else None


def follows_default(db, profile_id: Optional[int], profile: Optional[Dict[str, Any]] = None) -> bool:
    """showing what new profiles see: the admin never set this profile, and
    its own pick doesn't count (it has none, or it's locked)."""
    if profile_id is None:
        return False
    profile = profile if profile is not None else _profile(db, profile_id)
    if not admin_sets(profile) or _admin_off(db, profile_id) is not None:
        return False
    return is_locked(db, profile_id, profile) or _stored_off(db, profile_id) is None


def is_locked(db, profile_id: Optional[int], profile: Optional[Dict[str, Any]] = None) -> bool:
    """can this profile change its own choice? admins always can. a profile
    the admin never locked or unlocked by hand follows the default's lock."""
    if profile_id is None:
        return False
    profile = profile if profile is not None else _profile(db, profile_id)
    if not admin_sets(profile):
        return False
    try:
        raw = db.get_metadata(_lock_key(profile_id))
    except Exception as e:  # noqa: BLE001 - can't tell, keep the admin's word
        logger.debug("features: lock for %s unreadable: %s", profile_id, e)
        raw = None
    if raw is None:
        return default_for(db, profile)["locked"]
    return raw == "1"


def set_locked(db, profile_id: int, locked: bool) -> bool:
    want = "1" if locked else "0"
    db.set_metadata(_lock_key(profile_id), want)
    if db.get_metadata(_lock_key(profile_id)) != want:
        raise RuntimeError("couldn't save the lock")
    return bool(locked)


def _write_off(db, key: str, profile_id: int, off: Iterable[str]) -> List[str]:
    fixed = set(fixed_on(_profile(db, profile_id)))
    clean = [f for f in normalize_off(list(off)) if f not in fixed]
    db.set_metadata(key, json.dumps(clean))
    # set_metadata logs and swallows a failed write. read it back so the
    # page never says saved when it wasn't.
    if normalize_off(db.get_metadata(key)) != clean:
        raise RuntimeError("couldn't save which features are shown")
    return clean


def _drop(db, keys: Iterable[str]) -> None:
    for key in keys:
        db.delete_metadata(key)
        if db.get_metadata(key) is not None:
            raise RuntimeError("couldn't reset what this profile sees")


def save_off(db, profile_id: int, off: Iterable[str]) -> List[str]:
    """the member's own pick."""
    return _write_off(db, _key(profile_id), profile_id, off)


def save_admin_off(db, profile_id: int, off: Iterable[str]) -> List[str]:
    """an admin sets what this profile shows (#2164). the member's old pick
    goes, so what the admin set is what they see now, locked or not. an
    admin target has no admin layer, its pick is just its own."""
    if not admin_sets(_profile(db, profile_id)):
        return save_off(db, profile_id, off)
    clean = _write_off(db, _admin_key(profile_id), profile_id, off)
    _drop(db, [_key(profile_id)])
    return clean


def admin_lock(db, profile_id: int, locked: bool) -> bool:
    """the admin locks or unlocks this profile (#2164). the member's old pick
    goes either way: locked it must never win, and unlocking keeps showing
    what the admin set rather than bringing back what they hid before."""
    set_locked(db, profile_id, locked)
    _drop(db, [_key(profile_id)])
    return bool(locked)


def reset_own(db, profile_id: int) -> None:
    """the member drops their own pick: back to what the admin set for them,
    or the default. the admin's set and lock stay."""
    _drop(db, [_key(profile_id)])


def reset(db, profile_id: int) -> None:
    """back to the default: the profile's own choice, the admin's set and
    the lock all go, so it follows what new profiles get (and any later
    change to that)."""
    _drop(db, [_key(profile_id), _admin_key(profile_id), _lock_key(profile_id)])


def seed_from_purposes(db, profile_id: Optional[int], purposes: Iterable[str]) -> Optional[List[str]]:
    """first-run setup's answer, saved only when this profile never chose.
    running setup again (the dev preview, a reset) must not undo a choice
    someone made by hand afterwards, or one an admin locked."""
    if profile_id is None or db.get_metadata(_key(profile_id)) is not None:
        return None
    if is_locked(db, profile_id) or db.get_metadata(_admin_key(profile_id)) is not None:
        return None
    return save_off(db, profile_id, off_from_purposes(purposes))


def clear(db, profile_id: int) -> None:
    """a deleted profile takes its choice with it, so a new profile that gets
    the same id starts like any new profile."""
    reset(db, profile_id)


def has_chosen(db, profile_id: Optional[int], profile: Optional[Dict[str, Any]] = None) -> bool:
    """did this profile (or an admin, for it) ever choose? the dashboard
    offers the picker once to everyone who hasn't."""
    if profile_id is None:
        return True
    try:
        if db.get_metadata(_key(profile_id)) is not None:
            return True
        profile = profile if profile is not None else _profile(db, profile_id)
        if not admin_sets(profile):
            return False
        if db.get_metadata(_admin_key(profile_id)) is not None:
            return True
        return is_locked(db, profile_id, profile) or default_for(db, profile)["set"]
    except Exception:  # noqa: BLE001 - can't tell, don't nag
        return True


def profile_payload(db, profile_id: Optional[int]) -> Dict[str, Any]:
    """everything the picker needs for one profile."""
    profile = _profile(db, profile_id)
    off = load_off(db, profile_id, profile)
    return payload(
        off,
        has_chosen(db, profile_id, profile),
        locked=is_locked(db, profile_id, profile),
        follows=follows_default(db, profile_id, profile),
        offer=offered(profile) if profile_id is not None else None,
    )


def admin_payload(db, profile_id: int) -> Dict[str, Any]:
    """the profile payload plus what the admin's editor needs (#2164): the
    set the admin controls (what a lock shows) and the member's own pick,
    kept apart so the editor never passes one off as the other."""
    carry_over_2157(db)
    profile = _profile(db, profile_id)
    body = profile_payload(db, profile_id)
    body["set_off"] = admin_set_off(db, profile_id, profile)
    body["admin_set"] = admin_sets(profile) and _admin_off(db, profile_id) is not None
    body["own_off"] = own_off(db, profile_id, profile)
    # locked or unlocked by hand, not just following the default's lock
    try:
        body["lock_set"] = admin_sets(profile) and db.get_metadata(_lock_key(profile_id)) is not None
    except Exception:  # noqa: BLE001 - can't tell, offer the reset
        body["lock_set"] = True
    # what they follow when nobody set theirs: the template's Sidebar & look
    # (default.template names it, "From Teen"), else what new profiles see
    body["default"] = default_for(db, profile)
    return body


def default_payload(db) -> Dict[str, Any]:
    d = load_default(db)
    body = payload(d["off"], True, locked=d["locked"], follows=False, offer=list(FEATURE_IDS))
    body["set"] = d["set"]
    return body


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
    return feature in load_off(db, profile.get("id"), profile)


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


def payload(off: Iterable[str], chosen: bool = True, *, locked: bool = False,
            follows: bool = False, offer: Optional[List[str]] = None) -> Dict[str, Any]:
    clean = normalize_off(list(off))
    return {
        "chosen": bool(chosen),
        # an admin set it and locked it: show it, don't offer to change it
        "locked": bool(locked),
        # never chose, so it follows what an admin set for new profiles
        "follows_default": bool(follows),
        "offered": list(FEATURE_IDS) if offer is None else list(offer),
        "features": FEATURES,
        "groups": GROUPS,
        "presets": [{"id": k, **PRESET_LABELS[k], "off": v} for k, v in PRESETS.items()],
        "off": clean,
        "preset": preset_for(clean),
        "hidden_pages": hidden_pages(clean),
    }


__all__ = ["FEATURES", "FEATURE_IDS", "GROUPS", "PRESETS", "normalize_off", "preset_for",
           "hidden_pages", "off_from_purposes", "seed_from_purposes", "load_off", "save_off", "clear", "has_chosen",
           "admin_sets", "fixed_on", "offered", "load_default", "save_default", "follows_default",
           "template_default", "default_for",
           "is_locked", "set_locked", "reset", "profile_payload", "default_payload",
           "admin_set_off", "own_off", "carry_over_2157", "save_admin_off", "admin_lock", "reset_own", "admin_payload",
           "BACKGROUND_FEATURES", "feature_for_automation", "everyone_hid", "load_paused",
           "set_paused", "background_paused", "background_state",
           "payload"]
