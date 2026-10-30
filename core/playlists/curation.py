"""Decision semantics for optional mirrored-playlist curation.

Persistence lives in :mod:`database.music_database`; this module keeps the
precedence rule and API projection pure so sync, wishlist, download and UI
paths all answer eligibility the same way.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


CURATION_DECISIONS = frozenset({"keep", "pending", "reject"})


class CurationPolicyError(RuntimeError):
    """A persisted curation policy could not be read safely."""


def effective_decision(explicit: Any, globally_rejected: bool) -> str:
    """Resolve one source track's effective decision.

    Explicit Keep is the sole override for a profile/source rejection.
    Pending remains distinguishable from the absence of a decision row.
    """
    decision = str(explicit or "").strip().lower()
    if decision == "keep":
        return "keep"
    if decision in ("pending", "reject"):
        return decision
    return "reject" if globally_rejected else "unreviewed"


def decorate_tracks(
    tracks: Iterable[Mapping[str, Any]],
    *,
    enabled: bool,
    decisions: Mapping[str, str],
    global_rejections: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Add explicit/effective decision fields without changing track order."""
    rejected = {str(value) for value in global_rejections if value}
    counts: Counter[str] = Counter()
    decorated: list[dict[str, Any]] = []
    for original in tracks:
        track = dict(original)
        source_track_id = str(track.get("source_track_id") or "")
        explicit = decisions.get(source_track_id)
        effective = effective_decision(explicit, source_track_id in rejected)
        track["curation_decision"] = explicit
        track["globally_rejected"] = source_track_id in rejected
        track["effective_decision"] = effective
        track["curation_eligible"] = not enabled or effective == "keep"
        counts[effective] += 1
        decorated.append(track)
    return decorated, {
        "keep": counts["keep"],
        "pending": counts["pending"],
        "reject": counts["reject"],
        "unreviewed": counts["unreviewed"],
    }


__all__ = [
    "CURATION_DECISIONS",
    "CurationPolicyError",
    "decorate_tracks",
    "effective_decision",
]
