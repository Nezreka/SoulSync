"""Sample Studio rough splits: quick built-in separation, no torch, no downloads.

rough on purpose. these guess, they don't isolate:

* drums/music: harmonic-percussive separation (librosa hpss). transients go
  to "drums (rough)", sustained tones to "music (rough)". the two add back up
  to the original, so nothing is lost between them.
* center: keeps what sits in the middle of the stereo image (usually vocal,
  kick, snare, bass) by keeping time-frequency bins where left and right
  agree. a mono track is all center, so it comes back as itself.

both write 16-bit wavs at the source sample rate.
"""

from __future__ import annotations

import os
from typing import Dict

from utils.logging_config import get_logger

logger = get_logger("sample.rough")

ROUGH_VERSION = 1
_N_FFT = 2048
_HOP = 512


def _write(path: str, y, sr: int) -> None:
    import numpy as np

    from .render import _load_soundfile

    sf = _load_soundfile()
    sf.write(path, np.clip(y, -1.0, 1.0), sr, subtype="PCM_16")


class RoughDrumsSeparator:
    name = "rough-hpss"
    method = "rough-drums"

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        import numpy as np
        import librosa

        from .render import decode_stereo

        os.makedirs(out_dir, exist_ok=True)
        y, sr = decode_stereo(track_path)
        drums = np.zeros_like(y)
        music = np.zeros_like(y)
        for c in range(y.shape[1]):
            stft = librosa.stft(y[:, c], n_fft=_N_FFT, hop_length=_HOP)
            harm, perc = librosa.decompose.hpss(stft)
            drums[:, c] = librosa.istft(perc, hop_length=_HOP, length=len(y))
            music[:, c] = librosa.istft(harm, hop_length=_HOP, length=len(y))
        paths = {
            "drums-rough": os.path.join(out_dir, "drums-rough.wav"),
            "music-rough": os.path.join(out_dir, "music-rough.wav"),
        }
        _write(paths["drums-rough"], drums, sr)
        _write(paths["music-rough"], music, sr)
        logger.info("rough drums/music split %s", track_path)
        return paths


class RoughCenterSeparator:
    name = "rough-center"
    method = "rough-center"

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        import numpy as np
        import librosa

        from .render import decode_stereo

        os.makedirs(out_dir, exist_ok=True)
        y, sr = decode_stereo(track_path)
        path = os.path.join(out_dir, "center-rough.wav")
        if y.shape[1] < 2:
            _write(path, y, sr)
            return {"center-rough": path}
        left = librosa.stft(y[:, 0], n_fft=_N_FFT, hop_length=_HOP)
        right = librosa.stft(y[:, 1], n_fft=_N_FFT, hop_length=_HOP)
        # 1 where left and right match in level and phase, 0 where they don't
        # (real part, so a bin that's equal in level but out of phase scores
        # low, not high)
        cross = np.real(left * np.conj(right))
        power = np.abs(left) ** 2 + np.abs(right) ** 2
        similarity = np.clip((2 * cross) / (power + 1e-12), 0.0, 1.0)
        mask = similarity ** 4  # sharpen: only keep bins that clearly agree
        center = librosa.istft(mask * (left + right) / 2, hop_length=_HOP, length=len(y))
        _write(path, np.stack([center, center], axis=1), sr)
        logger.info("rough center split %s", track_path)
        return {"center-rough": path}
