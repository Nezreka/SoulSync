"""Whisper models: the lineup, downloads, and the settings status.

Ai subtitles: when every network provider has missed for long enough (or
someone asks for them in the player or on a title's page), the video's own
audio is transcribed on this server. The run itself lives in
``core.video.subtitles.ai`` (its own queue, off the wanted worker's cycle);
this module owns the models. Several can be installed at once (tiny for
quick jobs, turbo for the best), each on the data volume. Fully optional:
nothing runs unless the user turns it on AND installs a model.

Engine choice: faster-whisper (CTranslate2), NOT openai-whisper. Justification:
  - faster-whisper runs quantized int8 on CPU (~4x faster than openai-whisper
    on CPU) — this is what makes local transcription practical on a NAS/box
    without a GPU. A 45-minute episode transcribes in ~10-20 minutes instead
    of over an hour.
  - No PyTorch dependency (openai-whisper drags in torch, ~2GB) — CTranslate2
    is ~30MB.
  - openai-whisper reads its own .pt checkpoints; faster-whisper reads
    CTranslate2 model dirs (model.bin). The two formats are incompatible, so
    supporting both engines would mean two download formats, two size tables,
    and engine↔artifact matching logic — double the download complexity for an
    engine that is slower and heavier. One format, one preferred engine.
  - If openai-whisper-only support is ever wanted, the honest way is a second
    artifact entry per model (the .pt snapshot) chosen by installed engine —
    not silently mis-loading one format into the other.

The dependency is OPTIONAL: SoulSync installs and runs fine without
faster-whisper. It is imported lazily inside the transcribe path only; every
other function in this module (registry, paths, download, status) is stdlib.

Model storage: <data>/whisper_models/<name>/ — beside the video DB on the
persisted data volume (same convention as overlays' AssetStore), so models
survive restarts and container/image updates. <data> is derived from
VIDEO_DATABASE_PATH exactly like AssetStore.default_root().

Download discipline (hard rules):
  - Download ONLY via start_download(), which is called ONLY by the
    POST /api/video/subtitles/whisper/download endpoint (the settings UI's
    explicit button). No import-time, startup-time, install-time, or
    transcribe-time downloading — WhisperModel is always handed a local path,
    never a bare model name (faster-whisper would auto-download otherwise).
  - Corrupt-download safety: every file downloads to a .part temp file, its
    size is verified against the HuggingFace tree listing, then it is
    atomically renamed (os.replace) into place. is_configured() only trusts a
    fully-present model.bin — a half-download never reads as "downloaded".

Isolated: stdlib only at import time; no music imports.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from utils.logging_config import get_logger

from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery

logger = get_logger("video.subtitles.whisper")

_UA = "SoulSync/1.0 (whisper-model-download)"

#: Model name → HuggingFace repo + expected total snapshot bytes (measured
#: Oct 2026 via the HF tree API; shown in the settings UI). THE allowlist:
#: model names are validated against these keys before they ever reach a URL
#: or a filesystem path — never interpolate raw user input into either.
WHISPER_MODELS: dict[str, dict[str, Any]] = {
    "tiny": {
        "repo": "Systran/faster-whisper-tiny",
        "size_bytes": 78207087,
        "ram_bytes": 300_000_000,
        "label": "tiny",
        "blurb": "~78 MB · fastest, roughest. good for a raspberry pi",
    },
    "base": {
        "repo": "Systran/faster-whisper-base",
        "size_bytes": 147886409,
        "ram_bytes": 400_000_000,
        "label": "base",
        "blurb": "~148 MB · quick, fine for clear english",
    },
    "small": {
        "repo": "Systran/faster-whisper-small",
        "size_bytes": 486215847,
        "ram_bytes": 1_000_000_000,
        "label": "small",
        "blurb": "~486 MB · the usual pick for a server without a graphics card",
    },
    "medium": {
        "repo": "Systran/faster-whisper-medium",
        "size_bytes": 1530575217,
        "ram_bytes": 2_500_000_000,
        "label": "medium",
        "blurb": "~1.5 GB · better on accents and other languages, needs a strong CPU",
    },
    "large-v3-turbo": {
        "repo": "dropbox-dash/faster-whisper-large-v3-turbo",
        "size_bytes": 1621668947,
        "ram_bytes": 2_500_000_000,
        "label": "large-v3-turbo",
        "blurb": "~1.6 GB · the best, and fast on an NVIDIA graphics card",
    },
    "distil-large-v3": {
        "repo": "Systran/faster-distil-whisper-large-v3",
        "size_bytes": 1516482645,
        "ram_bytes": 2_500_000_000,
        "label": "distil-large-v3",
        "blurb": "~1.5 GB · english only, quick on a graphics card",
        "english_only": True,
    },
}

#: Default model (the settings default too).
DEFAULT_WHISPER_MODEL = "small"

#: The CTranslate2 model file faster-whisper loads. A fully-downloaded model
#: is <models>/<name>/model.bin (+ tokenizer/config siblings).
MODEL_BIN = "model.bin"


# ── model-name validation + paths ────────────────────────────────────────────

def valid_model_name(name: Any) -> str | None:
    """The allowlisted model name for ``name``, or None. Case-insensitive,
    whitespace-tolerant; anything else (including path separators) is None.
    Pure — the path-traversal gate for every URL/path built below."""
    s = str(name or "").strip().lower()
    return s if s in WHISPER_MODELS else None


def default_models_dir() -> Path:
    """<data>/whisper_models — beside the video DB on the persisted volume,
    mirroring overlays' AssetStore.default_root()."""
    db = os.environ.get("VIDEO_DATABASE_PATH", "database/video_library.db")
    return Path(db).resolve().parent / "whisper_models"


_models_dir_override: Path | None = None


def set_models_dir_override(path: Any | None) -> None:
    """Test seam: force models_dir() to ``path`` (None clears it)."""
    global _models_dir_override
    _models_dir_override = Path(path) if path else None


def models_dir() -> Path:
    """The models root (override-aware)."""
    if _models_dir_override is not None:
        return _models_dir_override
    return default_models_dir()


def model_dir(name: str) -> Path:
    """<models>/<name>/ — raises ValueError on a non-allowlisted name."""
    valid = valid_model_name(name)
    if valid is None:
        raise ValueError("unknown whisper model: %r" % (name,))
    return models_dir() / valid


def model_file_path(name: str) -> Path:
    """The model.bin path is_configured() trusts. Raises on bad names."""
    return model_dir(name) / MODEL_BIN


def model_file_exists(name: Any) -> bool:
    """True when the allowlisted model's model.bin is fully on disk."""
    try:
        return model_file_path(name).is_file()
    except ValueError:
        return False


# ── repo snapshot download (stdlib urllib; no huggingface_hub needed) ─────────

def _repo_tree(repo: str) -> list[tuple[str, int]]:
    """[(path, size_bytes)] for every file in the repo's main branch, via the
    public HuggingFace API. ``repo`` comes from WHISPER_MODELS only — never
    user input. Raises on network/API failure. (Seam: monkeypatch in tests.)"""
    url = "https://huggingface.co/api/models/%s/tree/main" % repo
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        entries = json.loads(r.read().decode("utf-8"))
    out: list[tuple[str, int]] = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or e.get("type") != "file":
            continue
        path = str(e.get("path") or "")
        try:
            size = int(e.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if path and size > 0:
            out.append((path, size))
    return out


def _safe_target(root: Path, member: str) -> Path:
    """The on-disk target for a repo member, or raises. Defense in depth:
    even though the listing comes from HuggingFace, a member must never
    escape the model dir (``..`` / absolute paths fail closed)."""
    parts = Path(member).parts
    if not parts or Path(member).is_absolute() or ".." in parts:
        raise RuntimeError("unsafe repo member path: %r" % (member,))
    target = root.joinpath(*parts)
    if root not in target.resolve().parents and target.resolve() != root:
        # resolve() follows symlinks; the target must stay inside root.
        raise RuntimeError("repo member escapes model dir: %r" % (member,))
    return target


class DownloadCancelled(RuntimeError):
    """the settings page's cancel button."""


_cancel_download = threading.Event()


def _download_one(url: str, tmp_path: Path, expected_size: int,
                  on_chunk: Callable[[int], None] | None = None) -> None:
    """Stream ``url`` to ``tmp_path``; raise unless the byte count matches.
    (Seam: monkeypatch in tests.)"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp_path, "wb") as f:
        while True:
            if _cancel_download.is_set():
                raise DownloadCancelled("download cancelled")
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            if callable(on_chunk):
                on_chunk(len(chunk))
    actual = os.path.getsize(tmp_path)
    if actual != expected_size:
        raise RuntimeError("size mismatch for %s: got %d bytes, expected %d"
                           % (tmp_path.name, actual, expected_size))


def download_model(name: Any,
                   on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Download the full faster-whisper repo snapshot for ``name``.

    Idempotent/resumable: files already present with the right size are
    skipped. Each file goes to a ``.part`` temp file, is size-verified, then
    atomically renamed — a killed download never leaves a half-model that
    is_configured() would trust.

    Returns {"name", "path", "size_bytes", "downloaded_at"}. Raises
    ValueError (bad name), RuntimeError (network/size/unsafe-path), OSError
    (disk). Called ONLY from the download worker thread (see
    start_download) — never from a request or monitor thread.
    """
    valid = valid_model_name(name)
    if valid is None:
        raise ValueError("unknown whisper model: %r" % (name,))
    repo = WHISPER_MODELS[valid]["repo"]
    members = _repo_tree(repo)
    if not members:
        raise RuntimeError("empty file listing for %s" % repo)
    total = sum(size for _, size in members)
    dest = model_dir(valid)
    dest.mkdir(parents=True, exist_ok=True)
    # Stale .part files from a killed download must not accumulate — and must
    # never be mistaken for real files (is_configured only trusts model.bin,
    # but tidy anyway).
    for stale in dest.glob("*.part"):
        try:
            stale.unlink()
        except OSError:
            pass
    done = 0
    if callable(on_progress):
        try:
            on_progress(0, total)
        except Exception:  # noqa: BLE001 - progress is cosmetic
            pass

    def _chunk(n: int) -> None:
        nonlocal done
        done += n
        if callable(on_progress):
            try:
                on_progress(done, total)
            except Exception:  # noqa: BLE001 - progress is cosmetic
                pass

    for member, size in members:
        if _cancel_download.is_set():
            raise DownloadCancelled("download cancelled")
        target = _safe_target(dest, member)
        if target.is_file():
            try:
                if target.stat().st_size == size:
                    done += size
                    if callable(on_progress):
                        try:
                            on_progress(done, total)
                        except Exception:  # noqa: BLE001
                            pass
                    continue
            except OSError:
                pass
        tmp = target.with_name(target.name + ".part")
        try:
            _download_one(
                "https://huggingface.co/%s/resolve/main/%s" % (repo, member),
                tmp, size, on_chunk=_chunk)
            os.replace(tmp, target)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
    # The download is only "done" when model.bin itself verified into place —
    # the tokenizer/config siblings are useless without it and vice versa.
    if not model_file_exists(valid):
        raise RuntimeError("download finished but %s is missing" % MODEL_BIN)
    return {
        "name": valid,
        "path": str(model_file_path(valid)),
        "size_bytes": total,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }


# ── download worker state (one download at a time, daemon thread) ─────────────

_download_lock = threading.Lock()
_download_state: dict[str, Any] = {
    "state": "idle",          # idle | downloading | done | error
    "model": None,
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "error": None,
}


def get_download_status() -> dict:
    """A copy of the current download state. Never raises."""
    with _download_lock:
        return dict(_download_state)


def _public_download_status() -> dict:
    """Download state safe for the open status endpoint: the raw ``error``
    string can embed server filesystem paths, so non-admin callers get a
    generic message instead."""
    st = get_download_status()
    err = st.get("error")
    if err:
        st = dict(st)
        st["error"] = "download failed — check the server log for details"
    return st


def _set_download_state(**kw) -> None:
    with _download_lock:
        _download_state.update(kw)


def installed_models() -> list[str]:
    """every allowlisted model fully on disk, in lineup order."""
    return [n for n in WHISPER_MODELS if model_file_exists(n)]


def model_disk_bytes(name: str) -> int:
    """bytes the model's folder takes. 0 when it isn't there."""
    try:
        d = model_dir(name)
    except ValueError:
        return 0
    total = 0
    for p in d.rglob("*") if d.is_dir() else []:
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def delete_model(name: Any) -> bool:
    """remove one installed model. only allowlisted names; refuses while that
    model is downloading. True when something was removed."""
    valid = valid_model_name(name)
    if valid is None:
        raise ValueError("unknown whisper model: %r" % (name,))
    st = get_download_status()
    if st.get("state") == "downloading" and st.get("model") == valid:
        raise RuntimeError("that model is downloading; cancel it first")
    d = model_dir(valid)
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    logger.info("whisper: removed model %s", valid)
    return True


def cancel_download() -> bool:
    """stop the running download after its current block. True when one was
    running."""
    with _download_lock:
        running = _download_state["state"] == "downloading"
    if running:
        _cancel_download.set()
    return running


def start_download(name: Any,
                   on_complete: Callable[[dict | None, Exception | None], None] | None = None
                   ) -> tuple[bool, str | None]:
    """Begin downloading ``name`` on a daemon thread. (True, None) on start;
    (False, reason) when the name is invalid or a download is already running.

    ONLY the POST /api/video/subtitles/whisper/download endpoint calls this —
    it is the single code path that downloads a model. ``on_complete`` runs on
    the worker thread with (info, None) on success or (None, exc) on failure;
    it must never raise (guarded).
    """
    valid = valid_model_name(name)
    if valid is None:
        return False, "unknown model: %r (choose one of: %s)" % (
            name, ", ".join(sorted(WHISPER_MODELS)))
    with _download_lock:
        if _download_state["state"] == "downloading":
            return False, "a model download is already in progress"
        _download_state.update(state="downloading", model=valid,
                               downloaded_bytes=0, total_bytes=WHISPER_MODELS[valid]["size_bytes"],
                               error=None)
        _cancel_download.clear()

    def _progress(done_bytes: int, total_bytes: int) -> None:
        _set_download_state(downloaded_bytes=done_bytes, total_bytes=total_bytes)

    def _run() -> None:
        try:
            info = download_model(valid, on_progress=_progress)
        except DownloadCancelled as exc:
            logger.info("whisper: download of %s cancelled", valid)
            if callable(on_complete):
                try:
                    on_complete(None, exc)
                except Exception:  # noqa: BLE001
                    logger.exception("whisper on_complete raised (cancel path)")
            _set_download_state(state="cancelled", error=None)
            return
        except Exception as exc:  # noqa: BLE001 - download is best-effort
            logger.exception("whisper model download failed for %s", valid)
            if callable(on_complete):
                try:
                    on_complete(None, exc)
                except Exception:  # noqa: BLE001
                    logger.exception("whisper on_complete raised (failure path)")
            # State flips AFTER the callback: a "done"/"error" status must
            # mean the persisted settings already reflect it — otherwise a
            # poller seeing "done" can read a stale blob.
            _set_download_state(state="error", error=str(exc))
            return
        if callable(on_complete):
            try:
                on_complete(info, None)
            except Exception:  # noqa: BLE001
                logger.exception("whisper on_complete raised (success path)")
        _set_download_state(state="done")

    threading.Thread(target=_run, daemon=True,
                     name="whisper-model-download").start()
    return True, None


# ── transcription (lazy faster-whisper import — optional dependency) ──────────

def transcription_engine() -> str | None:
    """"faster-whisper" when importable, else None. Uses find_spec — no import
    side effects, cheap enough to call on every is_configured()."""
    try:
        import importlib.util
        if importlib.util.find_spec("faster_whisper") is not None:
            return "faster-whisper"
    except Exception:  # noqa: BLE001
        pass
    return None


def _srt_timestamp(seconds: float) -> str:
    try:
        total_ms = max(0, int(round(float(seconds) * 1000)))
    except (TypeError, ValueError):
        total_ms = 0
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def segments_to_srt(segments: Any) -> str:
    """faster-whisper segments → SRT text. Pure (segments are duck-typed)."""
    out: list[str] = []
    n = 0
    for seg in segments or []:
        try:
            text = str(getattr(seg, "text", "") or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        n += 1
        out.append(str(n))
        out.append("%s --> %s" % (_srt_timestamp(getattr(seg, "start", 0)),
                                  _srt_timestamp(getattr(seg, "end", 0))))
        out.append(text)
        out.append("")
    return "\n".join(out).strip() + ("\n" if out else "")


def transcribe_to_srt(name: Any, file_path: str, language: str | None = None) -> str | None:
    """Transcribe ``file_path`` to SRT with model ``name``, in one go.

    The ai queue (``core.video.subtitles.ai.queue``) is how SoulSync makes
    subtitles: it is resumable, can be paused and cancelled, and shows
    progress. This is the same run with nobody watching it, kept for
    scripts. Returns None when the engine isn't installed or the audio can't
    make subtitles in ``language``.
    """
    valid = valid_model_name(name)
    if valid is None:
        raise ValueError("unknown whisper model: %r" % (name,))
    if transcription_engine() is None:
        logger.warning("whisper: faster-whisper is not installed; transcription unavailable")
        return None
    model_path = model_file_path(valid)
    if not model_path.is_file():
        raise RuntimeError("whisper model %s is not downloaded" % valid)
    from core.player import transcode as T
    from core.video.subtitles.ai import engine
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is missing")
    job = {"id": "adhoc-%d" % int(time.time() * 1000)}
    try:
        res = engine.run(job, video=str(file_path), model_dir=str(model_path.parent), ffmpeg=ffmpeg,
                         ffprobe=T.ffprobe_for(ffmpeg),
                         want=(str(language or "").strip().lower() or "en"))
    finally:
        engine.cleanup(job["id"])
    return res.get("srt") if res.get("status") == "done" else None


# ── status payload for the settings UI ─────────────────────────────────────────

def status_payload(get_setting: Callable[[str, Any], Any]) -> dict:
    """Everything the settings UI needs. Never raises."""
    def _g(key: str, default: Any) -> Any:
        try:
            return get_setting(key, default)
        except Exception:  # noqa: BLE001
            return default

    name = valid_model_name(_g("whisper_model", DEFAULT_WHISPER_MODEL)) or DEFAULT_WHISPER_MODEL
    enabled = bool(_g("whisper_enabled", False))
    installed = installed_models()
    engine = transcription_engine()
    bench = _g("whisper_bench", {}) or {}
    if isinstance(bench, str):
        try:
            bench = json.loads(bench)
        except ValueError:
            bench = {}
    if not isinstance(bench, dict):
        bench = {}
    try:
        from core.video.subtitles.ai import hardware
        hw = hardware.detect()
        hw_line = hardware.summary(hw)
        recommended = hardware.recommend(hw, bench)
    except Exception:  # noqa: BLE001
        hw, hw_line, recommended = {}, "", DEFAULT_WHISPER_MODEL
    try:
        from core.video.subtitles import autosync
        sync_ok = autosync.ffsubsync_available()
    except Exception:  # noqa: BLE001
        sync_ok = False
    models = {}
    for n, m in WHISPER_MODELS.items():
        on = n in installed
        b = bench.get(n) if isinstance(bench.get(n), dict) else None
        models[n] = {"repo": m["repo"], "size_bytes": m["size_bytes"], "ram_bytes": m.get("ram_bytes"),
                     "label": m["label"], "blurb": m["blurb"],
                     "english_only": bool(m.get("english_only")),
                     "installed": on, "disk_bytes": model_disk_bytes(n) if on else 0,
                     "bench": b}
    return {
        "enabled": enabled,
        "model": name,
        "models": models,
        "installed": installed,
        "downloaded": name in installed,
        "disk_model": installed[0] if installed else None,
        "size_bytes": WHISPER_MODELS[name]["size_bytes"],
        "downloaded_at": str(_g("whisper_model_downloaded_at", "") or ""),
        "engine": engine,
        "engine_hint": None if engine else "the faster-whisper package is missing on the server",
        "sync_engine": "ffsubsync" if sync_ok else None,
        "hardware": {"summary": hw_line, "cuda": bool(hw.get("cuda")), "gpu": hw.get("gpu"),
                     "cores": hw.get("cores"), "memory_bytes": hw.get("memory_bytes"),
                     "arch": hw.get("arch"), "cuda_problem": hw.get("cuda_problem")},
        "recommended": recommended,
        "device": str(_g("whisper_device", "auto") or "auto"),
        "download": _public_download_status(),
    }


# ── the provider ──────────────────────────────────────────────────────────────

class WhisperProvider(SubtitleProvider):
    """Local transcription as the last provider in the chain.

    Double-gated: ``is_configured()`` requires the user to have enabled
    transcription AND the selected model's model.bin to exist on disk — both
    checked every call, never assumed. ``search()`` is cheap (one candidate,
    no work); the minutes-long transcription happens in ``download()``, which
    only ever runs on the background wanted-loop worker thread
    (``background_download_only``) — never in the import hook and never in a
    request thread.
    """

    id = "whisper"
    display_name = "Whisper (local)"

    burns_network_quota = False
    background_download_only = True

    def __init__(self) -> None:
        self._get_setting: Callable[[str, Any], Any] = lambda k, d=None: d
        self._model: str | None = None

    @property
    def needs_key(self) -> bool:
        return False

    def is_configured(self, get_setting: Callable[[str, Any], Any]) -> bool:
        # Gate 1: the user enabled local transcription. Gate 2: the selected
        # model's file is actually on disk (a stale "downloaded" flag with no
        # file — or a half-download — reads as unconfigured). Gate 3: the
        # transcription engine is installed. All three, every call.
        try:
            self._get_setting = get_setting
            self._model = None
            if not bool(get_setting("whisper_enabled", False)):
                return False
            name = valid_model_name(get_setting("whisper_model",
                                                DEFAULT_WHISPER_MODEL))
            if name is None:
                return False
            if transcription_engine() is None:
                return False
            if not model_file_exists(name):
                return False
            self._model = name
            return True
        except Exception:  # noqa: BLE001 - a broken check reads as unconfigured
            self._model = None
            return False

    def search(self, query: SubtitleQuery) -> list[SubtitleCandidate]:
        """nothing: ai subtitles don't run in the provider chain any more.
        the wanted worker queues an ai job (``core.video.subtitles.ai.queue``)
        once the network providers have missed for long enough, so a minutes
        long transcription never holds up every other language's retry."""
        return []

    def download(self, candidate: SubtitleCandidate) -> str | None:
        return None


__all__ = [
    "WHISPER_MODELS", "DEFAULT_WHISPER_MODEL", "MODEL_BIN",
    "valid_model_name", "default_models_dir", "set_models_dir_override",
    "models_dir", "model_dir", "model_file_path", "model_file_exists",
    "download_model", "start_download", "get_download_status",
    "installed_models", "model_disk_bytes", "delete_model", "cancel_download", "DownloadCancelled",
    "transcription_engine", "segments_to_srt", "transcribe_to_srt",
    "status_payload", "WhisperProvider",
]
