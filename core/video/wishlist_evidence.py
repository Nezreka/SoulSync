"""Why a wishlist row is stuck — kept, instead of thrown away every hour.

A wanted item searches on the hourly drain, gets candidates, judges each against
the quality profile, and takes the best acceptable one. When nothing is
acceptable it increments ``search_attempts`` and discards everything else. So a
row that has searched forty times looks exactly like one waiting for next week's
episode, and the user cannot tell "be patient" from "this will never work".

Three rows on the live install sit at thirteen fruitless searches (Aussie Shore
S02E03/E09/E10) with nothing on screen to explain any of them.

Everything needed is already in hand at the moment of refusal: each candidate
carries its ``quality_label`` and a ``rejected`` string saying which rule turned
it down. This module turns that into one line worth storing.

**The distinction that makes it useful.** Refusals split into two families, and
only one is about availability:

* *identity* — "Wrong season", "Wrong year", "This is a TV release, not the
  movie". The release is not this item. Reporting it as "best available" would
  be a lie; these are search noise.
* *availability* — "1080p WEB isn't in your enabled tiers", "No seeders",
  "Over your 20 GB size cap", "Uploader blocklisted". The release IS this item
  and a rule of yours refused it. That is the actionable half, and it is what
  turns "why is this stuck" into a decision.

Pure: no DB, no network, no clock.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

IDENTITY = "identity"          # not this item at all — says nothing about availability
QUALITY = "quality"            # exists, but outside the profile's tiers/codecs/HDR
SWARM = "swarm"                # exists, but nobody is sharing it
SIZE = "size"                  # exists, but over the size cap
BLOCKED = "blocked"            # exists, but you blocked it
OTHER = "other"

# Matched against the reason strings core.video.quality_eval actually produces.
_IDENTITY = ("wrong title", "wrong year", "wrong season", "wrong episode",
             "not a single episode", "is a tv release, not the movie",
             "release is s", "not the episode requested")
_SWARM = ("no seeders", "seeder(s)")
_SIZE = ("size cap",)
_BLOCKED = ("blocklisted",)
_QUALITY = ("enabled tiers", "reject list", "unsupported quality", "hdr required",
            "format score")


def classify_refusal(reason: Any) -> str:
    """Which family a refusal belongs to. Unknown reasons are OTHER rather than
    IDENTITY — treating something we don't recognise as "not this item" would
    silently hide a real availability problem."""
    low = str(reason or "").strip().lower()
    if not low:
        return OTHER
    if any(p in low for p in _IDENTITY):
        return IDENTITY
    if any(p in low for p in _BLOCKED):
        return BLOCKED
    if any(p in low for p in _SWARM):
        return SWARM
    if any(p in low for p in _SIZE):
        return SIZE
    if any(p in low for p in _QUALITY):
        return QUALITY
    return OTHER


def _rank(label: Any) -> int:
    """Order quality labels so 'best seen' means best. Falls back to the digits in
    the label so an unrecognised '1440p WEB' still sorts above 720p."""
    try:
        from core.video.quality_eval import resolution_rank
        r = resolution_rank(label)
        if r:
            return int(r) * 100
    except Exception:      # noqa: BLE001 - ranking is an assist
        pass
    m = re.search(r"(\d{3,4})p", str(label or ""))
    return int(m.group(1)) if m else 0


def summarize_refusals(candidates: Iterable[Any]) -> Optional[Dict[str, Any]]:
    """The receipt for one fruitless search, or None when there is nothing to say.

    Returns ``{"quality_label", "reason", "kind", "seen"}`` describing the BEST
    release that was refused for a reason about availability — best because that
    is the one the user would actually accept if they relaxed something, and
    ``seen`` because "twelve of them" is the difference between bad luck and a
    setting that will never be satisfied.

    Identity-only refusals return None: a hundred hits for the wrong season mean
    the search found nothing, and saying "best seen: 1080p" about another show's
    episode would be worse than saying nothing."""
    best, seen = None, 0
    for c in candidates or []:
        if not isinstance(c, dict) or c.get("accepted"):
            continue
        # No stated reason is nothing to report. Counting it would produce a
        # receipt with no explanation on it — worse than staying quiet, because
        # the row would claim evidence it does not have.
        if not str(c.get("rejected") or "").strip():
            continue
        kind = classify_refusal(c.get("rejected"))
        if kind == IDENTITY:
            continue
        seen += 1
        if best is None or _rank(c.get("quality_label")) > _rank(best.get("quality_label")):
            best = c
    if best is None:
        return None
    return {"quality_label": str(best.get("quality_label") or "").strip() or None,
            "reason": str(best.get("rejected") or "").strip() or None,
            "kind": classify_refusal(best.get("rejected")),
            "seen": seen}


def refusal_line(summary: Any) -> Optional[str]:
    """One sentence for the wishlist row. Names the tier AND the rule, because
    either alone leaves the user guessing: "720p" doesn't say why it was refused,
    and "isn't in your enabled tiers" doesn't say what was on offer."""
    if not isinstance(summary, dict):
        return None
    label, reason = summary.get("quality_label"), summary.get("reason")
    if not reason:
        return None
    seen = summary.get("seen") or 0
    times = "" if seen <= 1 else " (%d releases)" % seen
    if label:
        return "Best found: %s — %s%s" % (label, reason, times)
    return "%s%s" % (reason, times)


def is_actionable(summary: Any) -> bool:
    """Whether the user could plausibly do something about it. A quality, size or
    blocklist refusal is a setting they own; a dead swarm is not."""
    return isinstance(summary, dict) and summary.get("kind") in (QUALITY, SIZE, BLOCKED)


__all__ = ["classify_refusal", "summarize_refusals", "refusal_line", "is_actionable",
           "IDENTITY", "QUALITY", "SWARM", "SIZE", "BLOCKED", "OTHER"]
