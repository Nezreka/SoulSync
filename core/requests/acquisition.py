"""how an approved request gets its release (#2199).

automatic (the default, and what every install had before): approving hands
the request to the usual acquisition, the wishlist pass grabs the best match.

manual: approving puts the request on hold for a release pick. nothing
automatic touches it (the wishlist passes, retries, scheduled searches, rss)
until an admin or moderator opens the picker from the Requests page and
takes one. the pick downloads through the normal path and the request
carries on from there like any other.

one setting per kind, in the config (the database). an approval can say
which way it goes for that one request, else the kind's setting decides.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

AUTO = "auto"
MANUAL = "manual"
MODES = (AUTO, MANUAL)

# the four kinds a release can be picked for. music videos, youtube and
# podcast episodes have one file each, nothing to pick between
KINDS = ("music", "movie", "tv", "audiobook")
KIND_WORDS = {"music": "Music", "movie": "Movies", "tv": "TV", "audiobook": "Audiobooks"}

CONFIG_KEY = "requests.acquisition"

# the state an approved, held request (and its wishlist rows) is in
AWAITING = "awaiting_selection"


def _config(config=None):
    if config is not None:
        return config
    from core.settings import config_manager
    return config_manager


def clean_mode(value: Any) -> Optional[str]:
    """'auto' | 'manual' | None for anything else."""
    text = str(value or "").strip().lower()
    return text if text in MODES else None


def modes(config=None) -> Dict[str, str]:
    """{kind: mode} for every kind, automatic where nothing is saved."""
    try:
        saved = _config(config).get(CONFIG_KEY, {}) or {}
    except Exception:  # noqa: BLE001 - an unreadable setting is the default
        saved = {}
    if not isinstance(saved, Mapping):
        saved = {}
    return {k: clean_mode(saved.get(k)) or AUTO for k in KINDS}


def mode_for(kind: str, config=None) -> str:
    return modes(config).get(kind, AUTO)


def set_modes(changes: Mapping[str, Any], config=None) -> Dict[str, str]:
    """save {kind: mode} for the kinds given. raises ValueError on an unknown
    kind or mode, before anything is written."""
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("nothing to change")
    clean = {}
    for kind, value in changes.items():
        if kind not in KINDS:
            raise ValueError("unknown kind %r" % kind)
        mode = clean_mode(value)
        if mode is None:
            raise ValueError("unknown mode %r for %s" % (value, kind))
        clean[kind] = mode
    cfg = _config(config)
    for kind, mode in clean.items():
        cfg.set("%s.%s" % (CONFIG_KEY, kind), mode)
    return modes(cfg)


def resolve(kind: Optional[str], override: Any = None, config=None) -> str:
    """the mode one approval runs in: the approver's choice when they made
    one, else the kind's setting. a kind with nothing to pick is automatic."""
    if kind not in KINDS:
        return AUTO
    return clean_mode(override) or mode_for(kind, config)


def video_kind(request_kind: Any) -> Optional[str]:
    """movie -> movie, show / episode -> tv, youtube (one file) -> None."""
    k = str(request_kind or "").lower()
    if k == "movie":
        return "movie"
    if k in ("show", "episode"):
        return "tv"
    return None


def override_from(body: Any) -> Optional[str]:
    """the approver's choice in a request body ({"acquisition": "manual"})."""
    if not isinstance(body, Mapping):
        return None
    return clean_mode(body.get("acquisition"))


__all__ = ["AUTO", "MANUAL", "MODES", "KINDS", "KIND_WORDS", "CONFIG_KEY", "AWAITING",
           "clean_mode", "modes", "mode_for", "set_modes", "resolve", "video_kind", "override_from"]
