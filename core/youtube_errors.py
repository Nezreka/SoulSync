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
# Needs a signed-in adult account. Retryable, but only once cookies exist.
AGE_GATED = "age_gated"
# YouTube wants to see a logged-in browser session. Same shape, different fix.
COOKIES = "cookies"
# We are being rate-limited. Says nothing about the video.
THROTTLED = "throttled"
# The download itself worked; merging/remuxing it did not (usually ffmpeg).
POSTPROCESS = "postprocess"
# Out of disk. Ours to fix, and nothing to do with this video.
DISK = "disk"
# Network hiccup, timeout — the ordinary case the three-strike budget is for.
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

# Ordered before BLOCKED because yt-dlp says "sign in to confirm your age" for
# age gates and "sign in to confirm you're not a bot" for cookie problems, and the
# generic 403 read would swallow both into one useless "update yt-dlp" message.
_AGE_GATED_PATTERNS = (
    "sign in to confirm your age",
    "confirm your age",
    "age-restricted",
    "age restricted",
    "inappropriate for some users",
)

# Deliberately NARROW. The bot gate — "Sign in to confirm you're not a bot. Use
# --cookies-from-browser ..." — reads like a cookie problem and is not one: #1126
# was a datacenter-IP block where the reporter re-exported cookies twice and it
# changed nothing. That message stays BLOCKED, whose advice (update yt-dlp) is the
# lever that actually moves. Only unambiguous "your cookies are the problem"
# signals belong here.
_COOKIES_PATTERNS = (
    "cookies are no longer valid",
    "invalid cookies",
    "could not copy cookie",
    "cookie file",
    "login required",
    "account authentication is required",
)

_THROTTLED_PATTERNS = (
    "http error 429",
    "429: too many requests",
    "too many requests",
    "rate limit",
    "rate-limit",
    "temporarily blocked",
    "try again later",
)

# The bytes arrived. Something after that failed — almost always a missing or
# broken ffmpeg, which is a local install problem, not a bad video.
_POSTPROCESS_PATTERNS = (
    "postprocessing:",
    "ffmpeg not found",
    "ffprobe and ffmpeg not found",
    "you have requested merging",
    "error opening output files",
    "conversion failed",
    "unable to rename file",
    "audio conversion failed",
)

_DISK_PATTERNS = (
    "no space left on device",
    "errno 28",
    "disk quota exceeded",
    "insufficient disk space",
    "not enough space",
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
    # Our-side problems next: a full disk reported as "the video is blocked"
    # sends the user to fix YouTube when the fix is on their own machine.
    if any(p in low for p in _DISK_PATTERNS):
        return DISK
    if any(p in low for p in _POSTPROCESS_PATTERNS):
        return POSTPROCESS
    if any(p in low for p in _THROTTLED_PATTERNS):
        return THROTTLED
    # An age gate before the generic 403 read: yt-dlp phrases it as a sign-in
    # prompt, and "update yt-dlp" does nothing for it.
    if any(p in low for p in _AGE_GATED_PATTERNS):
        return AGE_GATED
    if any(p in low for p in _COOKIES_PATTERNS):
        return COOKIES
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
    if kind == AGE_GATED:
        return ("Age-restricted — YouTube wants a signed-in adult account. Add browser "
                "cookies in Settings to fetch this one.")
    if kind == COOKIES:
        return ("YouTube asked us to prove we're not a bot. Add browser cookies in "
                "Settings; updating yt-dlp alone won't clear this.")
    if kind == THROTTLED:
        return "YouTube is rate-limiting us. SoulSync will back off and try later."
    if kind == POSTPROCESS:
        return ("The video downloaded but couldn't be assembled — this is usually a "
                "missing or broken ffmpeg on the server, not a problem with the video.")
    if kind == DISK:
        return "Out of disk space. Free some room and this will go straight through."
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
    # Zero-weight failures say nothing about the video, so they must not spend its
    # give-up budget. A full disk or a rate-limit window would otherwise blacklist
    # everything queued while it lasted — permanently, long after the disk was
    # cleared. strikes_for still counts them once they are older than the grace
    # period, so a row that is STILL saying this a month later stops costing searches.
    if kind in (NOT_YET, DISK, THROTTLED):
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


# The kinds nothing will fix on its own: retrying changes nothing until a person
# adds cookies, installs ffmpeg or frees disk. The UI uses this to separate "still
# trying" from "waiting on you".
_NEEDS_USER = frozenset({AGE_GATED, COOKIES, POSTPROCESS, DISK})


def needs_user_action(error: Any) -> bool:
    """Whether this failure is waiting on the operator rather than on time."""
    return classify(error) in _NEEDS_USER


def looks_like_stale_ytdlp(errors: Iterable[Any], *, threshold: int = 3) -> bool:
    """Whether a batch of failures reads as 'yt-dlp needs updating' rather than
    a scatter of unrelated problems. Used to say it once, loudly, instead of once
    per video.

    Age gates and cookie prompts are deliberately NOT counted here any more: they
    also produce a sign-in message, but updating yt-dlp fixes neither, and sending
    the user to do that is how a cookie problem stays a cookie problem. Those get
    their own cluster read below."""
    n = sum(1 for e in (errors or []) if classify(e) == BLOCKED)
    return n >= max(1, int(threshold))


def dominant_failure(errors: Iterable[Any]) -> Optional[str]:
    """The kind most worth naming out of a batch, or None for an empty batch.

    'Most worth naming' is not 'most common': one full disk or one missing ffmpeg
    is a standing problem the operator must act on, and it should not be drowned
    out by a dozen ordinary timeouts. So the actionable kinds win, and only then
    does frequency decide."""
    kinds = [classify(e) for e in (errors or [])]
    if not kinds:
        return None
    for kind in (DISK, POSTPROCESS, COOKIES, AGE_GATED, BLOCKED, THROTTLED, GONE, NOT_YET):
        if kind in kinds:
            return kind
    return TRANSIENT


def failure_summary(errors: Iterable[Any]) -> dict:
    """``{kind: count}`` for a batch, plus what to tell the user about it.

    Callers were each counting kinds by hand against a list that has since grown,
    which is how the health tile ended up claiming it reported age and cookie
    blocks while only ever counting the generic one."""
    kinds: dict = {}
    first_of: dict = {}
    for e in (errors or []):
        k = classify(e)
        kinds[k] = kinds.get(k, 0) + 1
        first_of.setdefault(k, e)
    top = dominant_failure(errors)
    return {
        "counts": kinds,
        "total": sum(kinds.values()),
        "dominant": top,
        "reason": human_reason(first_of[top]) if top in first_of else None,
        "needs_user_action": any(k in _NEEDS_USER for k in kinds),
    }


__all__ = ["classify", "human_reason", "failure_weight", "strikes_for",
           "looks_like_stale_ytdlp", "needs_user_action", "dominant_failure",
           "failure_summary",
           "GONE", "NOT_YET", "BLOCKED", "TRANSIENT",
           "AGE_GATED", "COOKIES", "THROTTLED", "POSTPROCESS", "DISK"]
