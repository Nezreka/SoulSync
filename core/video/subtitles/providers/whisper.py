"""Whisper local transcription ("Replace Bazarr" Phase 4).

The LAST provider in the chain: when every network provider misses (or none is
configured), the video's own audio is transcribed on this server as a final
fallback. Fully optional — nothing here runs unless the user enables it AND
downloads a model via the settings UI.

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
        "label": "tiny",
        "blurb": "~78 MB · fastest, roughest transcription",
    },
    "base": {
        "repo": "Systran/faster-whisper-base",
        "size_bytes": 147886409,
        "label": "base",
        "blurb": "~148 MB · balanced speed/quality",
    },
    "small": {
        "repo": "Systran/faster-whisper-small",
        "size_bytes": 486215847,
        "label": "small",
        "blurb": "~486 MB · slowest, best transcription",
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


def _download_one(url: str, tmp_path: Path, expected_size: int,
                  on_chunk: Callable[[int], None] | None = None) -> None:
    """Stream ``url`` to ``tmp_path``; raise unless the byte count matches.
    (Seam: monkeypatch in tests.)"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp_path, "wb") as f:
        while True:
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


def _remove_other_model_dirs(keep: str) -> None:
    """Delete fully-downloaded OTHER allowlisted model dirs after a successful
    switch — "downloading replaces", per the UX. Only allowlisted names are
    ever removed; anything else under the root is left alone."""
    root = models_dir()
    for name in WHISPER_MODELS:
        if name == keep:
            continue
        d = root / name
        if d.is_dir():
            try:
                shutil.rmtree(d)
                logger.info("whisper: removed replaced model dir %s", d)
            except OSError:
                logger.exception("whisper: could not remove old model dir %s", d)


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
                               downloaded_bytes=0, total_bytes=0, error=None)

    def _progress(done_bytes: int, total_bytes: int) -> None:
        _set_download_state(downloaded_bytes=done_bytes, total_bytes=total_bytes)

    def _run() -> None:
        try:
            info = download_model(valid, on_progress=_progress)
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
        _remove_other_model_dirs(valid)
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
    """Transcribe ``file_path``'s audio to SRT with model ``name``.

    faster-whisper decodes the audio stream itself (no ffmpeg subprocess).
    Raises on a bad model name or missing model.bin; returns None when the
    engine isn't installed. Importing faster_whisper here (not at module top
    level) keeps it an optional dependency — this is the ONLY place it's
    imported.
    """
    valid = valid_model_name(name)
    if valid is None:
        raise ValueError("unknown whisper model: %r" % (name,))
    if transcription_engine() is None:
        logger.warning("whisper: faster-whisper is not installed; "
                       "transcription unavailable (pip install faster-whisper)")
        return None
    model_path = model_file_path(valid)
    if not model_path.is_file():
        raise RuntimeError("whisper model %s is not downloaded" % valid)
    t0 = time.monotonic()
    from faster_whisper import WhisperModel  # noqa: PLC0415 - optional dep, lazy
    # A local dir (never a bare name — that would auto-download from HF).
    model = WhisperModel(str(model_path.parent), device="cpu",
                         compute_type="int8")
    # whisper writes what it hears. forcing language="es" on english audio
    # doesn't translate, it spends minutes producing junk labeled .es.srt.
    # so detect first (info is ready before the lazy segments run): transcribe
    # when the audio is the wanted language, translate when english is wanted
    # (whisper only translates into english), otherwise it's a miss.
    want = (str(language or "").strip().lower() or "en").split("-")[0]
    segments, info = model.transcribe(str(file_path), beam_size=5)
    heard = str(getattr(info, "language", "") or "").lower()
    if heard != want:
        if want != "en":
            logger.info("whisper: %s audio is %r, can't make %r subtitles",
                        file_path, heard, want)
            del model
            return None
        segments, _info = model.transcribe(str(file_path), task="translate",
                                           language=heard or None, beam_size=5)
    text = segments_to_srt(segments)
    # faster-whisper frees GPU/CPU buffers with the model; drop our ref too.
    del model
    logger.info("whisper: transcribed %s in %.1fs", file_path,
                time.monotonic() - t0)
    return text


# ── status payload for the settings UI ─────────────────────────────────────────

def status_payload(get_setting: Callable[[str, Any], Any]) -> dict:
    """Everything the settings UI needs. Never raises."""
    try:
        name = valid_model_name(get_setting("whisper_model",
                                            DEFAULT_WHISPER_MODEL))
    except Exception:  # noqa: BLE001
        name = None
    name = name or DEFAULT_WHISPER_MODEL
    try:
        enabled = bool(get_setting("whisper_enabled", False))
    except Exception:  # noqa: BLE001
        enabled = False
    try:
        flag = bool(get_setting("whisper_model_downloaded", False))
    except Exception:  # noqa: BLE001
        flag = False
    on_disk = model_file_exists(name)
    # Which model (if any) is actually on disk — the picker may point at a
    # different one, in which case the UI offers "Download <picked> model"
    # with a "replaces <disk_model>" note.
    disk_model = next((n for n in WHISPER_MODELS if model_file_exists(n)), None)

    def _s(key: str, default: str = "") -> str:
        try:
            return str(get_setting(key, default) or "")
        except Exception:  # noqa: BLE001
            return default

    try:
        size_bytes = int(get_setting("whisper_model_size_bytes", 0) or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    engine = transcription_engine()
    return {
        "enabled": enabled,
        "model": name,
        "models": {
            n: {"repo": m["repo"], "size_bytes": m["size_bytes"],
                "label": m["label"], "blurb": m["blurb"]}
            for n, m in WHISPER_MODELS.items()
        },
        "downloaded": bool(flag and on_disk),
        "disk_model": disk_model,
        "size_bytes": size_bytes,
        "downloaded_at": _s("whisper_model_downloaded_at"),
        "engine": engine,
        "engine_hint": None if engine else "pip install faster-whisper",
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
        if not self._model:
            return []
        if getattr(query, "forced", False):
            # a transcript is every spoken line. it can never be a forced
            # (foreign-parts-only) track.
            return []
        file_path = getattr(query, "file_path", None)
        if not file_path or not os.path.isfile(str(file_path)):
            # No media, no transcription. (The chain always passes file_path
            # in production; manual/blind queries simply get no candidate.)
            return []
        lang = str(getattr(query, "language", None) or "en")
        return [SubtitleCandidate(
            provider_id=self.id,
            language=lang,
            hi=False,  # no sound cues in a transcript
            forced=False,
            title="Local transcription (whisper %s)" % self._model,
            download_ref={"file_path": str(file_path),
                          "language": lang,
                          "model": self._model},
            # Honest, not a hack: the transcript is GENERATED from this exact
            # file, so its timing is perfect by construction — the same
            # guarantee a hash match gives. It clears the min-score gate so
            # the last-resort chain can use it.
            hash_match=True,
            download_count=0,
        )]

    def download(self, candidate: SubtitleCandidate) -> str | None:
        # Re-verify both gates on EVERY call — never assume the model is still
        # there because search() saw it (the user could delete it mid-cycle).
        try:
            if not self.is_configured(self._get_setting):
                return None
        except Exception:  # noqa: BLE001
            return None
        ref = getattr(candidate, "download_ref", None)
        if not isinstance(ref, dict):
            return None
        file_path = ref.get("file_path")
        if not file_path or not os.path.isfile(str(file_path)):
            return None
        if str(ref.get("model") or "") != (self._model or ""):
            # The candidate was built for a different model than the one now
            # selected — don't transcribe with a stale ref.
            return None
        lang = str(ref.get("language") or "en")
        try:
            text = transcribe_to_srt(self._model, str(file_path), lang)
        except Exception:  # noqa: BLE001 - a failed transcription is a miss
            logger.exception("whisper transcription failed for %s", file_path)
            return None
        return text if text and text.strip() else None


__all__ = [
    "WHISPER_MODELS", "DEFAULT_WHISPER_MODEL", "MODEL_BIN",
    "valid_model_name", "default_models_dir", "set_models_dir_override",
    "models_dir", "model_dir", "model_file_path", "model_file_exists",
    "download_model", "start_download", "get_download_status",
    "transcription_engine", "segments_to_srt", "transcribe_to_srt",
    "status_payload", "WhisperProvider",
]
