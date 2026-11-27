"""whisper's words into subtitles a person would time by hand.

whisper hands back segments: a run of speech that can last 10 s and carry
120 characters on one line. a subtitle wants at most two lines of about 42
characters, on screen 1 to 7 s, slow enough to read (about 17 to 20
characters a second), split where the speaker pauses or a sentence ends,
never in the middle of a word, with a small gap between cues.

the word timestamps decide the edges: a cue starts when its first word is
said and ends when its last word is. pure.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MAX_LINE = 42
MAX_LINE_CJK = 16
MAX_LINES = 2
MIN_DURATION = 1.0
MAX_DURATION = 7.0
MAX_CPS = 20.0
MIN_GAP = 0.08
PAUSE_BREAK = 0.6
# a sentence end with at least this much text already in the cue closes it
SENTENCE_MIN_CHARS = 12

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_SENT_END = re.compile(r"[.!?。！？…]['\"”’)]*$")
_CLAUSE_END = re.compile(r"[,;:，、；：—–-]['\"”’)]*$")


class Cue(Tuple[float, float, str]):
    pass


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def words_of(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """every word with its time, in order. a segment without word timings
    gets them spread over its span by length. a segment's last word is marked
    so the cue builder can treat it as a soft break."""
    out: List[Dict[str, Any]] = []
    for seg in segments or []:
        ws = [w for w in (seg.get("words") or []) if str(w.get("word") or "").strip()]
        if not ws:
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            toks = list(text) if _CJK.search(text) and " " not in text else text.split()
            start, end = _f(seg.get("start")), _f(seg.get("end"))
            if end <= start:
                end = start + max(0.5, len(text) / MAX_CPS)
            total = sum(len(t) for t in toks) or 1
            t = start
            ws = []
            sep = "" if _CJK.search(text) and " " not in text else " "
            for tok in toks:
                d = (end - start) * len(tok) / total
                ws.append({"start": t, "end": t + d, "word": sep + tok})
                t += d
        for i, w in enumerate(ws):
            out.append({"start": _f(w.get("start")), "end": max(_f(w.get("end")), _f(w.get("start"))),
                        "word": str(w.get("word") or ""), "seg_end": i == len(ws) - 1})
    return out


def _join(words: List[Dict[str, Any]]) -> str:
    return re.sub(r"\s+", " ", "".join(w["word"] for w in words)).strip()


def _max_line(text: str) -> int:
    return MAX_LINE_CJK if _CJK.search(text) else MAX_LINE


def split_lines(text: str, max_line: Optional[int] = None) -> List[str]:
    """``text`` on as few lines as fit, two balanced ones when it needs two.
    breaks at spaces only (or anywhere in CJK text); prefers a break after
    punctuation. a single word longer than a line stays whole."""
    text = str(text or "").strip()
    limit = max_line or _max_line(text)
    if len(text) <= limit:
        return [text] if text else []
    cjk = bool(_CJK.search(text)) and " " not in text
    if cjk:
        points = list(range(1, len(text)))
    else:
        points = [m.start() for m in re.finditer(r" ", text)]
    best = None
    for p in points:
        a, b = text[:p].strip(), text[p:].strip()
        if not a or not b:
            continue
        over = max(0, len(a) - limit) + max(0, len(b) - limit)
        balance = abs(len(a) - len(b))
        punct = 0 if _CLAUSE_END.search(a) or _SENT_END.search(a) else 6
        # a short first line reads badly ("I / don't know what you want")
        cost = over * 1000 + balance + punct
        if best is None or cost < best[0]:
            best = (cost, a, b)
    if best is None:
        return [text]
    _cost, a, b = best
    if len(b) > limit and not cjk:
        return [a] + split_lines(b, limit)
    return [a, b]


def _fits(text: str) -> bool:
    return len(split_lines(text)) <= MAX_LINES and all(
        len(line) <= _max_line(text) or " " not in line for line in split_lines(text))


def build_cues(words: List[Dict[str, Any]]) -> List[Tuple[float, float, List[Dict[str, Any]]]]:
    """group words into cues: break on a pause, on a sentence end once the
    cue has some text, before it stops fitting two lines, and before it runs
    past the max duration."""
    cues: List[Tuple[float, float, List[Dict[str, Any]]]] = []
    cur: List[Dict[str, Any]] = []

    def close():
        if cur:
            cues.append((cur[0]["start"], cur[-1]["end"], list(cur)))
            cur.clear()

    for w in words:
        if cur:
            prev = cur[-1]
            text_now = _join(cur)
            gap = w["start"] - prev["end"]
            joined = _join(cur + [w])
            if (gap >= PAUSE_BREAK
                    or (_SENT_END.search(prev["word"].strip()) and len(text_now) >= SENTENCE_MIN_CHARS)
                    or (prev.get("seg_end") and len(text_now) >= SENTENCE_MIN_CHARS * 2)
                    or not _fits(joined)
                    or w["end"] - cur[0]["start"] > MAX_DURATION):
                # a clause end a word or two back makes a nicer break than here
                if not _fits(joined) and len(cur) > 3 and not _CLAUSE_END.search(prev["word"].strip()):
                    for back in range(1, min(7, len(cur) - 1)):
                        if _CLAUSE_END.search(cur[-1 - back]["word"].strip()):
                            carry = cur[-back:]
                            del cur[-back:]
                            close()
                            cur.extend(carry)
                            break
                    else:
                        close()
                else:
                    close()
        cur.append(w)
    close()
    return cues


def time_cues(raw: List[Tuple[float, float, List[Dict[str, Any]]]]) -> List[Tuple[float, float, str]]:
    """the final timing: at least MIN_DURATION and long enough to read at
    MAX_CPS (stretched into the silence after, never over the next cue), at
    most MAX_DURATION, with MIN_GAP between cues."""
    out: List[Tuple[float, float, str]] = []
    for i, (start, end, ws) in enumerate(raw):
        text = "\n".join(split_lines(_join(ws)))
        if not text:
            continue
        nxt = raw[i + 1][0] if i + 1 < len(raw) else None
        need = max(MIN_DURATION, len(text.replace("\n", " ")) / MAX_CPS)
        want_end = max(end, start + need)
        want_end = min(want_end, start + MAX_DURATION)
        if nxt is not None:
            want_end = min(want_end, nxt - MIN_GAP)
        want_end = max(want_end, end if nxt is None or end <= nxt - MIN_GAP else start + 0.2)
        if want_end <= start:
            want_end = start + 0.2
        out.append((round(start, 3), round(want_end, 3), text))
    return out


def shape(segments: List[Dict[str, Any]]) -> List[Tuple[float, float, str]]:
    """segments (with or without word timings) -> (start, end, text) cues."""
    return time_cues(build_cues(words_of(segments)))


def _ts(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def cues_to_srt(cues: List[Tuple[float, float, str]]) -> str:
    lines: List[str] = []
    for n, (start, end, text) in enumerate(cues, 1):
        lines += [str(n), "%s --> %s" % (_ts(start), _ts(end)), text, ""]
    return "\n".join(lines).strip() + ("\n" if lines else "")


__all__ = ["shape", "build_cues", "time_cues", "words_of", "split_lines", "cues_to_srt",
           "MAX_LINE", "MAX_LINES", "MIN_DURATION", "MAX_DURATION", "MAX_CPS", "MIN_GAP"]
