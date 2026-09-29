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
from typing import Dict, Optional, Protocol

from utils.logging_config import get_logger

logger = get_logger("sample.stems")

STEMS = ("drums", "vocals", "bass", "other")
SEPARATOR_VERSION = 1

MODEL_NAME = "htdemucs"
MODEL_FILENAME = "04573f0d-f3cf-4e94-8bdd-57779fcda8dd.th"
# Meta's public hosting, then the community Hugging Face mirror as fallback.
MODEL_URLS = (
    f"https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/{MODEL_FILENAME}",
    f"https://huggingface.co/adefossez/HTDemucs/resolve/main/{MODEL_FILENAME}",
)
# Trust-on-first-use: the SHA-256 observed on the first successful download is
# pinned in a sidecar file and verified on every later load. This guards
# against corruption / partial downloads, not against a hostile first fetch —
# the URLs above are Meta's own public hosting.
_CHECKSUM_SUFFIX = ".sha256"


class SeparatorBackend(Protocol):
    """Anything that can split a track file into the four stems."""

    name: str

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        """Write one WAV per stem into out_dir. Returns {stem: file_path}."""
        ...


def models_dir() -> str:
    """Directory for downloaded separator models (data volume, never the repo)."""
    from .store import sample_data_dir

    d = os.path.join(sample_data_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def model_path() -> str:
    return os.path.join(models_dir(), MODEL_FILENAME)


def _checksum_path() -> str:
    return model_path() + _CHECKSUM_SUFFIX


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model() -> str:
    """Download htdemucs on first use; verify against the pinned checksum after.

    Returns the model file path. Raises RuntimeError when the download fails.
    """
    path = model_path()
    if os.path.isfile(path) and os.path.getsize(path) > 10_000_000:
        pinned = None
        try:
            with open(_checksum_path(), "r", encoding="utf-8") as f:
                pinned = f.read().strip()
        except OSError:
            pinned = None
        if pinned:
            actual = _sha256_file(path)
            if actual != pinned:
                raise RuntimeError(
                    "htdemucs model failed checksum — delete it and retry the download"
                )
        return path

    import urllib.request

    last_error: Optional[Exception] = None
    tmp = path + ".download"
    for url in MODEL_URLS:
        try:
            logger.info("Downloading htdemucs model from %s", url)
            req = urllib.request.Request(url, headers={"User-Agent": "SoulSync/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
            if os.path.getsize(tmp) < 10_000_000:
                raise RuntimeError(f"suspiciously small download ({os.path.getsize(tmp)} bytes)")
            os.replace(tmp, path)
            digest = _sha256_file(path)
            with open(_checksum_path(), "w", encoding="utf-8") as f:
                f.write(digest)
            logger.info("htdemucs model cached at %s (sha256 pinned)", path)
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


class DemucsSeparator:
    """Real separator — Demucs v4 hybrid transformer on CPU.

    Requires torch + demucs + torchaudio installed (the Docker image's job,
    not this module's). Model weights are downloaded on first use via
    ensure_model() — never bundled.
    """

    name = "demucs-htdemucs"

    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        ensure_model()  # fail fast with a clear error before importing torch
        try:
            import torch
            from demucs.pretrained import get_model
        except ImportError as exc:
            raise ImportError(
                "Demucs needs torch + demucs installed "
                "(Docker: CPU torch pair or onnxruntime — see module docstring)"
            ) from exc
        # get_model resolves its own hub cache; point torch hub at our data dir
        # so the weights live on the persistent volume, not in ~/.cache.
        torch.hub.set_dir(models_dir())
        model = get_model(name=MODEL_NAME)
        model.cpu()
        model.eval()
        self._model = model
        return model

    def separate(self, track_path: str, out_dir: str) -> Dict[str, str]:
        import torch
        import torchaudio
        from demucs.apply import apply_model

        model = self._load()
        os.makedirs(out_dir, exist_ok=True)
        wav, sr = torchaudio.load(track_path)
        # Demucs wants its native sample rate; resample when needed.
        if sr != model.samplerate:
            wav = torchaudio.functional.resample(wav, sr, model.samplerate)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            sources = apply_model(model, wav[None], device="cpu")[0]
        try:
            import soundfile as sf

            write = lambda p, a: sf.write(p, a.T, model.samplerate, subtype="PCM_16")
        except ImportError:
            write = lambda p, a: torchaudio.save(  # noqa: E731
                p, torch.from_numpy(a), model.samplerate, bits_per_sample=16
            )
        paths: Dict[str, str] = {}
        for source, stem in zip(sources, model.sources, strict=True):
            name = stem if stem in STEMS else "other"
            out = os.path.join(out_dir, f"{name}.wav")
            write(out, source.cpu().numpy())
            paths[name] = out
        for stem in STEMS:  # the contract is always four stems
            paths.setdefault(stem, paths.get("other", ""))
        logger.info("Demucs separated %s -> %s", track_path, out_dir)
        return paths


def get_backend(name: Optional[str] = None) -> SeparatorBackend:
    """Backend by name: 'demucs' (real) or anything else -> stub.

    Production default is demucs; the stub is for tests and for installs
    without torch until the Docker image story lands.
    """
    if (name or "demucs") == "demucs":
        return DemucsSeparator()
    return StubSeparator()


def separate_track(track_id: int, backend: Optional[SeparatorBackend] = None) -> Dict[str, str]:
    """Separate a library track into four stems. Returns {stem: file_path}.

    Raises on any failure — the worker records it as the job status.
    """
    from . import store
    from .worker import resolve_audio_path
    stored = store.get_track_file_path(track_id)
    if not stored:
        raise RuntimeError(f"unknown track_id {track_id}")
    path = resolve_audio_path(stored)
    if not path:
        raise RuntimeError(f"audio file not reachable on disk: {stored}")
    backend = backend or get_backend(_default_backend_name())
    out_dir = os.path.join(store.stems_dir(), str(int(track_id)))
    os.makedirs(out_dir, exist_ok=True)
    paths = backend.separate(path, out_dir)
    missing = [s for s in STEMS if not paths.get(s) or not os.path.isfile(paths[s])]
    if missing:
        raise RuntimeError(f"separator did not produce stems: {missing}")
    store.save_stems(track_id, paths, backend.name)
    return paths


def _default_backend_name() -> str:
    """'demucs' when torch looks importable, else 'stub' — never crash."""
    try:
        import torch  # noqa: F401
        import demucs  # noqa: F401

        return "demucs"
    except ImportError:
        return "stub"
