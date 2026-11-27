"""a profile's player settings, kept in player.db so every screen of the
profile has them. only settings the player really acts on live here; a
screen's own taste (subtitle size, brightness, quality) stays in that
browser.

each setting has a default and a cleaner. an unknown key is refused, a
bad value is refused, a missing one reads as its default.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, Tuple

TABLE = "player_settings"

_ready: set = set()
_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
_lock = threading.Lock()


class SettingsError(ValueError):
    pass


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    raise SettingsError("expected true or false")


def _int(lo: int, hi: int) -> Callable[[Any], int]:
    def clean(v: Any) -> int:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SettingsError(f"expected a number from {lo} to {hi}")
        n = int(v)
        if not lo <= n <= hi:
            raise SettingsError(f"expected a number from {lo} to {hi}")
        return n
    return clean


ACCENT_COLORS = ("emerald", "violet", "ocean", "amber", "rose", "crimson")
COMMERCIAL_ERAS = ("auto", "70s", "80s", "90s", "2000s", "kids", "mix")
COMMERCIAL_MODES = ("short", "cable")


def _accent(v: Any) -> str:
    if isinstance(v, str) and v.strip().lower() in ACCENT_COLORS:
        return v.strip().lower()
    raise SettingsError(f"expected one of {', '.join(ACCENT_COLORS)}")


def _commercial_era(v: Any) -> str:
    if isinstance(v, str) and v.strip().lower() in COMMERCIAL_ERAS:
        return v.strip().lower()
    raise SettingsError(f"expected one of {', '.join(COMMERCIAL_ERAS)}")


def _commercial_mode(v: Any) -> str:
    if isinstance(v, str) and v.strip().lower() in COMMERCIAL_MODES:
        return v.strip().lower()
    raise SettingsError(f"expected one of {', '.join(COMMERCIAL_MODES)}")


LOUDNESS_MODES = ("off", "track", "album", "smart")


def _loudness_mode(v: Any) -> str:
    if isinstance(v, str) and v.strip().lower() in LOUDNESS_MODES:
        return v.strip().lower()
    raise SettingsError(f"expected one of {', '.join(LOUDNESS_MODES)}")


def _db(lo: float, hi: float) -> Callable[[Any], float]:
    """a level in dB, to the half dB"""
    def clean(v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            raise SettingsError(f"expected a number from {lo:g} to {hi:g}")
        n = round(float(v) * 2) / 2
        if not lo <= n <= hi:
            raise SettingsError(f"expected a number from {lo:g} to {hi:g}")
        return n
    return clean


# a place in the player's sidebar: one of its pages ("/player/movies") or a
# library with a page of its own ("lib:5")
_NAV_ID = re.compile(r"^(?:/player(?:/[a-z][a-z-]{0,30}){0,3}|lib:\d{1,9})$")
MAX_NAV_IDS = 80


def _nav_ids(v: Any) -> list:
    """a list of sidebar places, each once. the player knows its places and
    skips one it doesn't have (a library that was removed), so only the
    shape is checked here."""
    if not isinstance(v, list) or len(v) > MAX_NAV_IDS:
        raise SettingsError(f"expected a list of at most {MAX_NAV_IDS} sidebar places")
    out, seen = [], set()
    for item in v:
        if not isinstance(item, str) or not _NAV_ID.match(item):
            raise SettingsError("expected sidebar places like /player/movies or lib:5")
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _nav_hidden(v: Any) -> list:
    out = _nav_ids(v)
    if "/player" in out:
        raise SettingsError("Home can't be hidden")
    return out


# key: (default, cleaner, what it does)
SETTINGS: Dict[str, Tuple[Any, Callable[[Any], Any], str]] = {
    "share_activity": (True, _bool,
                       "others on this server see what you're watching or listening to, and can join"),
    "autoplay_next": (True, _bool, "the next episode plays on its own"),
    "still_watching_after": (3, _int(0, 20),
                             "episodes in a row nobody touched before asking (0: never ask)"),
    "show_movie_ratings": (False, _bool,
                           "show the star rating badge in the corner of movie poster cards"),
    "show_tv_episode_count": (True, _bool,
                              "show the episode count badge on TV show poster cards"),
    "show_year_on_cards": (True, _bool,
                           "show the release year beneath movie and TV poster cards"),
    "accent_color": ("emerald", _accent,
                     "profile accent color for the player across all devices"),
    "commercials_enabled": (False, _bool,
                            "play optional retro commercial breaks between Live Guide programs"),
    "commercials_era": ("auto", _commercial_era,
                        "decade or vibe for retro commercials (auto matches each channel)"),
    "commercials_mode": ("cable", _commercial_mode,
                         "classic cable continuous breaks or short 1-2 spot breaks"),
    "loudness_mode": ("smart", _loudness_mode,
                      "volume levelling for music: off, track, album, or smart (album gain while an "
                      "album plays in order, track gain otherwise)"),
    "loudness_target": (-18.0, _db(-24, -10),
                        "the loudness levelled music plays at, in LUFS (-18 is the ReplayGain standard)"),
    "loudness_preamp": (0.0, _db(-12, 12),
                        "extra gain on top of levelling, in dB"),
    "loudness_prevent_clipping": (True, _bool,
                                  "never turn a song up past where its loudest moment would distort"),
    "video_loudness": (False, _bool,
                       "level very quiet or very loud videos to the same loudness"),
    "sidebar_order": ([], _nav_ids,
                      "the order of the places in the sidebar; any not listed keep their usual place"),
    "sidebar_hidden": ([], _nav_hidden,
                       "places left out of the sidebar (they still open from a link or search)"),
}


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                profile_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (profile_id, key)
            )""")
        conn.commit()
    _ready.add(key)


def _own(value: Any) -> Any:
    """a setting's value the caller may change: a list setting is copied, so
    one profile's (or the default's) list is never another's"""
    return list(value) if isinstance(value, list) else value


def defaults() -> Dict[str, Any]:
    return {k: _own(v[0]) for k, v in SETTINGS.items()}


def get(db: Any, profile_id: int) -> Dict[str, Any]:
    """every setting for the profile: what it saved, else the default"""
    ck = (str(getattr(db, "database_path", id(db))), int(profile_id))
    with _lock:
        hit = _cache.get(ck)
    if hit is not None:
        return {k: _own(v) for k, v in hit.items()}
    ensure_schema(db)
    out = defaults()
    with db._get_connection() as conn:
        for k, raw in conn.execute(f"SELECT key, value_json FROM {TABLE} WHERE profile_id = ?",
                                   (int(profile_id),)):
            if k not in SETTINGS:
                continue
            try:
                out[k] = SETTINGS[k][1](json.loads(raw))
            except (ValueError, TypeError):
                continue  # a value an older version wrote: the default stands
    with _lock:
        _cache[ck] = {k: _own(v) for k, v in out.items()}
    return out


def put(db: Any, profile_id: int, changes: Any) -> Dict[str, Any]:
    """save some settings (all or nothing); returns them all"""
    if not isinstance(changes, dict) or not changes:
        raise SettingsError("nothing to change")
    clean: Dict[str, Any] = {}
    for k, v in changes.items():
        if k not in SETTINGS:
            raise SettingsError(f"no setting called {k}")
        try:
            clean[k] = SETTINGS[k][1](v)
        except SettingsError as e:
            raise SettingsError(f"{k}: {e}") from None
    ensure_schema(db)
    now = time.time()
    with db._get_connection() as conn:
        for k, v in clean.items():
            conn.execute(
                f"INSERT INTO {TABLE} (profile_id, key, value_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(profile_id, key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (int(profile_id), k, json.dumps(v), now))
        conn.commit()
    with _lock:
        _cache.pop((str(getattr(db, "database_path", id(db))), int(profile_id)), None)
    return get(db, profile_id)


def forget(profile_id: int) -> None:
    """a deleted profile: drop what's cached for it"""
    with _lock:
        for k in [k for k in _cache if k[1] == int(profile_id)]:
            _cache.pop(k, None)
