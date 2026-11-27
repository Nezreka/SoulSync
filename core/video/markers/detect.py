"""the season matcher: intros, credits and recaps from what each file's
analysis left behind (fingerprints, envelopes, the picture's credits).

* **intro**: the span a file's head shares with its siblings: up to six
  episodes of the season, nearest first, and when the season has fewer
  than two others, the seasons either side (shows keep their intro, so one
  new episode, or a new season's first, still gets one). each comparison
  votes; spans that agree within 2.5 s are one answer, the one with the
  most votes wins. 10 s to 2.5 min, starting in the first quarter (at most
  ten minutes), so a cold open is no problem.
* **credits**: the shared end song (the tail fingerprints, 15 s to 5 min,
  ending in the last quarter) and the picture (``visual``). both agree: the
  picture's start (to the frame), high confidence. one alone still counts (an anime ending
  over full animation has no dark frames; a movie has no siblings). what
  plays after the shared song and isn't credit-like is a scene after the
  credits.
* **recap**: clips of the last two episodes in this one's head, before the
  intro (opt in: needs the earlier episodes' whole soundtrack). many short
  matches at different shifts, together at least 15 s.

pure: ``Features`` in, markers out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from .audio import Print, all_matches, best_match, refine
from .edl import head_window

INTRO_MIN, INTRO_MAX = 10.0, 150.0
CREDITS_MIN, CREDITS_MAX = 15.0, 300.0
MAX_SIBLINGS = 6
AGREE = 2.5


@dataclass
class Features:
    file_id: int
    duration: float
    season: Optional[int] = None
    episode: Optional[int] = None
    head: Optional[Print] = None
    tail: Optional[Print] = None
    body: Optional[Print] = None
    visual: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def pick_siblings(target: Features, pool: List[Features]) -> List[Features]:
    """who to compare with: the season's nearest episodes; with fewer than
    two of those, the nearest seasons' latest (earlier season first)"""
    # another copy of the same episode would match from start to end
    others = [f for f in pool if f.file_id != target.file_id
              and not (f.season == target.season and f.episode == target.episode)]
    same = [f for f in others if f.season == target.season]
    same.sort(key=lambda f: (abs((f.episode or 0) - (target.episode or 0)), f.episode or 0))
    out = same[:MAX_SIBLINGS]
    if len(out) < 2:
        near = [f for f in others if f.season != target.season and f.season is not None]
        near.sort(key=lambda f: (abs((f.season or 0) - (target.season or 0)),
                                 0 if (f.season or 0) < (target.season or 0) else 1,
                                 -(f.episode or 0) if (f.season or 0) < (target.season or 0)
                                 else (f.episode or 0)))
        out += near[: MAX_SIBLINGS - len(out)]
    return out


def _consensus(cands: List[Tuple[float, float, float]]) -> Optional[Tuple[float, float, int, float]]:
    """(start, end, votes, mean ratio) of the biggest group of agreeing spans"""
    if not cands:
        return None
    groups: List[List[Tuple[float, float, float]]] = []
    for c in sorted(cands):
        for g in groups:
            if abs(median(x[0] for x in g) - c[0]) <= AGREE and abs(median(x[1] for x in g) - c[1]) <= AGREE:
                g.append(c)
                break
        else:
            groups.append([c])
    groups.sort(key=lambda g: (len(g), sum(x[1] - x[0] for x in g)), reverse=True)
    g = groups[0]
    return (round(median(x[0] for x in g), 3), round(median(x[1] for x in g), 3), len(g),
            sum(x[2] for x in g) / len(g))


def _confidence(votes: int, ratio: float, asked: int) -> float:
    agree = votes / max(1, asked)
    return round(min(0.97, 0.35 + 0.12 * min(votes, 4) + 0.2 * ratio + 0.15 * agree), 2)


def intro(target: Features, siblings: List[Features]) -> Optional[Dict[str, Any]]:
    if target.head is None:
        return None
    window = head_window(target.duration)
    cands = []
    asked = 0
    for sib in siblings:
        if sib.head is None or sib.head.algo != target.head.algo:
            continue
        asked += 1
        m = best_match(target.head, sib.head, min_s=INTRO_MIN, max_s=INTRO_MAX,
                       a_start_max=window, b_start_max=head_window(sib.duration))
        if m is None:
            continue
        a = target.head
        s, e = refine(a, sib.head, a.time(m.a0), a.time(m.a1 + 1), m.shift * a.step)
        cands.append((s, e, m.ratio))
    found = _consensus(cands)
    if not found:
        return None
    s, e, votes, ratio = found
    # one comparison only counts when it's a clean, long match
    if votes == 1 and (ratio < 0.6 or e - s < 15):
        return None
    if e - s < INTRO_MIN or s > window:
        return None
    return {"kind": "intro", "start": max(0.0, s), "end": min(e, target.duration),
            "confidence": _confidence(votes, ratio, asked),
            "extra": {"votes": votes, "compared": asked, "how": "audio"}}


def credits(target: Features, siblings: List[Features], is_movie: bool = False) -> Optional[Dict[str, Any]]:
    dur = target.duration
    audio = None
    asked = 0
    if not is_movie and target.tail is not None:
        cands = []
        for sib in siblings:
            if sib.tail is None or sib.tail.algo != target.tail.algo:
                continue
            asked += 1
            m = best_match(target.tail, sib.tail, min_s=CREDITS_MIN, max_s=CREDITS_MAX,
                           a_end_min=dur * 0.75 if dur else None)
            if m is None:
                continue
            a = target.tail
            s, e = refine(a, sib.tail, a.time(m.a0), a.time(m.a1 + 1), m.shift * a.step)
            if dur and s < dur * 0.6:
                continue
            cands.append((s, e, m.ratio))
        got = _consensus(cands)
        if got and (got[2] > 1 or got[3] >= 0.6):
            audio = got
    visual = target.visual if isinstance(target.visual, dict) and target.visual.get("start") else None
    if audio is None and visual is None:
        return None
    post = None
    if audio and visual:
        a_s, a_e, votes, ratio = audio
        v_s = float(visual["start"])
        if abs(a_s - v_s) <= 20:
            # the picture says where the credits roll (to the frame); a song
            # that starts over the last scene isn't skipped with them
            start, conf, how = v_s, 0.95, "audio+picture"
        else:
            start, conf, how = a_s, _confidence(votes, ratio, asked), "audio"
        end = dur
        if visual.get("post_credits"):
            post = visual["post_credits"]
        elif dur and 8 <= dur - a_e <= 180:
            post = [a_e, dur]
    elif audio:
        a_s, a_e, votes, ratio = audio
        start, end, conf, how = a_s, dur, _confidence(votes, ratio, asked), "audio"
        # after the shared song: a preview or a scene, worth a hint, never skipped
        if dur and 8 <= dur - a_e <= 180:
            post = [a_e, dur]
    else:
        start, end = float(visual["start"]), dur
        conf, how = float(visual.get("confidence") or 0.6), "picture"
        post = visual.get("post_credits")
    if post:
        end = float(post[0])
    if not dur or end - start < CREDITS_MIN * (0.66 if post else 1):
        return None
    out = {"kind": "credits", "start": round(start, 3), "end": round(min(end, dur), 3),
           "confidence": round(conf, 2), "extra": {"how": how}}
    if post:
        out["extra"]["post_credits"] = [round(float(post[0]), 3), round(float(post[1]), 3)]
    return out


def recap(target: Features, earlier: List[Features], intro_marker: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """clips of earlier episodes at the head, before the intro"""
    if target.head is None:
        return None
    limit = float(intro_marker["start"]) if intro_marker else min(240.0, head_window(target.duration))
    if limit < 15:
        return None
    spans: List[Tuple[float, float]] = []
    for prev in earlier[:2]:
        if prev.body is None or prev.body.algo != target.head.algo:
            continue
        for s, e in all_matches(target.head, prev.body, min_s=2.5):
            if s < limit:
                spans.append((s, min(e, limit)))
    if not spans:
        return None
    spans.sort()
    first, last = spans[0][0], max(e for _, e in spans)
    covered = sum(e - s for s, e in spans)
    if first > 120 or covered < 15 or covered / max(1.0, last - first) < 0.4:
        return None
    if intro_marker and float(intro_marker["start"]) - last <= 6:
        last = float(intro_marker["start"])
    return {"kind": "recap", "start": round(max(0.0, first), 3), "end": round(last, 3),
            "confidence": round(min(0.9, 0.5 + covered / 120.0), 2),
            "extra": {"how": "audio", "clips": len(spans)}}


def season(pool: List[Features], *, recaps: bool = False) -> Dict[int, List[Dict[str, Any]]]:
    """every file of a season (and its neighbours) in, the markers of each
    file of the season out"""
    out: Dict[int, List[Dict[str, Any]]] = {}
    for f in pool:
        if f.extra.get("reference_only"):
            continue
        sibs = pick_siblings(f, pool)
        found = []
        i = intro(f, sibs)
        if i:
            found.append(i)
        c = credits(f, sibs)
        if c:
            if i and c["start"] < i["end"]:
                c = None
            else:
                found.append(c)
        if recaps:
            earlier = sorted((p for p in pool if p.season == f.season and p.episode is not None
                              and f.episode is not None and p.episode < f.episode),
                             key=lambda p: -(p.episode or 0))
            r = recap(f, earlier, i)
            if r:
                found.append(r)
        out[f.file_id] = found
    return out


def movie(f: Features) -> List[Dict[str, Any]]:
    c = credits(f, [], is_movie=True)
    return [c] if c else []
