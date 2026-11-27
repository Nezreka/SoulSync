"""Kodi .edl sidecars: ``<video stem>.edl`` beside the file.

one span per line, ``start end action``. times are seconds (``61.2``) or
clock times (``1:01.2``, ``0:01:01.200``); a frame number (``#1530``) needs
the frame rate, which the file doesn't say, so those lines are skipped.
the actions are 0 cut, 1 mute, 2 scene marker, 3 commercial break (Kodi
skips 3 by itself). a file has no intro or credits type, so a span is typed
by where it sits: inside the head window an intro (an earlier one there a
recap), touching the end credits, anything else a commercial.

soulsync writes intros, recaps and credits as action 3, so Kodi skips them
too. it only rewrites an .edl it wrote itself (video_marker_sidecars): a
user's own, or comskip's, is read and never touched.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

ACTION_SKIP = 3
EDL_KINDS = ("recap", "intro", "credits", "preview", "commercial")
_TIME = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


def sidecar_path(video_path: str) -> str:
    return os.path.splitext(video_path)[0] + ".edl"


def _seconds(text: str) -> Optional[float]:
    m = _TIME.match(text.strip())
    if not m:
        return None
    a, b, c = m.groups()
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + float(c)
    if a is not None:
        return int(a) * 60 + float(c)
    return float(c)


def parse(text: str) -> List[Tuple[float, float, int]]:
    """the spans in an .edl, in order. bad lines are skipped, never fatal."""
    out: List[Tuple[float, float, int]] = []
    for raw in (text or "").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("\t", " ").split()
        if len(parts) < 2 or parts[0].startswith("#") or parts[1].startswith("#"):
            continue
        start, end = _seconds(parts[0]), _seconds(parts[1])
        if start is None or end is None or end <= start or start < 0:
            continue
        action = 0
        if len(parts) > 2:
            try:
                action = int(float(parts[2]))
            except ValueError:
                continue
        if action not in (0, 1, 2, 3):
            continue
        out.append((round(start, 3), round(end, 3), action))
    out.sort()
    return out[:500]


def head_window(duration: Optional[float]) -> float:
    """where an intro can start: the first quarter, at most ten minutes"""
    if not duration or duration <= 0:
        return 600.0
    return min(600.0, duration * 0.25)


def classify(spans: List[Tuple[float, float, int]], duration: Optional[float],
             is_movie: bool = False) -> List[Dict[str, Any]]:
    """typed markers from untyped spans, by position. a mute (1) or a bare
    scene marker (2) is no skip and is left out."""
    skips = [(s, e) for s, e, a in spans if a in (0, 3) and e - s >= 1.0]
    window = head_window(duration)
    out: List[Dict[str, Any]] = []
    head = [(s, e) for s, e in skips if s < window and e - s <= 300 and not is_movie]
    tail_from = None
    if duration:
        tail_from = duration - max(30.0, min(duration * 0.2, 900.0 if is_movie else 420.0))
    taken = set()
    if head:
        # the last span in the head window is the intro, one before it a recap
        intro = head[-1]
        out.append({"kind": "intro", "start": intro[0], "end": intro[1]})
        taken.add(intro)
        if len(head) > 1:
            recap = head[-2]
            out.append({"kind": "recap", "start": recap[0], "end": recap[1]})
            taken.add(recap)
    for s, e in skips:
        if (s, e) in taken:
            continue
        if duration and tail_from is not None and s >= tail_from and e >= duration - 30.0:
            out.append({"kind": "credits", "start": s, "end": min(e, duration)})
        else:
            out.append({"kind": "commercial", "start": s, "end": e})
    out.sort(key=lambda m: m["start"])
    return out


def render(markers: List[Dict[str, Any]], action: int = ACTION_SKIP) -> str:
    """the .edl text for some markers (intro, recap, credits, preview,
    commercial), in order, one per line"""
    lines = []
    for m in sorted(markers, key=lambda x: float(x["start"])):
        if m.get("kind") not in EDL_KINDS:
            continue
        s, e = float(m["start"]), float(m["end"])
        if e <= s:
            continue
        lines.append(f"{s:.3f}\t{e:.3f}\t{int(action)}")
    return "\n".join(lines) + ("\n" if lines else "")


def read(video_path: str) -> Optional[Tuple[str, List[Tuple[float, float, int]], os.stat_result]]:
    """(path, spans, stat) of the .edl beside a video, or None"""
    path = sidecar_path(video_path)
    try:
        st = os.stat(path)
        if st.st_size > 256 * 1024:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return path, parse(text), st


def write_atomic(path: str, text: str) -> os.stat_result:
    """write next to the video through a temp file and a rename, so a reader
    never sees half a file. raises OSError (read-only mounts, permissions)."""
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, "." + os.path.basename(path) + ".soulsync-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return os.stat(path)
