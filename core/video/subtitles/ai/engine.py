"""one whisper run: a video file in, a shaped .srt out.

1. the chosen audio track (``language.choose_audio``) goes through ffmpeg to
   16 kHz mono wav in ``<data>/subtitle_cache/ai_jobs/`` (once; a sleeping
   drive is read straight through instead of seeked around).
2. the spoken language: the track's tag, else whisper's detection over
   several windows across the runtime.
3. transcription in 5 minute chunks, with silero vad, no conditioning on the
   previous text (the repeat loops), whisper's thresholds and word
   timestamps. after every chunk the segments so far are written to a
   checkpoint, so a restart picks up where it was, and the queue can pause
   (someone started watching) or cancel between chunks.
4. the guards drop what whisper made up, the confidence score decides
   ok / low / discard, and the words become proper subtitles (``shaping``).

faster-whisper and numpy are imported only in here, only when a job runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

from . import guards, language, shaping

logger = get_logger("video.subtitles.ai.engine")

CHUNK_SECONDS = 300
# a segment ending this close to a chunk's end may be cut: it is redone at
# the start of the next chunk instead
CHUNK_TAIL = 3.0
SAMPLE_RATE = 16000

VAD_PARAMETERS = {"min_silence_duration_ms": 500, "speech_pad_ms": 200, "threshold": 0.5}
TRANSCRIBE_OPTIONS = {
    "beam_size": 5,
    "vad_filter": True,
    "vad_parameters": VAD_PARAMETERS,
    "condition_on_previous_text": False,
    "no_speech_threshold": guards.NO_SPEECH_THRESHOLD,
    "log_prob_threshold": guards.LOGPROB_THRESHOLD,
    "compression_ratio_threshold": guards.COMPRESSION_RATIO_THRESHOLD,
    "word_timestamps": True,
    "hallucination_silence_threshold": 2.0,
}


class Cancelled(Exception):
    """the job was cancelled."""


class Paused(Exception):
    """someone started watching: the job stops after this chunk and waits.
    its checkpoint is on disk."""


class Control:
    """how the queue talks to a running job. the defaults never stop."""

    def cancelled(self) -> bool:
        return False

    def paused(self) -> bool:
        return False

    def progress(self, fraction: float, stage: str) -> None:
        pass


def work_dir() -> Path:
    db = os.environ.get("VIDEO_DATABASE_PATH", "database/video_library.db")
    return Path(db).resolve().parent / "subtitle_cache" / "ai_jobs"


# ── audio ────────────────────────────────────────────────────────────────────

def extract_audio(ffmpeg: str, video: str, audio_index: int, out_wav: str, timeout: int = 3600) -> None:
    """the chosen audio track as 16 kHz mono 16-bit wav. raises on failure."""
    tmp = out_wav + ".part"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video,
           "-map", "0:a:%d" % int(audio_index), "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", tmp]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)  # noqa: S603 - our own argv
    if res.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 1000:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("ffmpeg couldn't read the audio: %s"
                           % (res.stderr or b"").decode("utf-8", "replace")[-300:])
    os.replace(tmp, out_wav)


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate() or SAMPLE_RATE)


def read_wav(path: str, start: float, length: float):
    """float32 samples for [start, start+length) seconds."""
    import numpy as np  # noqa: PLC0415
    with wave.open(path, "rb") as w:
        rate = w.getframerate() or SAMPLE_RATE
        first = max(0, int(start * rate))
        if first >= w.getnframes():
            return np.zeros(0, dtype=np.float32)
        w.setpos(first)
        raw = w.readframes(max(0, int(length * rate)))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


# ── model ────────────────────────────────────────────────────────────────────

_model_lock = threading.Lock()
_loaded: Dict[str, Any] = {"key": None, "model": None, "device": None, "used": 0.0}


def load_model(model_dir: str, device_pref: str = "auto", threads: int = 0):
    """(model, device). cuda when asked for (or auto) and ctranslate2 sees a
    gpu; a cuda load that fails falls back to the cpu and says why in the
    log. the last model stays loaded for the next job."""
    from . import hardware
    want_cuda = device_pref in ("auto", "cuda") and hardware.detect().get("cuda")
    key = (model_dir, "cuda" if want_cuda else "cpu", threads)
    with _model_lock:
        if _loaded["key"] == key and _loaded["model"] is not None:
            _loaded["used"] = time.monotonic()
            return _loaded["model"], _loaded["device"]
        _loaded.update(key=None, model=None, device=None)
    from faster_whisper import WhisperModel  # noqa: PLC0415 - optional dep, lazy
    model = None
    device = "cpu"
    if want_cuda:
        try:
            model = WhisperModel(model_dir, device="cuda", compute_type="int8_float16")
            device = "cuda"
        except Exception:  # noqa: BLE001
            logger.warning("ai subtitles: the gpu wouldn't load the model, using the cpu", exc_info=True)
            model = None
    if model is None:
        model = WhisperModel(model_dir, device="cpu", compute_type="int8", cpu_threads=threads)
    with _model_lock:
        _loaded.update(key=key, model=model, device=device, used=time.monotonic())
    return model, device


def unload_model() -> None:
    with _model_lock:
        _loaded.update(key=None, model=None, device=None)


def idle_unload(after_seconds: float = 300) -> None:
    with _model_lock:
        if _loaded["model"] is not None and time.monotonic() - _loaded["used"] > after_seconds:
            _loaded.update(key=None, model=None, device=None)


# ── language detection over several windows ─────────────────────────────────

def detect_spoken(model: Any, wav: str, duration: float) -> tuple:
    """(language, vote share) from whisper's detection on windows across the
    runtime, each weighted by how much speech vad found in it."""
    results = []
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        get_speech_timestamps = None
    for start in language.detection_windows(duration):
        audio = read_wav(wav, start, 30.0)
        if audio.size < SAMPLE_RATE:
            continue
        speech = 1.0
        if get_speech_timestamps is not None:
            try:
                ts = get_speech_timestamps(audio, VadOptions(**VAD_PARAMETERS))
                speech = sum(t["end"] - t["start"] for t in ts) / float(audio.size)
            except Exception:  # noqa: BLE001
                speech = 1.0
        if speech < 0.1:
            continue
        try:
            lang, prob, _all = model.detect_language(audio=audio)
        except Exception:  # noqa: BLE001 - older faster-whisper: transcribe's own detection
            _segs, info = model.transcribe(audio, beam_size=1, without_timestamps=True)
            lang, prob = info.language, info.language_probability
        results.append((lang, prob, speech))
    return language.aggregate_detection(results)


# ── the run ──────────────────────────────────────────────────────────────────

def _seg_dict(seg: Any, offset: float) -> Dict[str, Any]:
    words = []
    for w in getattr(seg, "words", None) or []:
        words.append({"start": round(float(w.start) + offset, 3), "end": round(float(w.end) + offset, 3),
                      "word": str(w.word), "probability": float(getattr(w, "probability", 0) or 0)})
    return {"start": round(float(seg.start) + offset, 3), "end": round(float(seg.end) + offset, 3),
            "text": str(seg.text or "").strip(), "avg_logprob": float(getattr(seg, "avg_logprob", 0) or 0),
            "no_speech_prob": float(getattr(seg, "no_speech_prob", 0) or 0),
            "compression_ratio": float(getattr(seg, "compression_ratio", 1) or 1), "words": words}


def load_checkpoint(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_checkpoint(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def run(job: Dict[str, Any], *, video: str, model_dir: str, ffmpeg: str, ffprobe: str,
        want: str, stored_languages: Optional[List[str]] = None, device_pref: str = "auto",
        threads: int = 0, control: Optional[Control] = None,
        model_loader: Callable[..., Any] = load_model) -> Dict[str, Any]:
    """transcribe ``video`` for ``want``. returns {status: done | no_match |
    discarded, srt, language, task, confidence, verdict, device, why}.
    raises Cancelled / Paused (checkpoint kept) or on a hard failure."""
    control = control or Control()
    folder = work_dir()
    folder.mkdir(parents=True, exist_ok=True)
    jid = str(job.get("id"))
    ck_path = folder / ("%s.json" % jid)
    wav = str(folder / ("%s.wav" % jid))
    ck = load_checkpoint(ck_path)

    # 1. the audio track and, when its tag says, its language
    if "audio_index" not in ck:
        streams = language.probe_audio_streams(ffprobe, video)
        idx, spoken = language.choose_audio(streams, want, stored_languages)
        ck.update(audio_index=idx, spoken=spoken, offset=0.0, segments=[], speech_seconds=0.0)
        save_checkpoint(ck_path, ck)
    if not os.path.isfile(wav):
        control.progress(0.01, "reading audio")
        extract_audio(ffmpeg, video, int(ck["audio_index"]), wav)
    duration = wav_duration(wav)
    if control.cancelled():
        raise Cancelled()

    model, device = model_loader(model_dir, device_pref, threads)

    # 2. the language: the tag, else detection across the runtime
    if not ck.get("spoken"):
        control.progress(0.03, "listening for the language")
        lang, share = detect_spoken(model, wav, duration)
        ck.update(spoken=lang, detect_share=share)
        save_checkpoint(ck_path, ck)
    task = language.plan_task(ck.get("spoken"), want)
    if task is None:
        return {"status": "no_match", "language": ck.get("spoken"), "task": None, "device": device,
                "why": "the audio is %s, so it can't make %s subtitles yet" % (ck.get("spoken") or "unknown", want)}
    ck["task"] = task

    # 3. chunks, checkpointed
    offset = float(ck.get("offset") or 0.0)
    segments: List[Dict[str, Any]] = list(ck.get("segments") or [])
    speech = float(ck.get("speech_seconds") or 0.0)
    src_lang = language.base_lang(ck.get("spoken")) or None
    while offset < duration - 0.5:
        if control.cancelled():
            raise Cancelled()
        if control.paused():
            raise Paused()
        length = min(CHUNK_SECONDS, duration - offset)
        last = offset + length >= duration - 0.5
        audio = read_wav(wav, offset, length)
        segs, info = model.transcribe(audio, language=src_lang, task=task, **TRANSCRIBE_OPTIONS)
        chunk: List[Dict[str, Any]] = []
        for seg in segs:
            if control.cancelled():
                raise Cancelled()
            chunk.append(_seg_dict(seg, offset))
            control.progress(min(0.97, 0.05 + 0.92 * (chunk[-1]["end"] / duration)), "transcribing")
        end = offset + length
        if not last:
            keep = [s for s in chunk if s["end"] <= end - CHUNK_TAIL]
            nxt = keep[-1]["end"] if keep else end - CHUNK_TAIL
            nxt = max(nxt, offset + 30.0)
        else:
            keep, nxt = chunk, duration
        segments.extend(keep)
        try:
            speech += float(getattr(info, "duration_after_vad", 0) or 0) * ((nxt - offset) / max(1.0, length))
        except (TypeError, ValueError):
            pass
        offset = nxt
        ck.update(offset=offset, segments=segments, speech_seconds=speech)
        save_checkpoint(ck_path, ck)
        control.progress(min(0.97, 0.05 + 0.92 * (offset / duration)), "transcribing")

    # 4. guards, score, shape
    kept, why = guards.filter_segments(segments)
    conf = guards.confidence(segments, kept, speech, duration)
    verdict = guards.verdict(conf)
    result = {"language": ck.get("spoken"), "task": task, "confidence": conf, "verdict": verdict,
              "device": device, "dropped": why, "segments": len(kept)}
    if verdict == "discard" or not kept:
        result.update(status="discarded",
                      why="whisper's output looked made up (confidence %.0f%%), so nothing was written"
                      % (conf * 100))
        return result
    cues = shaping.shape(kept)
    result.update(status="done", srt=shaping.cues_to_srt(cues), cues=len(cues))
    return result


def cleanup(job_id: Any) -> None:
    """drop a job's wav and checkpoint."""
    folder = work_dir()
    for name in ("%s.wav" % job_id, "%s.wav.part" % job_id, "%s.json" % job_id, "%s.part" % job_id):
        try:
            (folder / name).unlink()
        except OSError:
            pass


__all__ = ["run", "Control", "Cancelled", "Paused", "extract_audio", "read_wav", "wav_duration",
           "load_model", "unload_model", "idle_unload", "detect_spoken", "cleanup", "work_dir",
           "TRANSCRIBE_OPTIONS", "CHUNK_SECONDS"]
