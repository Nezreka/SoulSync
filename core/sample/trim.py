"""Sample Studio trim: tighten a selection to the part that actually sounds.

pure numpy on a decoded region. "silent" is relative to the loudest moment in
the selection, so a quiet intro trims the same as a loud one.
"""

from __future__ import annotations

from typing import Optional, Tuple

# anything 45 dB under the selection's peak counts as silence
RELATIVE_FLOOR_DB = -45.0
# and so does anything under -70 dBFS, whatever the peak
ABSOLUTE_FLOOR_DB = -70.0
FRAME_MS = 5.0
# keep a little air so the attack isn't shaved and the release isn't chopped
PAD_BEFORE_MS = 5.0
PAD_AFTER_MS = 15.0


def sounding_bounds(y, sr: int) -> Optional[Tuple[float, float]]:
    """(start_s, end_s) of the sounding part of y, relative to y's start.

    None when the whole thing is silence, so the caller leaves the selection
    alone instead of collapsing it to nothing.
    """
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    if y.ndim == 1:
        y = y[:, None]
    n = len(y)
    if n == 0:
        return None
    level = np.max(np.abs(y), axis=1)
    frame = max(1, int(sr * FRAME_MS / 1000.0))
    n_frames = int(np.ceil(n / frame))
    padded = np.pad(level, (0, n_frames * frame - n))
    frame_peak = padded.reshape(n_frames, frame).max(axis=1)
    peak = float(frame_peak.max())
    abs_floor = 10 ** (ABSOLUTE_FLOOR_DB / 20)
    if peak <= abs_floor:
        return None
    threshold = max(peak * 10 ** (RELATIVE_FLOOR_DB / 20), abs_floor)
    loud = np.flatnonzero(frame_peak >= threshold)
    first = int(loud[0]) * frame
    last = min(n, (int(loud[-1]) + 1) * frame)
    start = max(0, first - int(sr * PAD_BEFORE_MS / 1000.0))
    end = min(n, last + int(sr * PAD_AFTER_MS / 1000.0))
    return start / sr, end / sr
