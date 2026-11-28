"""Single-decode music analysis pass: waveform peaks, Smart Transitions
(Plexamp-style Sweet Fades), BPM, musical key / Camelot wheel, and a compact
32-dim timbral/rhythmic sonic embedding.

Files are the source of truth: BPM and musical key are read from existing
file tags first so nothing is analyzed twice, and can be written back to
standard tags (TBPM/BPM, TKEY/INITIALKEY) when tag writing is enabled.
Waveform peaks, transition points, and sonic vectors live in rebuildable DB
tables (lib2_track_analysis and sonic_embeddings).
"""

from __future__ import annotations

import array
import json
import math
import os
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("analysis.music")

WAVEFORM_BUCKETS = 200
SAMPLE_RATE = 11025  # fast, Pi-friendly mono decode for rhythm/timbre/envelope
EMBEDDING_DIM = 32

# Krumhansl-Schmuckler major & minor pitch-class profiles (C..B)
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Standard Camelot wheel mapping for harmonic mixing
CAMELOT_MAP = {
    "B": "1B", "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B", "A#": "6B",
    "F": "7B", "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B",
    "G#m": "1A", "D#m": "2A", "A#m": "3A", "Fm": "4A", "Cm": "5A", "Gm": "6A",
    "Dm": "7A", "Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A", "C#m": "12A",
}

_ready_dbs: set = set()


def ensure_tables(db: Any) -> None:
    key = str(getattr(db, "db_path", getattr(db, "database_path", id(db))))
    if key in _ready_dbs:
        return
    with db._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lib2_track_analysis (
                track_id INTEGER PRIMARY KEY,
                duration_s REAL NOT NULL DEFAULT 0,
                bpm REAL,
                musical_key TEXT,
                camelot TEXT,
                peak_db REAL,
                leading_silence_s REAL NOT NULL DEFAULT 0,
                trailing_silence_s REAL NOT NULL DEFAULT 0,
                mix_in_s REAL NOT NULL DEFAULT 0,
                mix_out_s REAL NOT NULL DEFAULT 0,
                waveform_json TEXT,
                analyzed_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sonic_embeddings (
                track_id INTEGER PRIMARY KEY,
                vector_json TEXT NOT NULL,
                bpm REAL,
                camelot TEXT,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
    _ready_dbs.add(key)


# ── tag read-back & opt-in write-back ────────────────────────────────────

def read_bpm_key_tags(audio_path: str) -> Tuple[Optional[float], Optional[str]]:
    """Read existing BPM and musical key from audio file tags if present."""
    try:
        import mutagen
        mf = mutagen.File(audio_path, easy=True)
        if not mf:
            return None, None
        bpm = None
        for k in ("bpm", "tbpm"):
            vals = mf.get(k)
            if vals:
                try:
                    v = float(str(vals[0]).strip())
                    if 30.0 <= v <= 300.0:
                        bpm = round(v, 1)
                        break
                except ValueError:
                    pass
        mkey = None
        for k in ("initialkey", "key", "tkey"):
            vals = mf.get(k)
            if vals and str(vals[0]).strip():
                mkey = str(vals[0]).strip()
                break
        return bpm, mkey
    except Exception:  # noqa: BLE001
        return None, None


def write_bpm_key_tags(audio_path: str, bpm: Optional[float], musical_key: Optional[str]) -> bool:
    """Write standard BPM and musical key tags back to the file (opt-in)."""
    if not bpm and not musical_key:
        return False
    try:
        import mutagen
        mf = mutagen.File(audio_path, easy=True)
        if mf is None:
            return False
        if bpm:
            mf["bpm"] = [str(int(round(bpm)))]
        if musical_key:
            try:
                mf["initialkey"] = [musical_key]
            except Exception:  # noqa: BLE001
                pass
        mf.save()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not write bpm/key tags to %s: %s", audio_path, exc)
        return False


# ── single-decode PCM extraction ─────────────────────────────────────────

def _decode_pcm_mono(audio_path: str) -> Tuple[array.array, int]:
    """Decode audio file to mono 16-bit PCM. Reads WAV natively; uses ffmpeg otherwise."""
    p = Path(audio_path)
    if p.suffix.lower() == ".wav":
        with wave.open(str(p), "rb") as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
            if sw == 2:
                samples = array.array("h")
                samples.frombytes(raw)
                if ch > 1:
                    samples = array.array("h", samples[::ch])
                return samples, sr

    from core.player.transcode import ffmpeg_path
    ff = ffmpeg_path() or "ffmpeg"
    cmd = [
        ff, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(p),
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)  # noqa: S603
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg decode failed for {audio_path}")
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) - (len(proc.stdout) % 2)])
    return samples, SAMPLE_RATE


# ── pure DSP analysis over mono PCM ──────────────────────────────────────

def _compute_waveform_and_transitions(
    samples: array.array,
    sr: int,
    buckets: int = WAVEFORM_BUCKETS,
) -> Dict[str, Any]:
    n = len(samples)
    if n == 0 or sr <= 0:
        return {
            "duration_s": 0.0, "peak_db": -96.0,
            "leading_silence_s": 0.0, "trailing_silence_s": 0.0,
            "mix_in_s": 0.0, "mix_out_s": 0.0,
            "waveform": [0] * buckets,
        }
    duration_s = n / float(sr)
    # 100ms RMS frames for accurate silence & Sweet Fades transition detection
    frame_len = max(1, sr // 10)
    frame_rms: List[float] = []
    max_abs = 0
    for i in range(0, n, frame_len):
        chunk = samples[i : i + frame_len]
        if not chunk:
            break
        sq = 0.0
        for s in chunk:
            a = -s if s < 0 else s
            if a > max_abs:
                max_abs = a
            sq += s * s
        frame_rms.append(math.sqrt(sq / len(chunk)) / 32768.0)

    peak_norm = max(1e-6, max_abs / 32768.0)
    peak_db = round(20.0 * math.log10(peak_norm), 2)

    # silence threshold: -42 dB (~0.008 linear); mix transition threshold: -18 dB (~0.125 linear)
    silence_thr = 0.008
    mix_thr = max(0.03, peak_norm * 0.14)

    lead_frames = 0
    for r in frame_rms:
        if r >= silence_thr:
            break
        lead_frames += 1

    trail_frames = 0
    for r in reversed(frame_rms):
        if r >= silence_thr:
            break
        trail_frames += 1

    mix_in_frames = lead_frames
    for idx in range(lead_frames, min(len(frame_rms), lead_frames + 150)):
        if frame_rms[idx] >= mix_thr:
            mix_in_frames = idx
            break

    mix_out_frames = max(0, len(frame_rms) - trail_frames)
    for idx in range(len(frame_rms) - trail_frames - 1, max(0, len(frame_rms) - trail_frames - 180), -1):
        if frame_rms[idx] >= mix_thr:
            mix_out_frames = idx + 1
            break

    # downsample RMS into `buckets` normalized 0..255 waveform bars
    max_rms = max(frame_rms) if frame_rms else 1e-6
    max_rms = max(max_rms, 1e-6)
    waveform: List[int] = []
    total_f = len(frame_rms)
    for b in range(buckets):
        lo = int(b * total_f / buckets)
        hi = max(lo + 1, int((b + 1) * total_f / buckets))
        val = max(frame_rms[lo:hi]) if lo < total_f else 0.0
        waveform.append(min(255, max(0, int(round((val / max_rms) * 255)))))

    return {
        "duration_s": round(duration_s, 3),
        "peak_db": peak_db,
        "leading_silence_s": round(lead_frames * 0.1, 2),
        "trailing_silence_s": round(trail_frames * 0.1, 2),
        "mix_in_s": round(mix_in_frames * 0.1, 2),
        "mix_out_s": round(min(duration_s, mix_out_frames * 0.1), 2),
        "waveform": waveform,
        "_frame_rms": frame_rms,
    }


def _estimate_bpm(frame_rms: List[float]) -> Optional[float]:
    """Onset-flux autocorrelation BPM estimator over 10Hz envelope (60..180 BPM)."""
    if len(frame_rms) < 40:
        return None
    # positive energy flux
    flux = [max(0.0, frame_rms[i] - frame_rms[i - 1]) for i in range(1, len(frame_rms))]
    mean_f = sum(flux) / len(flux)
    if mean_f < 1e-5:
        return None
    centered = [x - mean_f for x in flux]
    # lag in 10Hz frames: 60 BPM = lag 10, 180 BPM = lag 3.33 -> test lags 3..15
    best_lag = None
    best_corr = -1.0
    n = len(centered)
    for lag in range(3, min(16, n // 2)):
        corr = sum(centered[i] * centered[i + lag] for i in range(n - lag))
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    if not best_lag or best_corr <= 0:
        return None
    bpm = 600.0 / best_lag  # 10 frames/sec * 60 sec/min
    while bpm < 70.0:
        bpm *= 2.0
    while bpm > 175.0:
        bpm /= 2.0
    return round(bpm, 1)


def _estimate_key_and_chroma(samples: array.array, sr: int) -> Tuple[str, str, List[float]]:
    """Estimate 12-bin pitch-class chroma via Goertzel DFT on middle excerpt -> Krumhansl key."""
    n = len(samples)
    if n < sr:
        return "C", "8B", [1.0 / 12.0] * 12
    # take up to 12 seconds from the middle of the track for speed
    mid = n // 2
    span = min(n, sr * 12)
    start = max(0, mid - span // 2)
    excerpt = samples[start : start + span]
    step = max(1, len(excerpt) // 8000)
    sub = [excerpt[i] / 32768.0 for i in range(0, len(excerpt), step)]
    eff_sr = sr / float(step)
    m = len(sub)

    chroma = [0.0] * 12
    # probe octaves 3..5 (C3=130.81Hz .. B5=987.77Hz)
    for pc in range(12):
        energy = 0.0
        for oct_shift in (0, 12, 24):
            midi = 48 + pc + oct_shift
            freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
            if freq >= eff_sr * 0.45:
                continue
            w = 2.0 * math.pi * freq / eff_sr
            coeff = 2.0 * math.cos(w)
            s0 = s1 = s2 = 0.0
            for x in sub:
                s0 = x + coeff * s1 - s2
                s2 = s1
                s1 = s0
            power = s1 * s1 + s2 * s2 - coeff * s1 * s2
            energy += max(0.0, power) / max(1, m)
        chroma[pc] = math.sqrt(energy)

    total = sum(chroma) or 1.0
    chroma = [c / total for c in chroma]

    best_key = "C"
    best_score = -1e9
    for root in range(12):
        rot = chroma[root:] + chroma[:root]
        maj = sum(rot[i] * _MAJOR_PROFILE[i] for i in range(12))
        if maj > best_score:
            best_score = maj
            best_key = _NOTE_NAMES[root]
        mn = sum(rot[i] * _MINOR_PROFILE[i] for i in range(12))
        if mn > best_score:
            best_score = mn
            best_key = f"{_NOTE_NAMES[root]}m"

    return best_key, CAMELOT_MAP.get(best_key, "8B"), chroma


def _build_sonic_embedding(
    samples: array.array,
    sr: int,
    chroma: List[float],
    frame_rms: List[float],
    bpm: Optional[float],
) -> List[float]:
    """Build a normalized 32-dim timbral/harmonic/rhythmic embedding vector:
    - 12 dims: pitch-class chroma
    - 8 dims: coarse energy contour across song sections
    - 8 dims: loudness distribution histogram + dynamic range
    - 4 dims: zero-crossing brightness, onset density, normalized BPM, energy variance
    """
    vec: List[float] = []
    # 12 chroma dims
    vec.extend(chroma[:12])

    # 8 section energy dims
    nf = len(frame_rms)
    max_r = max(frame_rms) if frame_rms else 1e-6
    max_r = max(max_r, 1e-6)
    for b in range(8):
        lo = int(b * nf / 8)
        hi = max(lo + 1, int((b + 1) * nf / 8))
        seg = frame_rms[lo:hi] if lo < nf else [0.0]
        vec.append((sum(seg) / len(seg)) / max_r)

    # 8 loudness histogram dims
    hist = [0.0] * 8
    for r in frame_rms:
        idx = min(7, int((r / max_r) * 7.99))
        hist[idx] += 1.0
    denom = float(nf) or 1.0
    vec.extend(h / denom for h in hist)

    # 4 rhythm & brightness dims
    step = max(1, len(samples) // 12000)
    zcr = 0
    prev = 0
    count = 0
    for i in range(0, len(samples), step):
        v = samples[i]
        if (v >= 0) != (prev >= 0):
            zcr += 1
        prev = v
        count += 1
    zcr_rate = zcr / float(max(1, count))
    flux = [max(0.0, frame_rms[i] - frame_rms[i - 1]) for i in range(1, len(frame_rms))]
    onset_density = (sum(flux) / len(flux)) / max_r if flux else 0.0
    mean_r = (sum(frame_rms) / nf) / max_r if nf else 0.0
    var_r = (sum(((r / max_r) - mean_r) ** 2 for r in frame_rms) / nf) if nf else 0.0
    bpm_norm = min(1.0, max(0.0, ((bpm or 110.0) - 60.0) / 140.0))
    vec.extend([zcr_rate, onset_density, bpm_norm, math.sqrt(var_r)])

    # L2 normalize for fast dot-product cosine similarity
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 5) for x in vec[:EMBEDDING_DIM]]


# ── public entry points ──────────────────────────────────────────────────

def analyze_track_file(
    db: Any,
    track_id: int,
    audio_path: str,
    *,
    write_tags: bool = False,
) -> Dict[str, Any]:
    """Run the single-decode music analysis pass for ``track_id`` and persist results."""
    ensure_tables(db)
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    tag_bpm, tag_key = read_bpm_key_tags(audio_path)
    samples, sr = _decode_pcm_mono(audio_path)
    env = _compute_waveform_and_transitions(samples, sr)
    frame_rms = env.pop("_frame_rms", [])

    est_bpm = tag_bpm if tag_bpm is not None else _estimate_bpm(frame_rms)
    est_key, camelot, chroma = _estimate_key_and_chroma(samples, sr)
    if tag_key:
        est_key = tag_key
        camelot = CAMELOT_MAP.get(tag_key, camelot)

    embedding = _build_sonic_embedding(samples, sr, chroma, frame_rms, est_bpm)
    now = time.time()

    with db._get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO lib2_track_analysis
                (track_id, duration_s, bpm, musical_key, camelot, peak_db,
                 leading_silence_s, trailing_silence_s, mix_in_s, mix_out_s,
                 waveform_json, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(track_id),
                env["duration_s"],
                est_bpm,
                est_key,
                camelot,
                env["peak_db"],
                env["leading_silence_s"],
                env["trailing_silence_s"],
                env["mix_in_s"],
                env["mix_out_s"],
                json.dumps(env["waveform"]),
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO sonic_embeddings
                (track_id, vector_json, bpm, camelot, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(track_id), json.dumps(embedding), est_bpm, camelot, now),
        )
        conn.commit()

    tags_written = False
    if write_tags:
        tags_written = write_bpm_key_tags(audio_path, est_bpm, est_key)

    return {
        "track_id": int(track_id),
        "duration_s": env["duration_s"],
        "bpm": est_bpm,
        "musical_key": est_key,
        "camelot": camelot,
        "peak_db": env["peak_db"],
        "leading_silence_s": env["leading_silence_s"],
        "trailing_silence_s": env["trailing_silence_s"],
        "mix_in_s": env["mix_in_s"],
        "mix_out_s": env["mix_out_s"],
        "waveform": env["waveform"],
        "tags_written": tags_written,
    }


def get_track_analysis(db: Any, track_id: int) -> Optional[Dict[str, Any]]:
    ensure_tables(db)
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM lib2_track_analysis WHERE track_id = ?", (int(track_id),)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["waveform"] = json.loads(d.pop("waveform_json") or "[]")
    except (TypeError, ValueError):
        d["waveform"] = []
    return d
