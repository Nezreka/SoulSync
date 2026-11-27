"""credits from the picture: the end of a file decoded small and grey.

only keyframes are decoded across the tail (a movie's last 15 minutes, an
episode's last 7), at 64x36: cheap even on a Pi. a frame looks like credits
when it is mostly dark with a little bright text, or a near-uniform card.
the credits are the earliest block of credit-like frames that runs to the
end. a run of ordinary picture between two credit blocks, or after the
last one, is a scene after the credits (the hint the player shows).

the start is then found to the frame: every frame of a few seconds around
it is decoded (still small), and the credits start at the first frame from
which the picture stays credit-like (or the fade to black before it).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from utils.logging_config import get_logger

from .audio import low_priority

logger = get_logger("video.markers.visual")

W, H = 64, 36
_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")


@dataclass
class Frame:
    t: float
    mean: float
    std: float
    dark: float      # share of pixels under 40
    bright: float    # share of pixels over 150

    @property
    def black(self) -> bool:
        return self.mean < 16 and self.bright < 0.002

    @property
    def credits_like(self) -> bool:
        if self.black:
            return True
        if self.dark >= 0.78 and self.bright <= 0.18:
            return True              # text on black, a roll or a card
        return self.std < 7.0        # a plain card (any colour, low detail)


def features(pixels: np.ndarray, t: float) -> Frame:
    px = pixels.astype(np.float32)
    return Frame(t, float(px.mean()), float(px.std()), float((px < 40).mean()),
                 float((px > 150).mean()))


def scan(ffmpeg: str, source: str, start: float, length: float, keyframes_only: bool = True,
         timeout: float = 900) -> List[Frame]:
    """the frames of [start, start + length), small and grey, with their times"""
    cmd = low_priority() + [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-threads", "1"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}", "-copyts", "-i", source,
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", f"scale={W}:{H},format=gray,showinfo", "-fps_mode", "passthrough",
            "-f", "rawvideo", "pipe:1"]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)  # noqa: S603 - our own argv
    if res.returncode != 0 and not res.stdout:
        raise RuntimeError((res.stderr or b"").decode("utf-8", "replace")[-300:] or "ffmpeg failed")
    times = [float(x) for x in _PTS.findall(res.stderr.decode("utf-8", "replace"))]
    size = W * H
    n = min(len(times), len(res.stdout) // size)
    raw = np.frombuffer(res.stdout[: n * size], dtype=np.uint8).reshape(n, H, W) if n else []
    return [features(raw[i], times[i]) for i in range(n)]


@dataclass
class Block:
    start: float
    end: float
    frames: int
    like: int

    @property
    def length(self) -> float:
        return self.end - self.start


def blocks(frames: List[Frame], max_gap: float = 12.0) -> List[Block]:
    """runs of credit-like frames; one odd frame inside a run (a logo, a
    bright card) doesn't break it"""
    out: List[Block] = []
    cur: Optional[Block] = None
    misses = 0
    last_like_t = None
    for f in frames:
        if f.credits_like:
            if cur is not None and last_like_t is not None and f.t - last_like_t <= max_gap and misses <= 1:
                cur.end = f.t
                cur.frames += 1 + misses
                cur.like += 1
            else:
                if cur is not None:
                    out.append(cur)
                cur = Block(f.t, f.t, 1, 1)
            misses = 0
            last_like_t = f.t
        else:
            misses += 1
            if cur is not None and misses > 1:
                out.append(cur)
                cur = None
    if cur is not None:
        out.append(cur)
    # a block reaches until the next frame after its last one
    times = [f.t for f in frames]
    for b in out:
        later = [t for t in times if t > b.end]
        b.end = later[0] if later else b.end
    return out


def find_credits(frames: List[Frame], duration: float, is_movie: bool) -> Optional[Dict[str, Any]]:
    """{start, end, post_credits?, confidence} or None"""
    if not frames or not duration:
        return None
    min_len = 30.0 if is_movie else 15.0
    scene_max = 300.0 if is_movie else 150.0
    end_of_file = max(duration, frames[-1].t)
    bs = [b for b in blocks(frames) if b.length >= 4.0]
    if not bs:
        return None
    last = bs[-1]
    post = None
    if last.end >= end_of_file - 25.0:
        main = last
        # walk back over short breaks (a logo) and one scene between two blocks
        i = len(bs) - 2
        while i >= 0:
            prev = bs[i]
            gap = main.start - prev.end
            if gap <= 20.0:
                main = Block(prev.start, main.end, prev.frames + main.frames, prev.like + main.like)
                i -= 1
                continue
            if post is None and gap <= scene_max and prev.length >= min_len and gap >= 5.0:
                post = (prev.end, main.start)
                main = Block(prev.start, main.end, prev.frames + main.frames, prev.like + main.like)
                i -= 1
                continue
            break
        if post is not None:
            # the credits are the block before the scene; the scene ends at the later block
            credits = Block(main.start, post[0], 0, 0)
        else:
            credits = main
    elif last.length >= min_len and 5.0 <= end_of_file - last.end <= scene_max:
        credits = last
        post = (last.end, end_of_file)
    else:
        return None
    if credits.length < min_len:
        return None
    if credits.start < duration * (0.6 if is_movie else 0.55):
        return None
    span = [f for f in frames if credits.start <= f.t < credits.end]
    share = (sum(1 for f in span if f.credits_like) / len(span)) if span else 0.0
    if share < 0.8:
        return None
    conf = 0.55 + 0.3 * min(1.0, credits.length / 90.0) + (0.1 if share > 0.95 else 0.0)
    out: Dict[str, Any] = {"start": round(credits.start, 3),
                           "end": round(post[0] if post else end_of_file, 3),
                           "confidence": round(min(conf, 0.9), 2)}
    if post:
        out["post_credits"] = [round(post[0], 3), round(post[1], 3)]
    return out


def sharpen(frames: List[Frame], near: float) -> Optional[float]:
    """the first frame (of every frame around ``near``) from which the
    picture stays credit-like; the fade to black before it if there is one"""
    if not frames:
        return None
    like = [f.credits_like for f in frames]
    # the earliest frame after which at least 90% of what's left is credit-like
    best = None
    for i in range(len(frames)):
        rest = like[i:]
        if rest and like[i] and sum(rest) / len(rest) >= 0.9:
            best = i
            break
    if best is None:
        return None
    # walk back over a fade (black frames just before)
    while best > 0 and frames[best - 1].black:
        best -= 1
    t = frames[best].t
    return round(t, 3) if abs(t - near) <= 10 else None
