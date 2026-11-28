"""which audio track to transcribe, and what language it speaks.

the old path let faster-whisper decode the first audio stream (often a
commentary or a dub) and guessed the language from the first 30 s (often
the cold open's music). here:

1. the audio track: one tagged with the wanted language wins (it is then
   transcribed, no guessing). otherwise the default track, skipping
   commentary and audio description.
2. its language: the track's own tag, else the language the library stored
   for the file (media_files.audio_languages) when there's just one, else
   whisper's detection over several windows spread through the runtime,
   added up by probability.
3. the task: transcribe when the spoken language is the wanted one;
   whisper's translate when english is wanted from another language; else
   no ai subtitle (translation into other languages is a later phase).

pure apart from ``probe_audio_streams``.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..languages import satisfies_language, to_code

_COMMENTARY = re.compile(r"\b(commentary|comment|director|descri(ptive|bed|ption)|"
                         r"audio description|\bad\b|narrat)", re.I)


def base_lang(code: Any) -> str:
    """'pt-BR' -> 'pt', 'eng' -> 'en'. '' when unknown."""
    c = to_code(str(code or "").strip()) if code else None
    return (c or "").split("-")[0]


def probe_audio_streams(ffprobe: str, path: str) -> List[Dict[str, Any]]:
    """the file's audio streams: [{index (among audio), language, title,
    default, channels}]. [] when ffprobe can't read it."""
    cmd = [ffprobe, "-v", "error", "-select_streams", "a",
           "-show_entries", "stream=index,channels:stream_tags=language,title:stream_disposition=default,comment,"
           "visual_impaired", "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60).stdout  # noqa: S603
        data = json.loads(out or "{}")
    except Exception:  # noqa: BLE001
        return []
    streams = []
    for i, st in enumerate(data.get("streams") or []):
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        disp = st.get("disposition") or {}
        lang = str(tags.get("language") or "").lower()
        streams.append({"index": i, "language": None if lang in ("", "und", "unk", "mis", "zxx") else lang,
                        "title": tags.get("title") or "", "default": bool(disp.get("default")),
                        "commentary": bool(disp.get("comment")) or bool(disp.get("visual_impaired")),
                        "channels": st.get("channels")})
    return streams


def _is_commentary(st: Dict[str, Any]) -> bool:
    return bool(st.get("commentary")) or bool(_COMMENTARY.search(str(st.get("title") or "")))


def choose_audio(streams: Sequence[Dict[str, Any]], want: str,
                 stored_languages: Optional[Iterable[str]] = None) -> Tuple[int, Optional[str]]:
    """(audio index among the file's audio streams, its spoken language or
    None when nobody knows). pure."""
    main = [s for s in streams if not _is_commentary(s)] or list(streams)
    if not main:
        stored = [base_lang(x) for x in (stored_languages or []) if base_lang(x)]
        return 0, (stored[0] if len(set(stored)) == 1 else None)
    for s in main:
        if s.get("language") and satisfies_language(base_lang(s["language"]) or s["language"], want):
            return int(s["index"]), base_lang(s["language"])
    pick = next((s for s in main if s.get("default")), main[0])
    lang = base_lang(pick.get("language")) or None
    if lang is None:
        stored = list(dict.fromkeys(base_lang(x) for x in (stored_languages or []) if base_lang(x)))
        # the library's tag is per file: only trust it when the file has one
        # audio language (or one track)
        if len(stored) == 1 and (len(main) == 1 or len(streams) == 1):
            lang = stored[0]
    return int(pick["index"]), lang


def detection_windows(duration: float, count: int = 5, window: float = 30.0) -> List[float]:
    """start times for ``count`` windows spread over the runtime, skipping the
    first and last 8% (cold opens, credits). short files get fewer. pure."""
    try:
        d = float(duration or 0)
    except (TypeError, ValueError):
        d = 0.0
    if d <= window * 1.5:
        return [0.0]
    lo, hi = d * 0.08, max(d * 0.08, d * 0.92 - window)
    n = max(1, min(count, int((hi - lo) // window) + 1))
    if n == 1:
        return [round(lo, 2)]
    step = (hi - lo) / (n - 1)
    return [round(lo + step * i, 2) for i in range(n)]


def aggregate_detection(results: Iterable[Tuple[str, float, float]]) -> Tuple[Optional[str], float]:
    """add up per-window detections [(language, probability, speech share)]:
    each window votes with its probability, weighted by how much speech it
    had (a music window barely counts). returns (language, share of the
    total vote). pure."""
    votes: Dict[str, float] = {}
    total = 0.0
    for lang, prob, speech in results:
        code = base_lang(lang)
        if not code:
            continue
        try:
            w = max(0.0, float(prob)) * max(0.05, min(1.0, float(speech)))
        except (TypeError, ValueError):
            continue
        votes[code] = votes.get(code, 0.0) + w
        total += w
    if not votes or total <= 0:
        return None, 0.0
    lang = max(votes, key=votes.get)
    return lang, round(votes[lang] / total, 3)


def plan_task(spoken: Optional[str], want: str) -> Optional[str]:
    """"transcribe" | "translate" | None (can't make ``want`` from this
    audio). pure."""
    w = base_lang(want) or str(want or "").split("-")[0].lower()
    s = base_lang(spoken) if spoken else ""
    if not s:
        return None
    if s == w:
        return "transcribe"
    if w == "en":
        return "translate"
    return None


__all__ = ["probe_audio_streams", "choose_audio", "detection_windows", "aggregate_detection",
           "plan_task", "base_lang"]
