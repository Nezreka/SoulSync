"""how an ai subtitle is named, and retiring it when a human one lands.

the name is ``<stem>.ai.<lang>[.hi][.forced].srt``: the language stays the
last token before the flags, which is where plex and emby read it (they take
the last token, after a forced/sdh flag). jellyfin and kodi read the tokens
right to left, take the first language and show what's left ("ai") as the
track title. ``ai`` is no language code in any of them (not iso 639-1, not a
bcp 47 subtag), so it never reads as one. ``.en.ai.srt`` would show as
"unknown" on plex.

pure except ``retire_ai_sidecars``, which touches the disk.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.subtitles.ai")

AI_TOKEN = "ai"

#: providers whose output is machine made. their history rows count as score
#: 0 in the upgrade pass, so any human sub that clears the gate wins.
AI_PROVIDERS = frozenset({"whisper", "ai"})


def ai_srt_name(video_path: Any, lang: str, hi: bool = False, forced: bool = False) -> str:
    """``Movie.ai.en.srt`` for ``Movie.mkv``. no directory."""
    stem = os.path.splitext(os.path.basename(str(video_path or "")))[0]
    name = "%s.%s.%s" % (stem, AI_TOKEN, lang)
    if hi:
        name += ".hi"
    if forced:
        name += ".forced"
    return name + ".srt"


def has_ai_token(tokens: List[str]) -> bool:
    """True when the dotted tokens between the video stem and the extension
    carry the ai marker."""
    return any(str(t or "").strip().lower() == AI_TOKEN for t in tokens or [])


def is_ai_name(name: Any, stem: Optional[str] = None) -> bool:
    """True for a sidecar named as an ai subtitle. with ``stem`` only the
    tokens after the video's own name count (a film called "Ai" stays human)."""
    base = os.path.basename(str(name or ""))
    root = os.path.splitext(base)[0]
    if stem is not None:
        st = str(stem)
        if not root.lower().startswith(st.lower()):
            return False
        root = root[len(st):]
    return has_ai_token(root.lower().split(".")[1:] if stem is None else root.lower().split("."))


def is_ai_provider(provider: Any) -> bool:
    return str(provider or "").strip().lower() in AI_PROVIDERS


_LANG_TAIL = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,4})?$")


def ai_sidecars_for(video_path: str, lang: str, names: Optional[List[str]] = None) -> List[str]:
    """full paths of the ai sidecars beside ``video_path`` in ``lang`` (any
    hi/forced flags). never raises."""
    from ..languages import satisfies_language, to_code
    folder = os.path.dirname(str(video_path))
    stem = os.path.splitext(os.path.basename(str(video_path)))[0]
    try:
        listing = names if names is not None else os.listdir(folder)
    except OSError:
        return []
    out = []
    for n in listing:
        n = str(n)
        root, ext = os.path.splitext(n)
        if ext.lower() not in (".srt", ".vtt", ".ass", ".ssa"):
            continue
        if not root.lower().startswith(stem.lower() + "."):
            continue
        tokens = root[len(stem) + 1:].lower().split(".")
        if not has_ai_token(tokens):
            continue
        codes = [to_code(t) for t in tokens if t not in ("ai", "hi", "sdh", "cc", "forced", "foreign")]
        code = next((c for c in reversed(codes) if c), None)
        if code and satisfies_language(code, lang):
            out.append(os.path.join(folder, n))
    return out


def retire_ai_sidecars(video_path: str, lang: str,
                       discard: Optional[Callable[[str], Any]] = None) -> int:
    """a human subtitle for ``lang`` just landed: the ai one goes. through the
    recycle bin when ``discard`` is given (so it can be put back), else
    deleted. returns how many went. never raises."""
    gone = 0
    for path in ai_sidecars_for(video_path, lang):
        try:
            if callable(discard):
                discard(path)
            else:
                os.remove(path)
            gone += 1
            logger.info("ai subtitles: %s replaced by a human subtitle", os.path.basename(path))
        except Exception:  # noqa: BLE001 - a stale ai file is untidy, never fatal
            logger.debug("could not retire %s", path, exc_info=True)
    return gone


def default_discarder(db: Any) -> Optional[Callable[[str], Any]]:
    """the recycle bin for this db, or None (plain delete)."""
    try:
        from core.video import organization, recycle
        settings = organization.load(db)

        def _discard(path: str) -> Any:
            return recycle.discard(path, settings, db, reason="ai subtitles replaced by a human subtitle")
        return _discard
    except Exception:  # noqa: BLE001
        return None


__all__ = ["AI_TOKEN", "AI_PROVIDERS", "ai_srt_name", "has_ai_token", "is_ai_name",
           "is_ai_provider", "ai_sidecars_for", "retire_ai_sidecars", "default_discarder"]
