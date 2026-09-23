"""The precondition on deleting a wishlist row (#1289).

A wishlist row is the only durable record that a user asked for a track. There
is no tombstone, no pending state and no audit trail behind it: ``success=True``
runs a ``DELETE`` and the request is gone. So the question "is this download
actually finished?" has to be answered before the delete, not assumed by it.

Three separate code paths assumed it, and each one lost tracks:

  * Atomic album publishing (#999) redirects a finished track to a path under
    ``.soulsync_atomic_staging``, which every scanner and media server skips by
    design. The per-track completion callback deleted the wishlist row there --
    minutes to hours before the album actually published, and unconditionally,
    so a restart, a failed publish or a cancel in that window left the user with
    neither a library file nor a request to retry.
  * The verification worker's "no matched context" branch reports success for a
    file that is still sitting in the downloads folder, never imported.
  * The completion callback in ``lifecycle`` builds its context out of
    ``track_info`` alone, so it had no path to check even in principle.

The guard below is the one place that decides, and it decides from the file:
a wishlist row may be deleted for a completed download only when the caller can
name a file that exists and is not staged. Everything else keeps the row, which
is the self-healing direction -- a track that is already in the library and
still on the wishlist gets cleared by the already-owned cleanup, whereas a track
that is on neither is gone for good.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from core.downloads.atomic_album_publish import contains_staging_segment, is_staged_path
from utils.logging_config import get_logger

logger = get_logger("wishlist.removal_guard")

PUBLISHED = "published"
STAGED = "staged"
MISSING = "missing"
UNKNOWN = "unknown"

# Removal reasons. Only DOWNLOAD_COMPLETE claims "the download put this in the
# library"; the others are user or library decisions that own their own proof
# and are not gated here.
REASON_DOWNLOAD_COMPLETE = "download_complete"
REASON_ATOMIC_PUBLISHED = "atomic_published"
REASON_ALREADY_OWNED = "already_owned"
REASON_MANUAL_MATCH = "manual_match"
REASON_USER = "user_action"

_GATED_REASONS = {REASON_DOWNLOAD_COMPLETE, REASON_ATOMIC_PUBLISHED}


def classify_publication(published_path: Optional[str],
                         transfer_dir: Optional[str] = None) -> str:
    """Where a completed download's file actually is.

    ``STAGED`` is decided by the path, not by batch state, and on purpose: the
    batch dict is exactly the thing that does not survive the failures this
    guard exists for. A path with a ``.soulsync_atomic_staging`` component is
    staged no matter who is asking or what they remember.
    """
    if not published_path:
        return UNKNOWN
    path = str(published_path)
    if contains_staging_segment(path):
        return STAGED
    if transfer_dir and is_staged_path(path, transfer_dir):
        return STAGED
    try:
        if not os.path.exists(path):
            return MISSING
    except OSError:
        return UNKNOWN
    return PUBLISHED


def may_remove(published_path: Optional[str], *,
               reason: str = REASON_DOWNLOAD_COMPLETE,
               transfer_dir: Optional[str] = None) -> Tuple[bool, str]:
    """``(allowed, state)`` for one removal decision.

    Ungated reasons pass straight through -- a user deleting their own wishlist
    entry, or the already-owned cleanup which proves ownership its own way --
    so this never becomes a bottleneck on paths it knows nothing about.
    """
    if reason not in _GATED_REASONS:
        return True, reason
    state = classify_publication(published_path, transfer_dir=transfer_dir)
    return state == PUBLISHED, state


def explain_refusal(state: str) -> str:
    """Why a refused removal was refused, in words a user can act on."""
    return {
        STAGED: ("the file is still in atomic-publish staging and is not visible to "
                 "the media server yet — the wishlist entry is kept until the album publishes"),
        MISSING: ("the recorded file does not exist on disk — the wishlist entry is kept "
                  "so the track can be retried"),
        UNKNOWN: ("the download reported success without naming an imported file — the "
                  "wishlist entry is kept so the track can be retried"),
    }.get(state, f"publication state {state!r}")


__all__ = [
    "MISSING",
    "PUBLISHED",
    "REASON_ALREADY_OWNED",
    "REASON_ATOMIC_PUBLISHED",
    "REASON_DOWNLOAD_COMPLETE",
    "REASON_MANUAL_MATCH",
    "REASON_USER",
    "STAGED",
    "UNKNOWN",
    "classify_publication",
    "explain_refusal",
    "may_remove",
]
