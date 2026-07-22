"""Canonical Library-v2 cutover contract.

Library v2 now owns the catalogue and native repair suite.  Keeping the old
``features.library_v2`` switch operational would make every native repair job
report a clean, empty scope when disabled, so the cutover is intentionally no
longer reversible through configuration.  The legacy key is still read only
to emit one migration warning; its value never disables the catalogue.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from utils.logging_config import get_logger


logger = get_logger("library2.feature")
_warned_deprecated_disable = False


def coerce_bool(value: Any, default: bool = True) -> bool:
    """Normalize common persisted/env boolean shapes in one place."""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def library_v2_enabled(
    config_manager: Any = None,
    *,
    config_get: Optional[Callable[..., Any]] = None,
) -> bool:
    """Return the non-disableable native-catalogue cutover state.

    ``config_manager`` and ``config_get`` are accepted so every former inline
    gate can share this migration boundary without changing its caller shape.
    """
    global _warned_deprecated_disable
    getter = config_get
    if getter is None and config_manager is not None:
        getter = getattr(config_manager, "get", None)
    if getter is not None and not _warned_deprecated_disable:
        try:
            configured = getter("features.library_v2", True)
            if not coerce_bool(configured, True):
                logger.warning(
                    "features.library_v2=false is deprecated and ignored: "
                    "Library v2 is the native catalogue and repair engine"
                )
                _warned_deprecated_disable = True
        except Exception as exc:  # noqa: BLE001 - config read cannot disable catalogue
            logger.debug("Library v2 legacy feature-key read skipped: %s", exc)
    return True


__all__ = ["coerce_bool", "library_v2_enabled"]
