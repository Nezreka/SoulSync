"""the drawer's settings, under config key ``server_activity``.

an install that already has a config row never gains new keys (no deep merge),
so every read here passes its own default.
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("server_activity")

DEFAULT_STOP_PRESETS: List[str] = [
    "The server is restarting for an update. Back in 5 minutes.",
    "Server maintenance, back soon.",
    "Too many streams right now, please try again later.",
    "Please watch at home tonight.",
]

DEFAULTS: Dict[str, Any] = {
    # remote upload speed in Mbps. None means no bar under remote bandwidth.
    "upload_mbps": None,
    # city / region for remote viewers through plex.tv's geoip lookup.
    "geo_lookup": True,
    # days the play log keeps. 0 keeps it forever.
    "retention_days": 0,
    "stop_presets": list(DEFAULT_STOP_PRESETS),
}

_MAX_PRESETS = 12
_MAX_PRESET_LEN = 240


def _cfg():
    from core.settings import config_manager
    return config_manager


def _clean_upload(v: Any):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("upload speed must be a number of Mbps") from None
    if f <= 0:
        return None
    if f > 100000:
        raise ValueError("upload speed is too large")
    return round(f, 2)


def _clean_retention(v: Any) -> int:
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise ValueError("retention must be a whole number of days") from None
    if n < 0:
        raise ValueError("retention can't be negative")
    return min(n, 36500)


def _clean_presets(v: Any) -> List[str]:
    if not isinstance(v, list):
        raise ValueError("stop presets must be a list of messages")
    out: List[str] = []
    for item in v:
        s = str(item or "").strip()[:_MAX_PRESET_LEN]
        if s and s not in out:
            out.append(s)
    return out[:_MAX_PRESETS]


def get_settings() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    out["stop_presets"] = list(DEFAULT_STOP_PRESETS)
    try:
        cm = _cfg()
        try:
            out["upload_mbps"] = _clean_upload(cm.get("server_activity.upload_mbps", None))
        except ValueError:
            out["upload_mbps"] = None
        out["geo_lookup"] = bool(cm.get("server_activity.geo_lookup", True))
        try:
            out["retention_days"] = _clean_retention(cm.get("server_activity.retention_days", 0))
        except ValueError:
            out["retention_days"] = 0
        presets = cm.get("server_activity.stop_presets", None)
        if isinstance(presets, list):
            out["stop_presets"] = _clean_presets(presets)
    except Exception:   # noqa: BLE001 - no config yet is a normal state
        logger.debug("server activity settings read failed", exc_info=True)
    return out


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """validate and store the keys in ``patch``. raises ValueError with a plain
    message on a bad value, nothing is written then."""
    if not isinstance(patch, dict):
        raise ValueError("settings must be an object")
    clean: Dict[str, Any] = {}
    for key, value in patch.items():
        if key == "upload_mbps":
            clean[key] = _clean_upload(value)
        elif key == "geo_lookup":
            clean[key] = bool(value)
        elif key == "retention_days":
            clean[key] = _clean_retention(value)
        elif key == "stop_presets":
            clean[key] = _clean_presets(value)
        else:
            raise ValueError(f"unknown setting {key!r}")
    cm = _cfg()
    for key, value in clean.items():
        cm.set(f"server_activity.{key}", value)
    return get_settings()
