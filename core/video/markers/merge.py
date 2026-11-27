"""one answer per kind from every place a marker can come from.

precedence, best first: what an admin set by hand, an .edl beside the file,
soulsync's own detection, the media server's markers (plex, jellyfin), and
last a chapter the file names Intro / Recap / Credits. a kind an admin
removed (a suppression) shows nothing from any automatic source; only a
marker set by hand afterwards brings it back. pure.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

KINDS = ("recap", "intro", "credits", "preview", "commercial")
SOURCES = ("user", "sidecar", "detected", "plex", "jellyfin", "chapter")
RANK = {s: i for i, s in enumerate(SOURCES)}
LABELS = {"recap": "Skip recap", "intro": "Skip intro", "credits": "Skip credits",
          "preview": "Skip preview", "commercial": "Skip ad"}

# the same names the web player has always read (timeline.ts SKIP_NAMES)
_CHAPTER_NAMES = (
    ("recap", re.compile(r"^(recap|previously(\s+on\b.*)?)$", re.I)),
    ("intro", re.compile(r"^(intro(duction)?|opening( credits| titles?)?|op|main titles?|"
                         r"title sequence|theme song)$", re.I)),
    ("credits", re.compile(r"^((end(ing)? |closing )?credits|ending|ed|outro|end titles?)$", re.I)),
    ("preview", re.compile(r"^(preview|next time|next episode)$", re.I)),
)


def from_chapters(chapters: Iterable[Dict[str, Any]], duration: Optional[float]) -> List[Dict[str, Any]]:
    """markers from the file's chapter names. a chapter longer than ten
    minutes isn't an intro whatever it's called (credits may run longer)."""
    chs = [c for c in chapters or [] if c.get("start") is not None]
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(chs):
        name = str(c.get("title") or "").strip()
        kind = next((k for k, rx in _CHAPTER_NAMES if rx.match(name)), None)
        if not kind:
            continue
        start = float(c["start"])
        end = c.get("end")
        if end is None:
            end = chs[i + 1]["start"] if i + 1 < len(chs) else duration
        if end is None or float(end) <= start + 1:
            continue
        end = float(end)
        if kind != "credits" and end - start > 600:
            continue
        out.append({"kind": kind, "start": round(start, 3), "end": round(end, 3), "source": "chapter"})
    return out


def clean(marker: Dict[str, Any], duration: Optional[float]) -> Optional[Dict[str, Any]]:
    """a marker inside the file, or None"""
    try:
        start, end = float(marker["start"]), float(marker["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if marker.get("kind") not in KINDS or start < 0 or end <= start:
        return None
    if duration and duration > 0:
        if start >= duration:
            return None
        end = min(end, float(duration))
    if end - start < 1.0:
        return None
    out = dict(marker)
    out["start"], out["end"] = round(start, 3), round(end, 3)
    return out


def best(candidates: Iterable[Dict[str, Any]], suppressed: Iterable[str] = (),
         duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """the marker per kind the player shows, by source rank, in time order.
    commercials are spans, not one per kind: every one from the winning
    source is kept."""
    hidden = set(suppressed or ())
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for raw in candidates:
        m = clean(raw, duration)
        if m is None or m.get("source") not in RANK:
            continue
        if m["kind"] in hidden and m["source"] != "user":
            continue
        by_kind.setdefault(m["kind"], []).append(m)
    out: List[Dict[str, Any]] = []
    for kind, ms in by_kind.items():
        top = min(RANK[m["source"]] for m in ms)
        winners = [m for m in ms if RANK[m["source"]] == top]
        if kind != "commercial":
            winners = winners[:1]
        out.extend(winners)
    out.sort(key=lambda m: (m["start"], m["kind"]))
    return out


def public(marker: Dict[str, Any]) -> Dict[str, Any]:
    """what a player sees of one marker"""
    out = {"kind": marker["kind"], "start": marker["start"], "end": marker["end"],
           "source": marker["source"], "label": LABELS.get(marker["kind"], "Skip")}
    if marker.get("confidence") is not None:
        out["confidence"] = round(float(marker["confidence"]), 2)
    post = (marker.get("extra") or {}).get("post_credits") if isinstance(marker.get("extra"), dict) else None
    if marker["kind"] == "credits" and post and len(post) == 2:
        out["post_credits"] = {"start": round(float(post[0]), 3), "end": round(float(post[1]), 3)}
    return out
