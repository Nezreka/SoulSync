"""Sample Studio — stem separation (Phase 4).

Splits a track into drums / vocals / bass / other using Demucs v4 (hybrid
transformer), the best-sounding free separation model. On-request per track:
the user taps "Separate stems", a background job runs for a few minutes, and
the four stems are cached per track forever.

License note (read before redistributing)
-----------------------------------------
The Demucs *code* is MIT. The pretrained *weights* are NOT: Meta's author
states they are "provided only for scientific purposes"
(facebookresearch/demucs#327) and they carry a non-commercial restriction
from the MUSDB training data. That is compatible with SoulSync, which is free
forever and non-commercial — but it means SoulSync must NEVER bundle the
weights. Instead, on first use the user's own server downloads htdemucs
(~80 MB) directly from Meta's public hosting into the data dir, where it is
verified and cached. The user, not SoulSync, performs the download.

Backend selection
-----------------
* ``DemucsSeparator`` — the real thing. Requires torch + demucs installed;
  NOT installable in this dev VM (CUDA-only torch wheel, no cp312 CPU
  torchaudio), so this path is Docker-verified later. Recommended lighter
  alternative for the image: onnxruntime + an ONNX-exported htdemucs
  (see module notes below) instead of the full torch stack.
* ``StubSeparator`` — test/CI stand-in. Writes four copies of the source so
  the whole pipeline (worker, API, UI) is exercisable without torch.

Recommended Docker story (documented here so the image work is mechanical):
``pip install onnxruntime`` + download the ONNX htdemucs export once, OR pin
a CPU torch pair (torch + torchaudio from the CPU index) with demucs. The
full torch stack is ~2 GB installed; onnxruntime is ~100 MB.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from typing import Dict, Optional, Protocol

from utils.logging_config import get_logger

logger = get_logger("sample.stems")

STEMS = ("drums", "vocals", "bass", "other")
SEPARATOR_VERSION = 1

# every way to split a track, and the outputs each one makes. rough outputs
# get their own slugs so they can sit next to real stems in the same cache.
METHOD_STEMS = {
    "demucs": STEMS,
    "rough-drums": ("drums-rough", "music-rough"),
    "rough-center": ("center-rough",),
}
SEPARATION_METHODS = tuple(METHOD_STEMS)
ALL_STEMS = tuple(s for outs in METHOD_STEMS.values() for s in outs)
STEM_LABELS = {
    "drums": "Drums",
    "vocals": "Vocals",
    "bass": "Bass",
    "other": "Other",
    "drums-rough": "Drums (rough)",
    "music-rough": "Music (rough)",
    "center-rough": "Center (rough)",
}


def method_for_stem(stem: str) -> Optional[str]:
    for method, outs in METHOD_STEMS.items():
        if stem in outs:
            return method
    return None


MODEL_NAME = "htdemucs"
# the real htdemucs checkpoint (demucs/remote/files.txt + htdemucs.yaml). the
# part after the dash is the first 8 hex of its sha256, same check torch.hub
# does. the old name here didn't exist on any server, so the download 403'd
# and demucs could never run.
MODEL_FILENAME = "955717e8-8726e21a.th"
MODEL_SHA256_PREFIX = "8726e21a"
MODEL_URLS = (
    f"https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/{MODEL_FILENAME}",
)
_MIN_MODEL_BYTES = 10_000_000


class SeparatorBackend(Protocol):
    """Anything that can split a track file into stems."""

    name: str

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        """Write one WAV per output into out_dir. Returns {stem: file_path}."""
        ...


def models_dir() -> str:
    """Directory for downloaded separator models (data volume, never the repo)."""
    from .store import sample_data_dir

    d = os.path.join(sample_data_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def model_path() -> str:
    # torch.hub.set_dir(models_dir()) makes demucs look in <dir>/checkpoints,
    # so downloading straight there means demucs finds it and never fetches
    # its own second copy.
    d = os.path.join(models_dir(), "checkpoints")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, MODEL_FILENAME)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _model_ok(path: str) -> bool:
    return (
        os.path.isfile(path)
        and os.path.getsize(path) > _MIN_MODEL_BYTES
        and _sha256_file(path).startswith(MODEL_SHA256_PREFIX)
    )


_model_verified = False


def ensure_model() -> str:
    """Download htdemucs on first use and check its hash. Returns the path.

    Raises RuntimeError when the download fails or the file is bad.
    """
    global _model_verified
    path = model_path()
    if os.path.isfile(path):
        if _model_verified:
            return path
        if _model_ok(path):
            _model_verified = True
            return path
        raise RuntimeError("htdemucs model failed its checksum, delete it and retry the download")

    import urllib.request

    last_error: Optional[Exception] = None
    tmp = path + ".download"
    for url in MODEL_URLS:
        try:
            logger.info("Downloading htdemucs model from %s", url)
            req = urllib.request.Request(url, headers={"User-Agent": "SoulSync/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
            if not _model_ok(tmp):
                raise RuntimeError("download failed its checksum")
            os.replace(tmp, path)
            _model_verified = True
            logger.info("htdemucs model cached at %s", path)
            return path
        except Exception as exc:  # noqa: BLE001 — try the next mirror
            last_error = exc
            logger.warning("htdemucs download failed from %s: %s", url, exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass
    raise RuntimeError(f"could not download the htdemucs model: {last_error}")


class StubSeparator:
    """Test/CI separator — four copies of the source, clearly labeled a stub.

    Lets the worker, API, and UI be exercised end-to-end without torch.
    Never used in production unless explicitly selected.
    """

    name = "stub"

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        from .render import decode_stereo, _load_soundfile

        os.makedirs(out_dir, exist_ok=True)
        audio, sr = decode_stereo(track_path)  # (n, channels) float32
        sf = _load_soundfile()
        paths: Dict[str, str] = {}
        for stem in STEMS:
            out = os.path.join(out_dir, f"{stem}.wav")
            sf.write(out, audio, sr, subtype="PCM_16")
            paths[stem] = out
        logger.info("StubSeparator wrote 4 stems for %s", track_path)
        return paths


_demucs_lock = threading.Lock()
_demucs_model = None


def _load_demucs_model():
    """one model per process. loading it takes seconds, so jobs share it."""
    global _demucs_model
    with _demucs_lock:
        if _demucs_model is not None:
            return _demucs_model
        # import first: no point downloading 84 MB for a server that can't run it
        try:
            import torch
            from demucs.pretrained import get_model
        except ImportError as exc:
            raise ImportError(
                "Demucs needs torch + torchaudio + demucs installed "
                "(see the setup note in Sample Studio)"
            ) from exc
        ensure_model()
        torch.hub.set_dir(models_dir())
        model = get_model(name=MODEL_NAME)
        model.cpu()
        model.eval()
        _demucs_model = model
        return model


class DemucsSeparator:
    """Real separator — Demucs v4 hybrid transformer on CPU.

    Requires torch + demucs + torchaudio installed (the Docker image's job,
    not this module's). Model weights are downloaded on first use via
    ensure_model() — never bundled.
    """

    name = "demucs-htdemucs"

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        import numpy as np
        import torch
        import torchaudio
        from demucs.apply import apply_model

        from .render import decode_stereo, _load_soundfile

        model = _load_demucs_model()
        os.makedirs(out_dir, exist_ok=True)
        # decode with our own reader (soundfile, ffmpeg fallback) so every
        # format the library holds works, not just what torchaudio can open
        audio, sr = decode_stereo(track_path)
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)  # htdemucs wants stereo
        elif audio.shape[1] > 2:
            audio = audio[:, :2]
        wav = torch.from_numpy(np.ascontiguousarray(audio.T))
        if sr != model.samplerate:
            wav = torchaudio.functional.resample(wav, sr, model.samplerate)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            sources = apply_model(model, wav[None], device="cpu")[0]
        # undo the input normalization, same as demucs' own separate.py.
        # without it every stem comes out at unit variance and clips.
        sources = sources * ref.std() + ref.mean()
        sf = _load_soundfile()
        paths: Dict[str, str] = {}
        for source, stem in zip(sources, model.sources, strict=True):
            name = stem if stem in STEMS else "other"
            out = os.path.join(out_dir, f"{name}.wav")
            sf.write(out, np.clip(source.cpu().numpy().T, -1.0, 1.0), model.samplerate,
                     subtype="PCM_16")
            paths[name] = out
        logger.info("Demucs separated %s -> %s", track_path, out_dir)
        return paths


def get_backend(name: Optional[str] = None) -> SeparatorBackend:
    """Demucs backend by name: 'demucs' (real) or anything else -> stub.

    the stub is for tests only. the api refuses demucs when torch isn't
    installed instead of quietly handing out four copies of the track.
    """
    if (name or "demucs") == "demucs":
        return DemucsSeparator()
    return StubSeparator()


def get_separator(method: str, backend: Optional[str] = None) -> SeparatorBackend:
    """the separator for a method. `backend` only matters for demucs ('stub')."""
    if method == "demucs":
        return get_backend(backend)
    from .rough import RoughCenterSeparator, RoughDrumsSeparator

    if method == "rough-drums":
        return RoughDrumsSeparator()
    if method == "rough-center":
        return RoughCenterSeparator()
    raise ValueError(f"unknown separation method {method!r}")


def separate_track(
    track_id: int,
    backend: Optional[SeparatorBackend] = None,
    method: str = "demucs",
) -> Dict[str, str]:
    """Split a library track with `method`. Returns {stem: file_path}.

    Raises on any failure — the worker records it as the job status.
    """
    from . import store
    from .worker import resolve_track_file, unreachable_message

    stored = store.get_track_file_path(track_id)
    if not stored:
        raise RuntimeError(f"unknown track_id {track_id}")
    path = resolve_track_file(stored)
    if not path:
        raise RuntimeError(unreachable_message(stored))
    backend = backend or get_separator(method)
    out_dir = os.path.join(store.stems_dir(), str(int(track_id)))
    os.makedirs(out_dir, exist_ok=True)
    paths = backend.separate(path, out_dir)
    wanted = METHOD_STEMS[method]
    missing = [s for s in wanted if not paths.get(s) or not os.path.isfile(paths[s])]
    if missing:
        raise RuntimeError(f"separator did not produce stems: {missing}")
    store.save_stems(
        track_id, {s: paths[s] for s in wanted}, backend.name,
        method=method, source_sig=store.source_signature(path),
    )
    return {s: paths[s] for s in wanted}


def stems_available() -> bool:
    """True when the real Demucs separator can run.

    Checks every heavy import the separator needs (torch, torchaudio,
    demucs) — a partial install (e.g. demucs without torchaudio) still
    fails at separation time, so it must not count as available.
    Never raises.
    """
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
        import demucs  # noqa: F401

        return True
    except ImportError:
        return False


def _default_backend_name() -> str:
    """'demucs' when the full torch stack is importable, else 'stub'."""
    return "demucs" if stems_available() else "stub"
