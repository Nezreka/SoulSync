"""Sample Studio key detection: Krumhansl-Kessler profiles on the track's chroma.

average the chroma over the track, then correlate it with the major and minor
key profiles rotated to all 12 roots. the best match is the key, and its
correlation is the confidence the page shows ("key uncertain" under 0.5).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

# Krumhansl & Kessler (1982) probe-tone profiles, index 0 = the tonic
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def key_from_chroma(chroma_mean: Sequence[float]) -> Optional[Dict[str, Any]]:
    """{"name": "C minor", "confidence": 0.82} from a 12-bin mean chroma.

    None when there's no pitch content to judge (silence, pure noise floor).
    """
    import numpy as np

    c = np.asarray(chroma_mean, dtype=np.float64)
    if c.shape != (12,) or not np.all(np.isfinite(c)) or c.sum() <= 1e-9 or c.std() <= 1e-9:
        return None
    best_r = -2.0
    best_name = None
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        p = np.asarray(profile, dtype=np.float64)
        for root in range(12):
            r = float(np.corrcoef(c, np.roll(p, root))[0, 1])
            if r > best_r:
                best_r = r
                best_name = f"{NOTE_NAMES[root]} {mode}"
    if best_name is None:
        return None
    return {"name": best_name, "confidence": round(max(0.0, min(1.0, best_r)), 3)}


def detect_key(y, sr: int) -> Optional[Dict[str, Any]]:
    """key of a mono signal. never raises, a key is a nice-to-have."""
    try:
        import warnings

        import librosa
        import numpy as np

        if len(y) < sr:  # under a second: not enough to hear a key
            return None
        with warnings.catch_warnings():
            # tuning estimation whines on near-silent audio, the None below covers it
            warnings.simplefilter("ignore", UserWarning)
            chroma = librosa.feature.chroma_stft(y=y, sr=sr, n_fft=4096, hop_length=2048)
        # chroma is normalized per frame, so weight by loudness: quiet frames
        # are mostly noise and shouldn't vote as hard as the chorus
        rms = librosa.feature.rms(y=y, frame_length=4096, hop_length=2048)[0]
        n = min(chroma.shape[1], len(rms))
        chroma, weights = chroma[:, :n], rms[:n]
        if n == 0 or weights.sum() <= 1e-9:
            return None
        mean = (chroma * weights).sum(axis=1) / weights.sum()
        return key_from_chroma(np.asarray(mean))
    except Exception:
        return None
