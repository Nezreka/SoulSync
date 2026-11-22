"""The Discover adventurousness dial, per profile.

Each profile can turn its own dial; one that hasn't follows the global
default in ``discover.adventurousness`` (Settings > Discovery, admins
only). Every reader of the dial asks ``profile_level`` so one person's
dial never moves anyone else's Discover.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.adventurousness")

DEFAULT_LEVEL = 0.3


def clamp(value: Any) -> float:
    """A dial value in [0, 1]. Raises ValueError for a non-number."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be a number") from exc
    if number != number:   # NaN
        raise ValueError("value must be a number")
    return max(0.0, min(1.0, number))


def global_level(config_manager: Any) -> float:
    """The install-wide default from config."""
    try:
        raw = config_manager.get("discover.adventurousness", DEFAULT_LEVEL)
        return clamp(DEFAULT_LEVEL if raw is None else raw)
    except Exception:  # noqa: BLE001 - a bad stored value reads as the default
        return DEFAULT_LEVEL


def own_level(database: Any, profile_id: Optional[int]) -> Optional[float]:
    """The profile's own dial, or None when it follows the default."""
    if database is None or profile_id is None:
        return None
    try:
        value = database.get_profile_adventurousness(int(profile_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("own dial read failed for %s: %s", profile_id, exc)
        return None
    if value is None:
        return None
    try:
        return clamp(value)
    except ValueError:
        return None


def profile_level(database: Any, profile_id: Optional[int], config_manager: Any) -> float:
    """The dial this profile's Discover uses: its own, else the default."""
    own = own_level(database, profile_id)
    return own if own is not None else global_level(config_manager)


def set_own_level(database: Any, profile_id: int, value: Optional[float]) -> bool:
    """Store the profile's dial (clamped); None clears it to the default."""
    return bool(database.set_profile_adventurousness(
        int(profile_id), None if value is None else clamp(value)))
