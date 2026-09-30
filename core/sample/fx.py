"""Sample Studio render fx: normalize, reverse, fade, space (reverb), delay.

preview and save both run through apply_fx with the same params, so what you
hear in the preview is what lands in the stash. everything here is pure
numpy/scipy on a (samples, channels) float32 array, no files.

chain order: reverse, space, delay, fade, normalize. reverse comes first so
the reverb and echo trail the reversed sound like they would on a sampler.
fade runs after the tails so the end of a reverb tail fades out too, and
normalize is last so it sees the real final peak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

FADE_MS_DEFAULT = 5.0
FADE_MS_MIN = 0.5
FADE_MS_MAX = 1000.0
SPACE_MIN_S = 0.2
SPACE_MAX_S = 1.5
# note value -> beats. 1/4 is one beat in 4/4.
DELAY_BEATS = {"1/4": 1.0, "1/8": 0.5, "1/2": 2.0}
# echoes stop at -60 dB or after this much tail, whichever comes first, so
# 0.99 feedback can't turn a one-bar chop into a minute of audio.
DELAY_MAX_TAIL_S = 8.0
# peak normalize lands a hair under full scale so 16-bit renders never clip.
NORMALIZE_PEAK = 10 ** (-0.1 / 20)
# how loud the reverb sits under the dry signal (unit-energy impulse).
SPACE_WET = 0.35


@dataclass(frozen=True)
class DelayFx:
    time: str
    feedback: float
    mix: float

    def as_dict(self) -> Dict[str, Any]:
        return {"time": self.time, "feedback": self.feedback, "mix": self.mix}


@dataclass(frozen=True)
class RenderFx:
    normalize: bool = False
    fade_ms: float = FADE_MS_DEFAULT
    reverse: bool = False
    space: Optional[float] = None
    delay: Optional[DelayFx] = None

    @property
    def needs_bpm(self) -> bool:
        return self.delay is not None

    def as_entry_fields(self) -> Dict[str, Any]:
        """the shape the stash echoes back to the page."""
        return {
            "normalize": "peak" if self.normalize else None,
            "fade_ms": self.fade_ms,
            "reverse": self.reverse,
            "space": self.space,
            "delay": self.delay.as_dict() if self.delay else None,
        }


def _num(value, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if out != out or out in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a number")
    return out


def parse_fx(data: Dict[str, Any]) -> RenderFx:
    """request body -> RenderFx. raises ValueError with a message the page can show.

    missing keys mean the defaults, so old callers get the same sound plus the
    always-on 5 ms fade that stops clicks.
    """
    normalize = data.get("normalize")
    if normalize not in (None, "", "peak", False):
        raise ValueError("normalize must be 'peak' or left out")

    fade_ms = data.get("fade_ms")
    fade_ms = FADE_MS_DEFAULT if fade_ms is None else _num(fade_ms, "fade_ms")
    if not FADE_MS_MIN <= fade_ms <= FADE_MS_MAX:
        raise ValueError(f"fade_ms must be {FADE_MS_MIN:g}..{FADE_MS_MAX:g}")

    reverse = data.get("reverse", False)
    if reverse not in (True, False, None):
        raise ValueError("reverse must be true or false")

    space = data.get("space")
    if space is not None:
        space = _num(space, "space")
        if not SPACE_MIN_S <= space <= SPACE_MAX_S:
            raise ValueError(f"space must be {SPACE_MIN_S:g}..{SPACE_MAX_S:g} seconds")

    delay = data.get("delay")
    if delay is not None:
        if not isinstance(delay, dict):
            raise ValueError("delay must be an object")
        time = delay.get("time")
        if time not in DELAY_BEATS:
            raise ValueError(f"delay time must be one of {', '.join(DELAY_BEATS)}")
        feedback = _num(delay.get("feedback", 0), "delay feedback")
        mix = _num(delay.get("mix", 0), "delay mix")
        if not 0 <= feedback < 1:
            raise ValueError("delay feedback must be 0..0.99")
        if not 0 <= mix <= 1:
            raise ValueError("delay mix must be 0..1")
        delay = DelayFx(time=time, feedback=round(feedback, 4), mix=round(mix, 4))

    return RenderFx(
        normalize=normalize == "peak",
        fade_ms=fade_ms,
        reverse=bool(reverse),
        space=space,
        delay=delay,
    )


def fx_from_row(row: Dict[str, Any]) -> RenderFx:
    """stash row columns -> RenderFx. bad or missing values fall back to defaults."""
    import json

    delay = None
    raw = row.get("delay_json")
    if raw:
        try:
            d = json.loads(raw)
            delay = DelayFx(time=d["time"], feedback=float(d["feedback"]), mix=float(d["mix"]))
            if delay.time not in DELAY_BEATS:
                delay = None
        except (ValueError, KeyError, TypeError):
            delay = None
    fade = row.get("fade_ms")
    return RenderFx(
        normalize=row.get("normalize") == "peak",
        fade_ms=float(fade) if fade is not None else FADE_MS_DEFAULT,
        reverse=bool(row.get("reverse")),
        space=float(row["space"]) if row.get("space") is not None else None,
        delay=delay,
    )


# ── dsp ──────────────────────────────────────────────────────────────────


def _reverb(y, sr: int, t60: float):
    """decaying-noise reverb. seeded, so the preview and the save are identical."""
    import numpy as np
    from scipy.signal import fftconvolve, lfilter

    n_ir = max(1, int(sr * t60 * 1.2))
    t = np.arange(n_ir) / sr
    env = np.exp(-6.9078 * t / t60)  # -60 dB at t60
    predelay = int(sr * 0.012)
    rng = np.random.default_rng(20260930)
    chans = y.shape[1]
    out = np.zeros((len(y) + n_ir + predelay - 1, chans), dtype=np.float64)
    out[: len(y)] += y
    for c in range(chans):
        noise = rng.standard_normal(n_ir)
        # darken the tail a bit, real rooms lose highs first
        ir = lfilter([0.45], [1.0, -0.55], noise) * env
        ir /= np.sqrt(np.sum(ir * ir)) + 1e-12
        wet = fftconvolve(y[:, c].astype(np.float64), ir)
        out[predelay: predelay + len(wet), c] += SPACE_WET * wet
    return out.astype(np.float32)


def _delay(y, sr: int, seconds: float, feedback: float, mix: float):
    import numpy as np

    d = max(1, int(round(seconds * sr)))
    if feedback <= 0:
        repeats = 1
    else:
        repeats = 1 + int(np.floor(np.log(1e-3) / np.log(feedback)))
    repeats = max(1, min(repeats, int(DELAY_MAX_TAIL_S * sr // d) or 1))
    out = np.zeros((len(y) + repeats * d, y.shape[1]), dtype=np.float32)
    out[: len(y)] += y
    for k in range(1, repeats + 1):
        gain = mix * feedback ** (k - 1)
        out[k * d: k * d + len(y)] += gain * y
    return out


def _fade(y, sr: int, fade_ms: float):
    import numpy as np

    n = min(int(sr * fade_ms / 1000.0), len(y) // 2)
    if n < 1:
        return y
    ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n))).astype(np.float32)[:, None]
    y = y.copy()
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def apply_fx(y, sr: int, fx: RenderFx, bpm: Optional[float] = None):
    """run the chain on y (samples, channels). bpm is only needed for delay."""
    import numpy as np

    out = np.asarray(y, dtype=np.float32)
    if out.ndim == 1:
        out = out[:, None]
    if len(out) == 0:
        return out
    if fx.reverse:
        out = out[::-1].copy()
    if fx.space is not None:
        out = _reverb(out, sr, fx.space)
    if fx.delay is not None:
        if not bpm or bpm <= 0:
            raise ValueError("delay follows the beat, so it needs the track's tempo")
        seconds = DELAY_BEATS[fx.delay.time] * 60.0 / float(bpm)
        out = _delay(out, sr, seconds, fx.delay.feedback, fx.delay.mix)
    out = _fade(out, sr, fx.fade_ms)
    if fx.normalize:
        peak = float(np.max(np.abs(out)))
        if peak > 1e-6:
            out = out * (NORMALIZE_PEAK / peak)
    return out.astype(np.float32)
