"""audio fingerprints and the matcher that finds a span two episodes share.

a fingerprint is one uint32 per ~0.124 s of sound. chromaprint's (fpcalc,
in the image) is used when it's there; otherwise soulsync makes its own
(Haitsma-Kalker style: 33 bands of 300-2000 Hz, one bit per band pair for
"rising or falling against the last frame", on long smoothed windows). two files only ever compare
fingerprints of the same algorithm.

matching two fingerprints:
  1. candidate time shifts from values the two have in common (an inverted
     index, so no all-shifts scan)
  2. at each shift, items that differ in at most 6 of 32 bits match; the
     longest run of matches, small gaps allowed, is the shared span
  3. the edges are then refined on a 75 Hz loudness envelope: the span
     grows or shrinks to where the two soundtracks really start and stop
     agreeing, and stops at silence, so a skip lands within 13 ms (under a
     frame) instead of a fingerprint step

decoding streams into memory a region at a time (mono, 11025 Hz, 16 bit:
about 13 MB for ten minutes), under ``nice``/``ionice`` and one ffmpeg thread.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from utils.logging_config import get_logger

logger = get_logger("video.markers.audio")

RATE = 11025
HOP = 1365                      # samples per item, chromaprint's own hop (4096 * 1/3)
STEP = HOP / RATE               # 0.1238 s
ENV_HZ = 75                      # 147 samples a value at 11025 Hz: 13 ms
MAX_BIT_ERRORS = 6
SILENT = 45                     # envelope value (dBFS + 100) under which it's silence: -55 dBFS

_POP16 = np.array([bin(i).count("1") for i in range(1 << 16)], dtype=np.uint8)


@dataclass
class Print:
    """a region's fingerprint: ``fp[i]`` covers ``offset + i * step``"""

    fp: np.ndarray
    offset: float
    step: float
    algo: str
    env: Optional[np.ndarray] = None   # uint8, ENV_HZ per second, from ``offset``

    def time(self, index: float) -> float:
        return self.offset + index * self.step


def popcount(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.uint32, copy=False)
    return _POP16[x & 0xFFFF] + _POP16[x >> 16]


# ── running the tools ────────────────────────────────────────────────────

def low_priority() -> List[str]:
    """a prefix that keeps the analysis out of everyone's way"""
    if os.name != "posix":
        return []
    out: List[str] = []
    if shutil.which("nice"):
        out += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        out += ["ionice", "-c", "3"]
    return out


def fpcalc_path() -> Optional[str]:
    return shutil.which("fpcalc")


def decode(ffmpeg: str, source: str, start: float, length: Optional[float],
           rate: int = RATE, timeout: float = 900) -> np.ndarray:
    """mono 16-bit pcm of [start, start + length) (length None: to the end)"""
    cmd = low_priority() + [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-i", source, "-map", "0:a:0", "-vn", "-sn", "-ac", "1", "-ar", str(rate),
            "-f", "s16le", "pipe:1"]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)  # noqa: S603 - our own argv
    if res.returncode != 0 and not res.stdout:
        raise RuntimeError((res.stderr or b"").decode("utf-8", "replace")[-300:] or "ffmpeg failed")
    raw = res.stdout[: len(res.stdout) // 2 * 2]
    return np.frombuffer(raw, dtype="<i2")


def chromaprint(fpcalc: str, pcm: np.ndarray, rate: int = RATE) -> Optional[np.ndarray]:
    """chromaprint's raw fingerprint of some pcm, or None if fpcalc can't"""
    if pcm.size < rate * 3:
        return None
    seconds = int(pcm.size / rate) + 2
    cmd = low_priority() + [fpcalc, "-raw", "-rate", str(rate), "-channels", "1",
                            "-format", "s16le", "-length", str(seconds), "-"]
    try:
        res = subprocess.run(cmd, input=pcm.astype("<i2").tobytes(), capture_output=True,  # noqa: S603
                             timeout=300)
    except (OSError, subprocess.SubprocessError):
        logger.debug("fpcalc failed", exc_info=True)
        return None
    for line in res.stdout.decode("ascii", "replace").splitlines():
        if line.startswith("FINGERPRINT="):
            try:
                vals = [int(v) & 0xFFFFFFFF for v in line[12:].split(",") if v.strip()]
            except ValueError:
                return None
            return np.array(vals, dtype=np.uint32) if vals else None
    return None


# ── soulsync's own fingerprint and the envelope ──────────────────────────

_FRAME = 8192                   # 0.74 s: long windows keep the bits steady when two
_BANDS = np.geomspace(300.0, 2000.0, 34)   # files' frames don't line up to the sample
_SMOOTH = 3


def own_fingerprint(pcm: np.ndarray, rate: int = RATE) -> np.ndarray:
    """one uint32 per HOP samples, from band-energy changes (pure numpy).
    log energies over long, smoothed windows: the same sound read half a
    step later still gives (nearly) the same bits."""
    x = pcm.astype(np.float32) / 32768.0
    n = 1 + (x.size - _FRAME) // HOP if x.size >= _FRAME else 0
    if n < 2:
        return np.zeros(0, dtype=np.uint32)
    window = np.hanning(_FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(_FRAME, 1.0 / rate)
    bins = np.digitize(freqs, _BANDS) - 1
    bands = np.zeros((freqs.size, 33), dtype=np.float32)
    for b in range(33):
        bands[bins == b, b] = 1.0
    energy = np.empty((n, 33), dtype=np.float64)
    for start in range(0, n, 512):          # a block at a time keeps memory small
        stop = min(n, start + 512)
        idx = np.arange(_FRAME)[None, :] + HOP * np.arange(start, stop)[:, None]
        power = np.abs(np.fft.rfft(x[idx] * window[None, :], axis=1)) ** 2
        energy[start:stop] = np.log(power @ bands + 1e-9)
    if _SMOOTH > 1:
        kernel = np.ones(_SMOOTH) / _SMOOTH
        pad = np.pad(energy, ((_SMOOTH // 2, _SMOOTH // 2), (0, 0)), mode="edge")
        energy = np.stack([np.convolve(pad[:, b], kernel, "valid") for b in range(33)], axis=1)
    diff = energy[:, :-1] - energy[:, 1:]
    bits = (diff[1:] - diff[:-1]) > 0
    weights = (1 << np.arange(32, dtype=np.uint64))
    vals = (bits.astype(np.uint64) * weights[None, :]).sum(axis=1)
    return np.concatenate([[0], vals]).astype(np.uint32)


def envelope(pcm: np.ndarray, rate: int = RATE, hz: int = ENV_HZ) -> np.ndarray:
    """loudness every 1/hz s as dBFS + 100, 0..100 (uint8)"""
    win = rate // hz
    n = pcm.size // win
    if n == 0:
        return np.zeros(0, dtype=np.uint8)
    x = pcm[: n * win].astype(np.float32).reshape(n, win) / 32768.0
    rms = np.sqrt(np.mean(x * x, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    return np.clip(np.round(db + 100), 0, 100).astype(np.uint8)


def fingerprint(pcm: np.ndarray, offset: float, fpcalc: Optional[str]) -> Optional[Print]:
    """the region's fingerprint and envelope, chromaprint first"""
    if pcm.size < RATE * 3:
        return None
    env = envelope(pcm)
    if fpcalc:
        fp = chromaprint(fpcalc, pcm)
        if fp is not None and fp.size > 8:
            return Print(fp, offset, STEP, "chromaprint", env)
    fp = own_fingerprint(pcm)
    if fp.size < 8:
        return None
    return Print(fp, offset, STEP, "soulsync", env)


# ── matching ─────────────────────────────────────────────────────────────

def candidate_shifts(a: np.ndarray, b: np.ndarray, top: int = 8) -> List[int]:
    """the shifts (index in a minus index in b) the most shared values point at"""
    if a.size == 0 or b.size == 0:
        return []
    found: dict = {}
    for mask in (0xFFFFFFFF, 0xFFFFFF00):
        aa, bb = (a & mask), (b & mask)
        order = np.argsort(bb, kind="stable")
        bs = bb[order]
        left = np.searchsorted(bs, aa, "left")
        right = np.searchsorted(bs, aa, "right")
        counts = right - left
        ok = (counts > 0) & (counts <= 8) & (aa != 0)
        ia = np.nonzero(ok)[0]
        if ia.size == 0:
            continue
        reps = counts[ia]
        starts = left[ia]
        a_idx = np.repeat(ia, reps)
        within = np.arange(reps.sum()) - np.repeat(np.cumsum(reps) - reps, reps)
        b_idx = order[np.repeat(starts, reps) + within]
        shifts, n = np.unique(a_idx - b_idx, return_counts=True)
        for s, c in zip(shifts.tolist(), n.tolist(), strict=True):
            found[s] = found.get(s, 0) + c
    ranked = sorted((c, s) for s, c in found.items() if c >= 2)[::-1][:top]
    out: List[int] = []
    for _, s in ranked:
        for d in (0, -1, 1):
            if s + d not in out:
                out.append(s + d)
    return out


def _runs(match: np.ndarray, max_gap: int) -> List[Tuple[int, int, int]]:
    """(first, last, matched count) runs of True, gaps up to max_gap allowed"""
    idx = np.nonzero(match)[0]
    if idx.size == 0:
        return []
    cuts = np.nonzero(np.diff(idx) > max_gap + 1)[0]
    out = []
    begin = 0
    for c in list(cuts) + [idx.size - 1]:
        seg = idx[begin:c + 1]
        out.append((int(seg[0]), int(seg[-1]), int(seg.size)))
        begin = c + 1
    return out


@dataclass
class Match:
    a0: int
    a1: int          # inclusive
    shift: int       # index in a minus index in b
    ratio: float     # matched items / span items

    @property
    def length(self) -> int:
        return self.a1 - self.a0 + 1


def runs_at(a: np.ndarray, b: np.ndarray, shift: int, max_gap: int,
            max_bits: int = MAX_BIT_ERRORS) -> List[Match]:
    lo = max(0, shift)
    hi = min(a.size, b.size + shift)
    if hi - lo < 4:
        return []
    aa, bb = a[lo:hi], b[lo - shift:hi - shift]
    errs = popcount(aa ^ bb)
    # digital silence fingerprints as 0 in both: silence isn't a shared intro
    match = (errs <= max_bits) & (aa != 0) & (bb != 0)
    out = []
    for first, last, count in _runs(match, max_gap):
        span = last - first + 1
        out.append(Match(lo + first, lo + last, shift, count / span))
    return out


def best_match(a: Print, b: Print, *, min_s: float, max_s: float,
               a_start_max: Optional[float] = None, b_start_max: Optional[float] = None,
               a_end_min: Optional[float] = None, gap_s: float = 3.5,
               min_ratio: float = 0.45) -> Optional[Match]:
    """the longest span both fingerprints share, within the limits (times are
    the file's own clock), or None"""
    if a.algo != b.algo or a.fp.size < 8 or b.fp.size < 8:
        return None
    max_gap = max(1, int(round(gap_s / a.step)))
    best: Optional[Match] = None
    for shift in candidate_shifts(a.fp, b.fp):
        for m in runs_at(a.fp, b.fp, shift, max_gap):
            secs = m.length * a.step
            if secs < min_s or m.ratio < min_ratio:
                continue
            if secs > max_s:
                continue
            if a_start_max is not None and a.time(m.a0) > a_start_max:
                continue
            if b_start_max is not None and b.time(m.a0 - m.shift) > b_start_max:
                continue
            if a_end_min is not None and a.time(m.a1 + 1) < a_end_min:
                continue
            if best is None or (m.length * m.ratio) > (best.length * best.ratio):
                best = m
    return best


def all_matches(a: Print, b: Print, *, min_s: float, gap_s: float = 1.0,
                top: int = 30) -> List[Tuple[float, float]]:
    """every span of ``a`` (times) that appears somewhere in ``b``, at any
    shift: a recap is many short clips, each from another place"""
    if a.algo != b.algo or a.fp.size < 8 or b.fp.size < 8:
        return []
    max_gap = max(1, int(round(gap_s / a.step)))
    spans = []
    for shift in candidate_shifts(a.fp, b.fp, top=top):
        for m in runs_at(a.fp, b.fp, shift, max_gap):
            if m.length * a.step >= min_s and m.ratio >= 0.5:
                spans.append((a.time(m.a0), a.time(m.a1 + 1)))
    return merge_spans(spans, 0.5)


def merge_spans(spans: List[Tuple[float, float]], gap: float) -> List[Tuple[float, float]]:
    out: List[List[float]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


# ── edges finer than a fingerprint step ──────────────────────────────────

def _env_at(p: Print, t: float) -> Optional[int]:
    if p.env is None:
        return None
    i = int(round((t - p.offset) * ENV_HZ))
    if 0 <= i < p.env.size:
        return int(p.env[i])
    return None


def fine_shift(a: Print, b: Print, start: float, end: float, shift_s: float,
               reach: float = 0.2) -> float:
    """the shift between two matched spans to the envelope's 13 ms: a
    fingerprint step is 124 ms, too coarse to line edges up on"""
    if a.env is None or b.env is None:
        return shift_s
    i0 = int(round((start - a.offset) * ENV_HZ))
    i1 = int(round((end - a.offset) * ENV_HZ))
    i0, i1 = max(0, i0), min(a.env.size, i1)
    if i1 - i0 < ENV_HZ:
        return shift_s
    seg = a.env[i0:i1].astype(np.int16)
    best, best_err = shift_s, None
    steps = int(reach * ENV_HZ)
    for k in range(-steps, steps + 1):
        cand = shift_s + k / ENV_HZ
        j0 = int(round((start - cand - b.offset) * ENV_HZ))
        if j0 < 0 or j0 + seg.size > b.env.size:
            continue
        err = float(np.mean(np.abs(seg - b.env[j0:j0 + seg.size].astype(np.int16))))
        if best_err is None or err < best_err:
            best, best_err = cand, err
    return best


def refine(a: Print, b: Print, start: float, end: float, shift_s: float,
           reach: float = 3.0, grow: float = 1.0, tol: int = 6) -> Tuple[float, float]:
    """move a shared span's edges (``a``'s clock; ``b`` runs ``shift_s``
    behind) to where the two envelopes really agree, never into silence.
    an edge moves in by up to ``reach`` but out by only ``grow``: a
    fingerprint window smears an edge by under a second, and two steady
    noises of the same loudness agree on an envelope without being the
    same sound.
    without envelopes the span is returned as it is."""
    if a.env is None or b.env is None:
        return start, end
    shift_s = fine_shift(a, b, start, end, shift_s)
    step = 1.0 / ENV_HZ

    def ok(t: float) -> Optional[bool]:
        va, vb = _env_at(a, t), _env_at(b, t - shift_s)
        if va is None or vb is None:
            return None
        if va < SILENT and vb < SILENT:
            return False
        return abs(va - vb) <= tol

    def steady(t: float, forward: bool) -> bool:
        # agreement that holds for a quarter second, so one lucky value
        # doesn't move an edge
        n = int(0.25 * ENV_HZ)
        for k in range(n):
            v = ok(t + (k if forward else -k) * step)
            if not v:
                return False
        return True

    s = start
    if steady(s, True):
        t = s
        while t - step >= start - grow and ok(t - step):
            t -= step
        s = t
    else:
        t = s
        while t <= start + reach and not steady(t, True):
            t += step
        s = t if t <= start + reach else start
    e = end
    if steady(e - step, False):
        t = e
        while t + step <= end + grow and ok(t):
            t += step
        e = t
    else:
        t = e
        while t >= end - reach and not steady(t - step, False):
            t -= step
        e = t if t >= end - reach else end
    if e - s < (end - start) * 0.5:
        return start, end
    # a gap of silence right at an edge is where the cut is: snap to it
    snap = int(0.2 * ENV_HZ)
    for k in range(snap + 1):
        v = _env_at(a, s - k * step)
        if v is not None and v < SILENT:
            s = s - k * step + step
            break
    for k in range(snap + 1):
        v = _env_at(a, e + k * step)
        if v is not None and v < SILENT:
            e = e + k * step
            break
    return round(s, 3), round(e, 3)


# ── storage shape ────────────────────────────────────────────────────────

def to_blob(arr: Optional[np.ndarray], dtype: str) -> Optional[bytes]:
    if arr is None:
        return None
    return np.asarray(arr).astype(dtype).tobytes()


def from_blob(blob: Optional[bytes], dtype: str) -> Optional[np.ndarray]:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=dtype).copy()
