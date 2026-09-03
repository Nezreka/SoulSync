"""Moved to :mod:`core.youtube_errors`.

Nothing here is video-specific — it classifies yt-dlp failure text, and the MUSIC
side needs the same knowledge (issue #1126: a bot-block was being reported to the
user as "No results found"). Rather than grow a second, drifting copy of the
patterns, the module moved up and this shim keeps the existing video imports
working. Import from ``core.youtube_errors`` in new code.
"""

from core.youtube_errors import *          # noqa: F401,F403 - re-export
from core.youtube_errors import (           # noqa: F401 - explicit for linters
    AGE_GATED,
    BLOCKED,
    COOKIES,
    DISK,
    GONE,
    NOT_YET,
    POSTPROCESS,
    THROTTLED,
    TRANSIENT,
    classify,
    failure_weight,
    human_reason,
    looks_like_stale_ytdlp,
    needs_user_action,
    strikes_for,
)
