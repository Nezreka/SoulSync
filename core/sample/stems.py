"""Sample Studio — stem separation.

Splits a track into drums / vocals / bass / other with htdemucs (Demucs v4,
hybrid transformer), the best free separation model. On request per track:
the user taps "Separate stems", a background job runs for a few minutes, and
the four stems are cached per track.

runs on onnxruntime, not torch. same htdemucs weights exported to ONNX
(StemSplitio/htdemucs-onnx, MIT), so it needs a ~20 MB pip package instead
of a ~2 GB torch install, and it's in the docker image by default.

memory: onnxruntime's graph optimizations turn the fp16 weights into big
fp32 copies and push one 7.8s chunk past 4 GB. with them off (and the memory
arena off) a whole song peaks around 1-1.5 GB at about realtime on CPU, so
that's how the session is built. the session is dropped after every job so
nothing stays resident between separations.

weights license: the demucs weights carry meta's non-commercial note. fine
for SoulSync (free, non-commercial), but they're never bundled. each server
downloads the model itself on first use, pinned to one commit and checked
against its sha256.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import Callable, Dict, Optional, Protocol

from utils.logging_config import get_logger

logger = get_logger("sample.stems")

STEMS = ("drums", "vocals", "bass", "other")
SEPARATOR_VERSION = 1

# every way to split a track, and the outputs each one makes. just demucs:
# the built-in rough splits (hpss, mid/side) sounded bad and were pulled.
METHOD_STEMS = {
    "demucs": STEMS,
}
SEPARATION_METHODS = tuple(METHOD_STEMS)
ALL_STEMS = tuple(s for outs in METHOD_STEMS.values() for s in outs)
STEM_LABELS = {
    "drums": "Drums",
    "vocals": "Vocals",
    "bass": "Bass",
    "other": "Other",
}


def method_for_stem(stem: str) -> Optional[str]:
    for method, outs in METHOD_STEMS.items():
        if stem in outs:
            return method
    return None


MODEL_NAME = "htdemucs"
MODEL_FILENAME = "htdemucs_fp16weights.onnx"
MODEL_SHA256 = "d05c269d0178d2a72ad484b10b11dd370193fc923201c3b27a99f848745db70a"
_MODEL_COMMIT = "d54ed9eb60e258ea82131c6ee14578628816456a"
MODEL_URLS = (
    f"https://huggingface.co/StemSplitio/htdemucs-onnx/resolve/{_MODEL_COMMIT}/{MODEL_FILENAME}",
)
_MIN_MODEL_BYTES = 10_000_000

# the exported graph takes exactly this: stereo, 44.1k, 7.8s chunks
MODEL_SR = 44100
CHUNK = 343980
MODEL_SOURCES = ("drums", "bass", "other", "vocals")


class SeparatorBackend(Protocol):
    """Anything that can split a track file into stems."""

    name: str

    def separate(self, track_path: str, out_dir: str,
                 progress: Optional[Callable[[float], None]] = None) -> Dict[str, str]:
        """Write one WAV per output into out_dir. Returns {stem: file_path}."""
        ...


def models_dir() -> str:
    """Directory for downloaded separator models (data volume, never the repo)."""
    from .store import sample_data_dir

    d = os.path.join(sample_data_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def model_path() -> str:
    return os.path.join(models_dir(), MODEL_FILENAME)


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
        and _sha256_file(path) == MODEL_SHA256
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

    def separate(self, track_path: str, out_dir: str,
                 progress: Optional[Callable[[float], None]] = None) -> Dict[str, str]:
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


def _threads() -> int:
    # half the cores: a separation shouldn't starve downloads and playback
    return max(1, (os.cpu_count() or 2) // 2)


def _open_session():
    """a fresh onnxruntime session. import first: no point downloading 166 MB
    for a server that can't run it."""
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ImportError("stem separation needs onnxruntime (pip install onnxruntime)") from exc
    path = ensure_model()
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    so.intra_op_num_threads = _threads()
    return ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])


def separate_array(audio, sr: int, run_chunk: Callable, progress: Optional[Callable[[float], None]] = None):
    """htdemucs over a whole track. audio: (samples, channels) float32.

    returns ({stem: (samples, 2) float32}, MODEL_SR). resamples to 44.1k,
    normalizes like demucs' own separate.py (and undoes it after), and runs
    7.8s chunks with a quarter overlap, crossfaded back together.
    `run_chunk` maps a (1, 2, CHUNK) array to (1, 4, 2, CHUNK).
    """
    import numpy as np

    y = np.asarray(audio, dtype=np.float32)
    if y.ndim == 1:
        y = y[:, None]
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)  # htdemucs wants stereo
    elif y.shape[1] > 2:
        y = y[:, :2]
    if sr != MODEL_SR:
        import soxr

        y = soxr.resample(y, sr, MODEL_SR, quality="HQ").astype(np.float32)
    x = np.ascontiguousarray(y.T)  # (2, n)
    ref = x.mean(0)
    mean, std = float(ref.mean()), float(ref.std()) + 1e-8
    x = (x - mean) / std

    total = x.shape[1]
    overlap = CHUNK // 4
    stride = CHUNK - overlap
    n_chunks = max(1, -(-total // stride))
    window = np.ones(CHUNK, dtype=np.float32)
    fade = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
    window[:overlap] = fade
    window[-overlap:] = fade[::-1]
    out = np.zeros((len(MODEL_SOURCES), 2, total), dtype=np.float32)
    weight = np.zeros(total, dtype=np.float32)
    for i in range(n_chunks):
        start = i * stride
        end = min(start + CHUNK, total)
        chunk = x[:, start:end]
        if chunk.shape[1] < CHUNK:
            chunk = np.pad(chunk, ((0, 0), (0, CHUNK - chunk.shape[1])))
        stems = run_chunk(chunk[None].astype(np.float32))[0]  # (4, 2, CHUNK)
        n = end - start
        # only fade where two chunks overlap. the window starts at exactly 0,
        # so fading the first chunk's start would zero the song's opening
        # samples, and the last chunk's end has nothing to blend into
        w = window[:n].copy()
        if i == 0:
            w[:overlap] = 1.0
        if end == total:
            w[overlap:] = 1.0
        out[:, :, start:end] += stems[:, :, :n] * w
        weight[start:end] += w
        if progress:
            progress((i + 1) / n_chunks)
    out /= np.maximum(weight, 1e-8)
    out = out * std + mean
    return {name: out[k].T for k, name in enumerate(MODEL_SOURCES)}, MODEL_SR


class DemucsSeparator:
    """htdemucs on onnxruntime, CPU. model downloaded on first use."""

    name = "htdemucs-onnx"

    def separate(self, track_path: str, out_dir: str,
                 progress: Optional[Callable[[float], None]] = None) -> Dict[str, str]:
        import numpy as np

        from .render import decode_stereo, _load_soundfile

        # a local session (~1 GB) goes away when this returns, nothing stays
        # resident between separations
        session = _open_session()
        os.makedirs(out_dir, exist_ok=True)
        audio, sr = decode_stereo(track_path)
        stems, out_sr = separate_array(
            audio, sr, lambda c: session.run(["stems"], {"mix": c})[0], progress,
        )
        sf = _load_soundfile()
        paths: Dict[str, str] = {}
        for name in STEMS:
            out = os.path.join(out_dir, f"{name}.wav")
            sf.write(out, np.clip(stems[name], -1.0, 1.0), out_sr, subtype="PCM_16")
            paths[name] = out
        logger.info("htdemucs separated %s -> %s", track_path, out_dir)
        return paths


def get_backend(name: Optional[str] = None) -> SeparatorBackend:
    """Demucs backend by name: 'demucs' (real) or anything else -> stub.

    the stub is for tests only. the api refuses demucs when onnxruntime isn't
    installed instead of quietly handing out four copies of the track.
    """
    if (name or "demucs") == "demucs":
        return DemucsSeparator()
    return StubSeparator()


def get_separator(method: str, backend: Optional[str] = None) -> SeparatorBackend:
    """the separator for a method. `backend` only matters for demucs ('stub')."""
    if method == "demucs":
        return get_backend(backend)
    raise ValueError(f"unknown separation method {method!r}")


def separate_track(
    track_id: int,
    backend: Optional[SeparatorBackend] = None,
    method: str = "demucs",
    progress: Optional[Callable[[float], None]] = None,
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
    try:
        paths = backend.separate(path, out_dir, progress=progress)
    except TypeError:  # a separator that predates progress reporting
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
    """True when the separator can run: onnxruntime and soxr importable.
    Never raises."""
    try:
        import onnxruntime  # noqa: F401
        import soxr  # noqa: F401

        return True
    except ImportError:
        return False


def _default_backend_name() -> str:
    """'demucs' when onnxruntime is importable, else 'stub'."""
    return "demucs" if stems_available() else "stub"
