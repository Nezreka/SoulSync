"""Tell YouTube's failures apart, because they are not the same failure.

The drain gives a video three attempts and then stops re-queueing it forever. That
budget is right for a download that *might* work next time and wrong for the two
cases either side of it, and Boulder's live database has one of each:

* A video that had not been released yet — *"Premieres in 3 days"* — burned all
  three attempts inside two hours on the day it was queued and is now permanently
  skipped. It premiered days ago. Nothing will ever fetch it. Counting "not out
  yet" as "broken" is the bug.
* Four members-only videos each took three attempts over several hours to learn
  something the very first attempt already knew. Paywalled is paywalled.

And the case in the middle, which is the one that actually matters right now:
five ``HTTP Error 403: Forbidden`` failures in two days, all different channels.
That is the signature of YouTube's bot-detection moving on while yt-dlp stays
still. It is genuinely retryable — but only after yt-dlp is updated, so the
message has to SAY that instead of leaving five identical mysteries.

Classification reads the stored error text, which means it applies to history
already on disk: the premiere above un-sticks itself the next time the drain runs,
with no migration.

Pure: no DB, no network, no yt-dlp import.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Never coming back on its own — one attempt is enough to know.
GONE = "gone"
# Not released yet. Not a failure at all; the video simply does not exist yet.
NOT_YET = "not_yet"
# YouTube refused us specifically. Retryable, but usually needs yt-dlp updating.
BLOCKED = "blocked"
# Network hiccup, disk, timeout — the ordinary case the three-strike budget is for.
TRANSIENT = "transient"

# yt-dlp colours its output; the raw string lands in the DB with the escapes intact.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_GONE_PATTERNS = (
    "available to this channel's members",
    "join this channel",
    "members-only",
    "members only",
    "private video",
    "video unavailable",
    "has been removed",
    "removed by the uploader",
    "account associated with this video has been terminated",
    "violating youtube's terms of service",
    "no longer available",
    "not available in your country",
    "blocked it on copyright grounds",
    "who has blocked it",
)

_NOT_YET_PATTERNS = (
    "premieres in",
    "premiere will begin",
    "this live event will begin in",
    "live event will begin",
    "begins in",
    "is upcoming",
    "upcoming video",
    "scheduled for",
)

_BLOCKED_PATTERNS = (
    "http error 403",
    "403: forbidden",
    "sign in to confirm you're not a bot",
    "sign in to confirm your age",
    "confirm you're not a bot",
    "please sign in",
    "unable to download video data",
    "requested format is not available",
    "nsig extraction failed",
    "unable to extract",
)


def _clean(error: Any) -> str:
    return _ANSI.sub("", str(error or "")).strip().lower()


def classify(error: Any) -> str:
    """Which kind of failure this is. Order matters: a members-only video also
    says 'unable to download', so the permanent read has to win."""
    low = _clean(error)
    if not low:
        return TRANSIENT
    if any(p in low for p in _GONE_PATTERNS):
        return GONE
    if any(p in low for p in _NOT_YET_PATTERNS):
        return NOT_YET
    if any(p in low for p in _BLOCKED_PATTERNS):
        return BLOCKED
    return TRANSIENT


def human_reason(error: Any) -> Optional[str]:
    """What to show the user instead of a raw yt-dlp traceback, or None when we
    have nothing better to say than the original. The 403 message names yt-dlp on
    purpose — five identical 'Forbidden' rows tell you nothing actionable."""
    low = _clean(error)
    kind = classify(error)
    if kind == GONE:
        if "member" in low or "join this channel" in low:
            return "Members-only — this needs a paid membership to that channel."
        if "private" in low:
            return "Private on YouTube."
        if "terminated" in low or "terms of service" in low:
            return "The channel or video was taken down by YouTube."
        if "country" in low:
            return "Not available in your region."
        if "copyright" in low:
            return "Blocked on copyright grounds."
        return "Gone from YouTube — removed, private or deleted."
    if kind == NOT_YET:
        return "Hasn't been released yet — SoulSync will keep checking."
    if kind == BLOCKED:
        return ("YouTube refused the download. This is almost always an out-of-date "
                "yt-dlp — update it with: pip install -U yt-dlp")
    return None


def failure_weight(error: Any, *, max_fail: int) -> int:
    """How much this failure counts against the give-up budget.

    ``GONE`` spends the whole budget at once — retrying a paywalled video hourly
    learns nothing. ``NOT_YET`` spends none of it, because the video not existing
    yet is not a failure to download it."""
    kind = classify(error)
    if kind == GONE:
        return max(1, int(max_fail))
    if kind == NOT_YET:
        return 0
    return 1


def strikes_for(rows: Iterable[Any], *, max_fail: int, not_yet_grace_days: int = 30) -> int:
    """Total strikes for one video's failure history.

    ``rows`` = [{"error", "days_ago"}]. The grace period is what stops a NOT_YET
    from retrying forever: a premiere resolves in days, so anything still saying
    "not released yet" a month later was cancelled or mis-detected and should stop
    costing hourly searches."""
    total = 0
    for r in rows or []:
        if isinstance(r, dict):
            err, age = r.get("error"), r.get("days_ago")
        else:
            err, age = r, None
        w = failure_weight(err, max_fail=max_fail)
        if w == 0:
            try:
                if age is not None and float(age) > float(not_yet_grace_days):
                    w = 1      # waited long enough; it isn't coming
            except (TypeError, ValueError):
                pass
        total += w
    return total


def looks_like_stale_ytdlp(errors: Iterable[Any], *, threshold: int = 3) -> bool:
    """Whether a batch of failures reads as 'yt-dlp needs updating' rather than
    a scatter of unrelated problems. Used to say it once, loudly, instead of once
    per video."""
    n = sum(1 for e in (errors or []) if classify(e) == BLOCKED)
    return n >= max(1, int(threshold))


__all__ = ["classify", "human_reason", "failure_weight", "strikes_for",
           "looks_like_stale_ytdlp", "GONE", "NOT_YET", "BLOCKED", "TRANSIENT"]
