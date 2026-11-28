"""hallucination guards for whisper output, and the confidence score.

whisper writes something for every stretch of audio it is handed. on
silence, music and noise that something is the classic junk: "thanks for
watching", "subtitles by amara.org", one line repeated forty times. VAD
(silero, inside faster-whisper) keeps most of that audio away; these guards
catch what still gets through, using whisper's own numbers per segment.

segments are plain dicts: start, end, text, avg_logprob, no_speech_prob,
compression_ratio, words ([{start, end, word, probability}]). pure.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# whisper's own decode fallbacks use these; a segment past them is unreliable
NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0
COMPRESSION_RATIO_THRESHOLD = 2.4
# a segment this unsure is dropped even when whisper thinks it heard speech
HARD_LOGPROB_FLOOR = -1.6

# below this the whole file is thrown away instead of written
DISCARD_CONFIDENCE = 0.2
# below this the track is written but flagged "low confidence"
LOW_CONFIDENCE = 0.5

#: lines whisper makes up on silence and music (from youtube captions in its
#: training data). only dropped when they are the whole segment.
_STOCK_LINES = (
    r"thanks? (you )?for watching[.!]*",
    r"thank you\.?",
    r"(please )?(like and )?subscribe[^.]*[.!]*",
    r"subtitles? by .*",
    r"subtitled by .*",
    r"captions? by .*",
    r".*amara\.org.*",
    r"translated by .*",
    r"see you (in the )?next (video|time)[.!]*",
    r"bye[.!]*",
    r"you",
    r"\.+",
    r"sous-titres? .*",
    r"untertitel (der|im auftrag) .*",
    r"ondertiteling .*",
    r"продолжение следует.*",
    r"字幕.*",
)
_STOCK_RX = re.compile(r"^\s*(?:%s)\s*$" % "|".join(_STOCK_LINES), re.I)

# music notes, bracketed sound cues, nothing but punctuation
_MUSIC_ONLY = re.compile(r"^[\s♪♫♬♩#*~.\-–—!?,…]*$")
_CUE_ONLY = re.compile(r"^\s*(?:[\[(][^\])]{0,40}[\])]\s*)+$")
_MUSIC_WORDS = re.compile(r"\b(music|musique|musik|música|musica|applause|laughter|silence|"
                          r"instrumental|singing|humming)\b", re.I)


def _f(seg: Dict[str, Any], key: str, default: float) -> float:
    try:
        v = seg.get(key)
        return float(default if v is None else v)
    except (TypeError, ValueError):
        return default


def is_music_only(text: str) -> bool:
    """♪♪, [music], (applause), "..." and the like: no words to read."""
    t = str(text or "").strip()
    if not t or _MUSIC_ONLY.match(t):
        return True
    if _CUE_ONLY.match(t):
        inner = re.sub(r"[\[\]()]", " ", t)
        return bool(_MUSIC_WORDS.search(inner)) or not re.search(r"\w", inner)
    stripped = re.sub(r"[♪♫♬♩]", "", t).strip()
    return stripped != t and not re.search(r"\w{2,}", stripped)


def is_stock_line(text: str) -> bool:
    return bool(_STOCK_RX.match(str(text or "").strip()))


def collapse_repeats_in_text(text: str, max_repeat: int = 2) -> str:
    """"no no no no no no" -> "no no"; "I'm sorry. I'm sorry. I'm sorry." ->
    "I'm sorry. I'm sorry." (whisper's loop, inside one segment)."""
    t = re.sub(r"\s+", " ", str(text or "")).strip() + " "
    # a phrase of 1 to 6 words said more than max_repeat times in a row
    for n in range(6, 0, -1):
        rx = re.compile(r"(?<!\S)((?:\S+ ){%d})\1{%d,}" % (n, max_repeat), re.I)
        t = rx.sub(lambda m: m.group(1) * max_repeat, t)
    return t.strip()


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", str(text or "").lower()).strip()


def filter_segments(segments: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """the segments worth keeping, and why the rest went. pure.

    - no speech: whisper's no_speech_prob over 0.6 with a weak logprob (its
      own skip rule), or a logprob under the hard floor
    - loops: compression ratio over 2.4 (the text is mostly repeats)
    - music only: notes and [music] cues, nothing to read
    - stock lines: "thanks for watching" and friends, alone in a segment
    - repeats: the same line again and again back to back collapses into one
      (its time is stretched over the run)
    """
    why = {"no_speech": 0, "low_logprob": 0, "loop": 0, "music": 0, "stock": 0, "repeat": 0}
    kept: List[Dict[str, Any]] = []
    for seg in segments or []:
        text = str(seg.get("text") or "").strip()
        nsp = _f(seg, "no_speech_prob", 0.0)
        lp = _f(seg, "avg_logprob", 0.0)
        cr = _f(seg, "compression_ratio", 1.0)
        if nsp > NO_SPEECH_THRESHOLD and lp < LOGPROB_THRESHOLD:
            why["no_speech"] += 1
            continue
        if lp < HARD_LOGPROB_FLOOR:
            why["low_logprob"] += 1
            continue
        if cr > COMPRESSION_RATIO_THRESHOLD:
            why["loop"] += 1
            continue
        if is_music_only(text):
            why["music"] += 1
            continue
        if is_stock_line(text):
            why["stock"] += 1
            continue
        clean = collapse_repeats_in_text(text)
        if kept and _norm(kept[-1]["text"]) == _norm(clean) and _f(seg, "start", 0) - _f(kept[-1], "end", 0) < 10:
            kept[-1] = dict(kept[-1], end=max(_f(kept[-1], "end", 0), _f(seg, "end", 0)))
            why["repeat"] += 1
            continue
        out = dict(seg, text=clean)
        if clean != text and seg.get("words"):
            out["words"] = _trim_words(seg["words"], clean)
        kept.append(out)
    return kept, why


def _trim_words(words: List[Dict[str, Any]], clean: str) -> List[Dict[str, Any]]:
    """keep the word timings that still match the collapsed text, in order."""
    want = clean.split()
    out = []
    i = 0
    for w in words:
        if i >= len(want):
            break
        if str(w.get("word") or "").strip().lower().strip(",.!?") == want[i].lower().strip(",.!?"):
            out.append(w)
            i += 1
    return out if len(out) == len(want) else []


def confidence(raw: List[Dict[str, Any]], kept: List[Dict[str, Any]],
               speech_seconds: float, duration: float) -> float:
    """0..1 from whisper's numbers: how sure it was of its words (mean
    logprob over what was kept), how much of what it wrote survived the
    guards, how few loops it fell into, and how much of the runtime had
    speech. pure."""
    if not raw or not kept:
        return 0.0
    import math
    total_t = sum(max(0.0, _f(s, "end", 0) - _f(s, "start", 0)) for s in kept) or 1.0
    mean_lp = sum(_f(s, "avg_logprob", -1.0) * max(0.0, _f(s, "end", 0) - _f(s, "start", 0))
                  for s in kept) / total_t
    word_conf = math.exp(max(-3.0, min(0.0, mean_lp)))  # 0.05 .. 1
    survived = len(kept) / float(len(raw))
    loops = sum(1 for s in raw if _f(s, "compression_ratio", 1.0) > COMPRESSION_RATIO_THRESHOLD)
    loop_free = 1.0 - min(1.0, loops / float(len(raw)) * 3.0)
    coverage = 1.0
    if duration and duration > 0:
        share = speech_seconds / float(duration)
        coverage = 1.0 if share >= 0.15 else max(0.0, share / 0.15)
    # how sure of its words, held down by how much was junk, loops and silence
    score = word_conf * (0.6 + 0.4 * survived) * (0.5 + 0.5 * loop_free) * (0.6 + 0.4 * coverage)
    return round(max(0.0, min(1.0, score)), 3)


def verdict(conf: float) -> str:
    """"discard" | "low" | "ok"."""
    if conf < DISCARD_CONFIDENCE:
        return "discard"
    if conf < LOW_CONFIDENCE:
        return "low"
    return "ok"


__all__ = ["filter_segments", "confidence", "verdict", "is_music_only", "is_stock_line",
           "collapse_repeats_in_text", "NO_SPEECH_THRESHOLD", "LOGPROB_THRESHOLD",
           "COMPRESSION_RATIO_THRESHOLD", "DISCARD_CONFIDENCE", "LOW_CONFIDENCE"]
