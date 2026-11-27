"""what this server can run whisper on, and which model to suggest.

cpu cores, memory, the cpu family, and an nvidia gpu when ctranslate2 can
see one (the ``:cuda`` image, or a source install with the cuda libraries).
the suggestion comes from the hardware first, and from a measured quick test
once one has run (``bench``), so the settings page shows real numbers.
"""

from __future__ import annotations

import glob
import os
import platform
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.subtitles.ai.hardware")

_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()
_cuda_preloaded = False


def _mem_bytes() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return 0


def preload_cuda_libs() -> None:
    """the ``:cuda`` image ships cublas and cudnn as pip wheels (nvidia-*).
    ctranslate2 finds them only on the loader path, so load them once by
    hand before ctranslate2 asks. a no-op everywhere else."""
    global _cuda_preloaded
    if _cuda_preloaded or os.name == "nt":
        return
    _cuda_preloaded = True
    try:
        import ctypes
        import importlib.util
        spec = importlib.util.find_spec("nvidia")
        roots = list(spec.submodule_search_locations or []) if spec else []
        for root in roots:
            for sub in ("cuda_runtime", "cublas", "cudnn"):
                for lib in sorted(glob.glob(os.path.join(root, sub, "lib", "lib*.so*"))):
                    try:
                        ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        continue
    except Exception:  # noqa: BLE001 - no gpu pack is the normal case
        logger.debug("cuda preload skipped", exc_info=True)


def _gpu_name() -> Optional[str]:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=5)
        line = (out.stdout or "").strip().splitlines()
        return line[0].strip() if out.returncode == 0 and line else None
    except Exception:  # noqa: BLE001
        return None


def detect(refresh: bool = False) -> Dict[str, Any]:
    """{cores, memory_bytes, arch, cpu, cuda, cuda_devices, gpu, engine,
    cuda_problem}. cached; never raises."""
    with _cache_lock:
        if _cache and not refresh:
            return dict(_cache)
    info: Dict[str, Any] = {
        "cores": os.cpu_count() or 1,
        "memory_bytes": _mem_bytes(),
        "arch": platform.machine().lower(),
        "cpu": (platform.processor() or platform.machine() or "").strip(),
        "cuda": False, "cuda_devices": 0, "gpu": None, "cuda_problem": None,
        "engine": None,
    }
    try:
        import importlib.util
        if importlib.util.find_spec("faster_whisper") is not None:
            info["engine"] = "faster-whisper"
    except Exception:  # noqa: BLE001
        pass
    try:
        import importlib.util
        if importlib.util.find_spec("ctranslate2") is not None:
            preload_cuda_libs()
            import ctranslate2  # noqa: PLC0415 - optional
            n = int(ctranslate2.get_cuda_device_count())
            info["cuda_devices"] = n
            info["cuda"] = n > 0
    except Exception as e:  # noqa: BLE001
        info["cuda_problem"] = str(e)[:200]
    if info["cuda"]:
        info["gpu"] = _gpu_name() or "NVIDIA GPU"
    elif _gpu_name():
        # a card is there but ctranslate2 can't use it: the plain image has
        # no cuda libraries. say so in plain words
        info["gpu"] = _gpu_name()
        info["cuda_problem"] = info["cuda_problem"] or "the cuda libraries aren't installed (use the :cuda image)"
    with _cache_lock:
        _cache.clear()
        _cache.update(info)
    return dict(info)


def summary(info: Dict[str, Any]) -> str:
    """"NVIDIA GeForce RTX 3060 (CUDA)" or "CPU, 8 cores, 16 GB"."""
    if info.get("cuda"):
        return "%s (CUDA)" % (info.get("gpu") or "NVIDIA GPU")
    gb = int(round((info.get("memory_bytes") or 0) / 1e9))
    arm = " (ARM)" if str(info.get("arch") or "").startswith(("arm", "aarch")) else ""
    return "CPU%s, %d cores%s" % (arm, int(info.get("cores") or 1), (", %d GB" % gb) if gb else "")


def recommend(info: Dict[str, Any], bench: Optional[Dict[str, Any]] = None) -> str:
    """the model to suggest. pure.

    with a gpu: large-v3-turbo. on a cpu: from cores and memory (a pi or a
    weak nas gets base, 4 cores small, 8+ cores small or medium). a measured
    quick test overrides that: the biggest model that ran at least 3x faster
    than real time on this box (a 45 min episode in 15 min or less)."""
    mem_gb = (info.get("memory_bytes") or 0) / 1e9
    if bench:
        order = ["large-v3-turbo", "medium", "small", "base", "tiny"]
        for name in order:
            b = bench.get(name) or {}
            try:
                speed = float(b.get("speed") or 0)
            except (TypeError, ValueError):
                speed = 0.0
            if speed >= 3.0:
                return name
    if info.get("cuda"):
        return "large-v3-turbo"
    cores = int(info.get("cores") or 1)
    arm = str(info.get("arch") or "").startswith(("arm", "aarch"))
    if arm or cores <= 2 or (mem_gb and mem_gb < 3):
        return "base"
    if cores >= 12 and mem_gb >= 8:
        return "medium"
    return "small"


# ── quick test ───────────────────────────────────────────────────────────────

BENCH_SECONDS = 30


def bench_audio(seconds: int = BENCH_SECONDS):
    """a speech-like test signal: harmonics with a moving pitch and syllable
    shaped bursts. it isn't words, but it keeps the encoder and decoder as
    busy as speech does (vad is off for the test)."""
    import numpy as np  # noqa: PLC0415
    sr = 16000
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    pitch = 140 + 40 * np.sin(2 * np.pi * 0.3 * t)
    phase = 2 * np.pi * np.cumsum(pitch) / sr
    voice = sum(np.sin(phase * k) / k for k in range(1, 6))
    syll = (np.sin(2 * np.pi * 3.5 * t) > -0.2).astype(np.float32)
    rng = np.random.default_rng(7)
    sig = (voice * syll * 0.3 + rng.normal(0, 0.01, t.shape)).astype(np.float32)
    return sig


def run_bench(model_dir: str, device: str = "cpu", compute_type: str = "int8",
              threads: int = 0) -> Dict[str, Any]:
    """time one 30 s transcription with this model. returns {speed (x real
    time), seconds, device}. raises when the model can't load."""
    from faster_whisper import WhisperModel  # noqa: PLC0415 - optional dep
    audio = bench_audio()
    t0 = time.monotonic()
    model = WhisperModel(model_dir, device=device, compute_type=compute_type, cpu_threads=threads)
    load_s = time.monotonic() - t0
    t1 = time.monotonic()
    segments, _info = model.transcribe(audio, language="en", beam_size=5, vad_filter=False,
                                       condition_on_previous_text=False)
    for _s in segments:
        pass
    took = max(0.001, time.monotonic() - t1)
    del model
    return {"speed": round(BENCH_SECONDS / took, 2), "seconds": round(took, 2),
            "load_seconds": round(load_s, 2), "device": device,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def estimate_minutes(duration_s: float, speed: Optional[float], speech_share: float = 0.7) -> Optional[float]:
    """how long a file of ``duration_s`` should take at a measured speed.
    vad skips the silence and music, so only ``speech_share`` of it is
    transcribed. pure."""
    try:
        sp = float(speed or 0)
        d = float(duration_s or 0)
    except (TypeError, ValueError):
        return None
    if sp <= 0 or d <= 0:
        return None
    return round(d * speech_share / sp / 60.0, 1)


__all__ = ["detect", "summary", "recommend", "run_bench", "bench_audio", "estimate_minutes",
           "preload_cuda_libs"]
