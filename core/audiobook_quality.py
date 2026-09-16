"""What "good" means for an audiobook, in one place the user can change.

The ranker had these preferences baked in: m4b beats mp3, a bitrate is a mild
tiebreak, a dramatisation is merely demoted. Those are reasonable defaults and
they are not everyone's. Somebody who plays books on a device that cannot read
m4b wants mp3 first; somebody who collects GraphicAudio wants dramatisations
allowed; somebody on a small disk wants a ceiling.

ONE profile for the whole side, deliberately, not one per followed author. A
listener's idea of an acceptable file does not change between authors, and the
per-author card already carries the two settings that genuinely differ there
(whether to auto-queue, and which narrator).

Distinct from ``min_complete_kbps``, which asks a different question: that one
is "could this possibly be the whole book", a fact about the release. This is
"do I want it", a matter of taste. A release can pass one and fail the other.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_quality")

# m4b first because it is the only one built for the job: chapters, bookmarks,
# and one file rather than ninety. flac last because speech gains nothing from
# it and pays several times the size.
DEFAULT_FORMAT_ORDER: List[str] = ["m4b", "m4a", "mp3", "opus", "ogg", "flac"]

# The spread between the best and worst format, shared out over the order. Kept
# well under the relevance weight: a beautifully formatted wrong book is still
# the wrong book.
_FORMAT_SPREAD = 24.0

DEFAULTS: Dict[str, Any] = {
    "format_order": DEFAULT_FORMAT_ORDER,
    # 0 means "no opinion". A floor here rejects; it is not a tiebreak.
    "min_bitrate_kbps": 0,
    "max_bitrate_kbps": 0,
    # GraphicAudio and the like. On by default because they are still shown
    # today, merely outranked — turning this off is what removes them.
    "allow_dramatized": True,
}


def profile() -> Dict[str, Any]:
    """The audiobook quality profile, with defaults that survive an old config.

    Every read carries its own default: there is no deep merge of new defaults
    into an existing config row, so a key added after an install was set up
    reads as None and would silently mean "no formats are any good".
    """
    values = dict(DEFAULTS)
    values["format_order"] = list(DEFAULT_FORMAT_ORDER)
    try:
        from core.settings import config_manager
        for key, fallback in DEFAULTS.items():
            got = config_manager.get(f"audiobooks.quality.{key}", fallback)
            if got is not None:
                values[key] = got
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the audiobook quality profile: %s", exc)

    order = [str(f).strip().lower() for f in (values.get("format_order") or []) if str(f).strip()]
    values["format_order"] = order or list(DEFAULT_FORMAT_ORDER)
    for key in ("min_bitrate_kbps", "max_bitrate_kbps"):
        try:
            values[key] = max(0, int(values.get(key) or 0))
        except (TypeError, ValueError):
            values[key] = 0
    values["allow_dramatized"] = bool(values.get("allow_dramatized", True))
    return values


def format_scores(prof: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Score per audio format, best first, from the configured order.

    Built from the order rather than stored as numbers so the setting stays
    something a person can reason about: a list of formats, best first.
    """
    prof = prof or profile()
    order = prof["format_order"]
    if len(order) == 1:
        return {order[0]: _FORMAT_SPREAD}
    step = _FORMAT_SPREAD / max(1, len(order) - 1)
    return {fmt: round(_FORMAT_SPREAD - index * step, 2) for index, fmt in enumerate(order)}


def rejection(release: Any, prof: Optional[Dict[str, Any]] = None) -> str:
    """Why the profile refuses this release, or "" when it does not.

    Only refuses on things the user actually asked to refuse. Taste is not
    allowed to reject silently: every reason here is one they set.
    """
    prof = prof or profile()

    if not prof["allow_dramatized"] and getattr(release, "dramatized", False):
        return "Dramatised adaptations are turned off in your audiobook quality profile."

    implied = getattr(release, "implied_kbps", None)
    floor = prof["min_bitrate_kbps"]
    ceiling = prof["max_bitrate_kbps"]

    if implied:
        if floor and implied < floor:
            return f"~{implied} kbps is below the {floor} kbps minimum you set."
        if ceiling and implied > ceiling:
            return f"~{implied} kbps is above the {ceiling} kbps maximum you set."
    return ""
