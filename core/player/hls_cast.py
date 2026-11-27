"""VOD HLS transcoder for Chromecast so transcoded streams expose exact duration.

Piped fragmented MP4 (`empty_moov`) omits total duration in its header, which
causes Google's Styled Media Receiver to treat the rolling ~5s buffer as the
end of the file and loop its "Up Next in 5..4..3..5" banner. Serving an
``#EXT-X-PLAYLIST-TYPE:VOD`` HLS playlist with exact segment durations lets
Chromecast know the true duration from millisecond 0, seek natively, show the
TV-side "Up Next" card only in the final seconds, and play through an entire
Live Guide queue even when the phone screen is locked or off.

Implementation:
- ``render_vod_playlist(duration_sec, query_string)`` produces the full VOD
  ``.m3u8`` immediately from probed duration (6-second segments).
- On the first segment request (or a far seek), one ffmpeg (the segment
  muxer) starts at ``start_seg * SEGMENT_SECONDS`` and makes the next 100
  segments, cut at the same source times whichever session makes them: a
  segment from a session started at 0 and one started by a seek are the
  same frames (checked on real ffmpeg, tests/player/test_hls_real_ffmpeg.py).
- A copied picture (h264 that plays) is only copied when its keyframes are
  close enough for 6 second segments; otherwise, or when a long gap shows up
  later, the picture is converted.
- A GPU that fails before the first segment: that cast runs on the CPU.
- Idle sessions are automatically stopped and their temp folders pruned.
"""

from __future__ import annotations

import atexit
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("player.hls_cast")

SEGMENT_SECONDS = 6
IDLE_TIMEOUT_SEC = 120.0
WAIT_SEGMENT_TIMEOUT_SEC = 25.0
MAX_SESSIONS = 3
# segments one ffmpeg makes before the next session takes over (10 minutes):
# keeps the cut list short and a runaway session bounded
WINDOW_SEGMENTS = 100


def segment_count(duration_sec: float, seg_sec: int = SEGMENT_SECONDS) -> int:
    if duration_sec <= 0:
        return 0
    return max(1, int(math.ceil(duration_sec / float(seg_sec))))


def render_vod_playlist(
    duration_sec: float,
    query_string: str = "",
    seg_sec: int = SEGMENT_SECONDS,
) -> str:
    """Build a deterministic VOD HLS media playlist with exact segment durations."""
    count = segment_count(duration_sec, seg_sec)
    if count <= 0:
        return ""
    suffix = f"?{query_string}" if query_string else ""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{seg_sec}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    remaining = float(duration_sec)
    for idx in range(count):
        seg_dur = min(float(seg_sec), max(0.25, remaining))
        remaining = max(0.0, remaining - seg_dur)
        lines.append(f"#EXTINF:{seg_dur:.3f},")
        lines.append(f"seg/{idx}.ts{suffix}")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def window_times(start_seg: int, end_seg: int, first: float, t0: float = 0.0,
                 seg_sec: int = SEGMENT_SECONDS) -> str:
    """where a session cuts: every segment boundary after its first, through
    one past its window (so the window's last segment is closed by a cut,
    not by ffmpeg stopping). ffmpeg's segment muxer counts these from the
    session's first packet (``first``, in the source's time), not from the
    source's start: checked on ffmpeg 8, a list in source time skipped every
    cut of a session that didn't start at 0"""
    return ",".join(f"{t0 + k * seg_sec - first:.3f}" for k in range(start_seg + 1, end_seg + 2))


def keyframes_between(ffprobe: str, source: str, a: float, b: float) -> Optional[List[float]]:
    """the video keyframes from a to b seconds (packets only, nothing decoded).
    None: couldn't tell"""
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
           "-of", "csv=p=0", "-read_intervals", f"{max(0.0, a - 1):.3f}%{b:.3f}", source]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30).stdout  # noqa: S603
    except Exception:  # noqa: BLE001 - no answer
        return None
    keys = []
    for line in out.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                keys.append(float(parts[0]))
            except ValueError:
                continue
    return sorted(k for k in keys if a - 0.001 <= k <= b)


def copy_start(ffprobe: str, source: str, start_seg: int, t0: float = 0.0,
               seg_sec: int = SEGMENT_SECONDS) -> Optional[tuple]:
    """a copied picture can only start on a keyframe. (seek, keyframe): the
    -ss that lands on the first keyframe inside this segment's 6 seconds,
    and that keyframe's time, so every session cuts the same segments.
    None when there's no keyframe in there: too far apart to copy"""
    a = t0 + start_seg * seg_sec
    keys = keyframes_between(ffprobe, source, a, a + seg_sec - 0.001)
    if not keys:
        return None
    if start_seg == 0:
        return 0.0, keys[0]
    from core.player.transcode import SEEK_PAST
    after = [k for k in (keyframes_between(ffprobe, source, keys[0] + 0.001, keys[0] + seg_sec) or [])
             if k > keys[0] + 0.001]
    room = (after[0] - keys[0]) / 2 if after else SEEK_PAST
    return keys[0] - t0 + min(SEEK_PAST, room), keys[0]


def hls_command(
    ffmpeg: str,
    source: str,
    plan,
    start_seg: int,
    out_dir: str,
    enc: Optional[tuple] = None,
    can_tonemap: bool = False,
    seg_sec: int = SEGMENT_SECONDS,
    seek: Optional[tuple] = None,
    t0: float = 0.0,
    end_seg: Optional[int] = None,
) -> List[str]:
    """the session's ffmpeg: segments ``start_seg`` up to ``end_seg``.

    every session cuts at the same places in the source's time, so a segment
    is the same whichever session made it and the playlist's 6 second steps
    hold:
      * converted video gets a keyframe forced at every boundary
      * copied video starts on the first keyframe inside its first segment
        (``seek`` = copy_start's answer) and is cut at the first keyframe
        after each boundary
    timestamps stay the source's own (-output_ts_offset), and the segment
    list (list.csv) says where each one really starts, which is how a
    copied file with a long gap between keyframes is caught.

    files are numbered from 0 in each session (seg_0.ts is ``start_seg``)."""
    from core.player import transcode as T

    end_seg = end_seg if end_seg is not None else start_seg + WINDOW_SEGMENTS
    start_sec = max(0.0, float(start_seg * seg_sec))
    tonemap = plan.tonemap and can_tonemap
    pre, on_gpu, _dec = T.gpu_input(enc, plan, tonemap) if enc is not None else ([], False, None)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *pre]
    if plan.copy_video and seek is not None:
        ss, first = seek
    else:
        ss, first = start_sec, t0 + start_sec
    if ss > 0:
        cmd += ["-ss", f"{ss:.3f}"]
    # read a segment past the window: its last cut needs the keyframe after it
    cmd += ["-t", f"{(end_seg + 2) * seg_sec - ss:.3f}", "-i", source]
    cmd += [
        "-map", "[v]" if plan.burn is not None and not plan.copy_video else "0:v:0",
        "-map", f"0:a:{plan.audio}?",
        "-sn", "-dn",
    ]
    times = window_times(start_seg, end_seg, first, t0, seg_sec)
    if plan.copy_video:
        cmd += ["-c:v", "copy", "-bsf:v", "h264_mp4toannexb"]
    else:
        cmd += T._video_args(enc or ("libx264", "CPU", [], ""), plan, tonemap, on_gpu,
                             keyframes=["-force_key_frames", times, "-g", "48", "-sc_threshold", "0"])
    if plan.copy_audio and not plan.delay_ms:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", T.AUDIO_KBPS, "-ac", "2"]
    cmd += [
        "-output_ts_offset", f"{ss:.3f}",
        "-f", "segment",
        "-segment_format", "mpegts",
        "-segment_times", times,
        "-segment_list", os.path.join(out_dir, "list.csv"),
        "-segment_list_type", "csv",
        "-reset_timestamps", "0",
        os.path.join(out_dir, "seg_%d.ts"),
    ]
    return cmd


@dataclass
class _HlsSession:
    key: str
    start_seg: int
    out_dir: str
    proc: subprocess.Popen
    err_path: Path
    err_file: object = None
    last_access: float = field(default_factory=time.monotonic)
    highest_written: int = -1
    # segments below this were deleted by the reaper
    trimmed_to: int = 0
    # the source time of the session's first frame, the source's own start,
    # copying the picture or not, and the encoder (for a GPU that fails)
    first: float = 0.0
    t0: float = 0.0
    copy: bool = False
    enc: object = None
    hw_decode: object = None
    end_seg: int = 1 << 30

    def seg_path(self, idx: int) -> Path:
        # files are numbered from 0 in each session
        return Path(self.out_dir) / f"seg_{idx - self.start_seg}.ts"

    def listed(self) -> Dict[int, float]:
        """finished segments, from ffmpeg's list: segment -> where it starts
        (seconds after the session's first frame)"""
        out: Dict[int, float] = {}
        try:
            text = (Path(self.out_dir) / "list.csv").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return out
        for line in text.splitlines():
            parts = line.strip().split(",")
            if len(parts) < 2 or not parts[0].startswith("seg_"):
                continue
            try:
                out[self.start_seg + int(parts[0][4:].split(".")[0])] = float(parts[1])
            except ValueError:
                continue
        return out

    def ready(self, idx: int) -> bool:
        """the segment is finished: ffmpeg listed it, started the next, or stopped"""
        if not self.seg_path(idx).exists():
            return False
        return (idx in self.listed() or self.seg_path(idx + 1).exists()
                or self.proc.poll() is not None)

    def in_step(self, idx: int, seg_sec: int = SEGMENT_SECONDS) -> bool:
        """a copied segment starts inside its own 6 seconds. one that doesn't
        means a stretch with no keyframe: every later segment is off by one"""
        if not self.copy:
            return True
        rel = self.listed().get(idx)
        if rel is None:
            return True
        real = self.first + rel
        lo = self.t0 + idx * seg_sec
        return lo - 0.5 <= real < lo + seg_sec

    def read_stderr_tail(self, max_chars: int = 300) -> str:
        try:
            if self.err_path.exists():
                return self.err_path.read_text(encoding="utf-8", errors="replace").strip()[-max_chars:]
        except OSError:
            pass
        return ""

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    logger.debug("cast-hls ffmpeg for %s didn't stop in 2s", self.key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cast-hls stopping ffmpeg for %s: %s", self.key, exc)
        try:
            if self.err_file is not None:
                self.err_file.close()
                self.err_file = None
        except Exception as exc:  # noqa: BLE001
            logger.debug("cast-hls closing the log for %s: %s", self.key, exc)
        shutil.rmtree(self.out_dir, ignore_errors=True)


_lock = threading.Lock()
_sessions: Dict[str, _HlsSession] = {}


# segments this far behind the newest one written are gone from disk: a cast
# seeking back that far starts a new session there anyway
KEEP_BEHIND_SEGMENTS = 30
REAP_EVERY_SEC = 30.0
_reaper: Optional[threading.Thread] = None


def _prune_locked(now: float) -> None:
    """idle sessions stop: ffmpeg killed, temp folder gone"""
    stale = [k for k, s in _sessions.items() if now - s.last_access > IDLE_TIMEOUT_SEC]
    for k in stale:
        _sessions.pop(k).close()


def _make_room_locked() -> None:
    """a new session needs a slot: the longest idle one gives it up"""
    while _sessions and len(_sessions) >= _max_sessions():
        oldest_key = min(_sessions, key=lambda k: _sessions[k].last_access)
        _sessions.pop(oldest_key).close()


def _trim_locked() -> None:
    """a running cast keeps only the segments near where it's playing:
    a whole movie of .ts files would otherwise pile up in the temp folder"""
    for sess in _sessions.values():
        floor = sess.highest_written - KEEP_BEHIND_SEGMENTS
        if floor <= sess.start_seg:
            continue
        for idx in range(max(sess.start_seg, sess.trimmed_to), floor):
            try:
                sess.seg_path(idx).unlink()
            except OSError:
                pass
        sess.trimmed_to = floor


def reap_once(now: Optional[float] = None) -> None:
    with _lock:
        _prune_locked(time.monotonic() if now is None else now)
        _trim_locked()


def _reap_loop() -> None:
    """a cast someone stopped never asks for another segment, so nothing
    would ever prune it: ffmpeg kept transcoding the whole title to disk"""
    global _reaper
    while True:
        time.sleep(REAP_EVERY_SEC)
        try:
            reap_once()
        except Exception as exc:  # noqa: BLE001
            logger.debug("cast-hls reaper: %s", exc)
        with _lock:
            if not _sessions:
                _reaper = None
                return


def _ensure_reaper_locked() -> None:
    global _reaper
    if _reaper is None or not _reaper.is_alive():
        _reaper = threading.Thread(target=_reap_loop, name="cast-hls-reaper", daemon=True)
        _reaper.start()


def close_all() -> None:
    with _lock:
        for k in list(_sessions):
            _sessions.pop(k).close()


# the server stopping: no ffmpeg left transcoding into a temp folder
atexit.register(close_all)


# casts that can't copy their picture after all (keyframes too far apart),
# and casts whose GPU failed: their next session converts / runs on the CPU
_no_copy: set = set()
_cpu_only: set = set()
_start_locks: Dict[str, threading.Lock] = {}
# how far ahead a copied picture's keyframes are checked before copying
COPY_LOOKAHEAD_SEGMENTS = 10


def _max_sessions() -> int:
    try:
        from core.player import hwaccel
        return hwaccel.settings()["slots"]
    except Exception:  # noqa: BLE001
        return MAX_SESSIONS


def copy_ok(ffprobe: str, source: str, start_seg: int, t0: float = 0.0,
            seg_sec: int = SEGMENT_SECONDS) -> Optional[tuple]:
    """copy_start, plus a look a minute ahead: every 6 seconds there has a
    keyframe, or the picture is converted from the start rather than
    found out a few segments in"""
    seek = copy_start(ffprobe, source, start_seg, t0, seg_sec)
    if seek is None:
        return None
    a = t0 + start_seg * seg_sec
    keys = keyframes_between(ffprobe, source, a, a + COPY_LOOKAHEAD_SEGMENTS * seg_sec)
    if keys is None:
        return seek
    if keys:
        last = keys[-1]
        for k in range(start_seg, start_seg + COPY_LOOKAHEAD_SEGMENTS):
            lo = t0 + k * seg_sec
            if lo + seg_sec > last + seg_sec:
                break  # past the last keyframe seen: the end of the file, or not read
            if not any(lo <= x < lo + seg_sec for x in keys):
                return None
    return seek


def _start(session_key: str, seg_idx: int, ffmpeg: str, source: str, plan, enc,
           can_tonemap: bool, t0: float) -> _HlsSession:
    """a new ffmpeg from ``seg_idx`` (keyframes probed outside the main lock)"""
    from dataclasses import replace

    from core.player import hwaccel
    from core.player import transcode as T
    p = plan
    seek = None
    if p.copy_video:
        if session_key not in _no_copy:
            seek = copy_ok(T.ffprobe_for(ffmpeg), source, seg_idx, t0)
        if seek is None:
            p = replace(plan, copy_video=False,
                        reasons=list(plan.reasons) + ["keyframes too far apart to copy"])
    if session_key in _cpu_only:
        enc = hwaccel.CPU
    out_dir = tempfile.mkdtemp(prefix="ss_cast_hls_")
    end_seg = seg_idx + WINDOW_SEGMENTS
    cmd = hls_command(ffmpeg, source, p, seg_idx, out_dir, enc=enc, can_tonemap=can_tonemap,
                      seek=seek, t0=t0, end_seg=end_seg)
    _pre, _gpu, hw_decode = T.gpu_input(enc, p, p.tonemap and can_tonemap) if enc is not None else (None, None, None)
    # stderr to a file in out_dir, not a pipe: Windows pipes have a 4 KB buffer
    # and deadlock ffmpeg on noisy files
    err_path = Path(out_dir) / "ffmpeg.log"
    err_file = open(err_path, "wb")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_file)  # noqa: S603 - our own argv
    except Exception:
        err_file.close()
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return _HlsSession(
        key=session_key, start_seg=seg_idx, out_dir=out_dir, proc=proc, err_path=err_path,
        err_file=err_file, last_access=time.monotonic(), highest_written=seg_idx - 1,
        first=(seek[1] if seek else t0 + seg_idx * SEGMENT_SECONDS), t0=t0,
        copy=p.copy_video, enc=None if p.copy_video else hwaccel.enc_for(enc) if enc is not None else hwaccel.CPU,
        hw_decode=hw_decode, end_seg=end_seg)


def _session_for(session_key: str, seg_idx: int, ffmpeg: str, source: str, plan, enc,
                 can_tonemap: bool, t0: float) -> _HlsSession:
    now = time.monotonic()
    with _lock:
        _prune_locked(now)
        start_lock = _start_locks.setdefault(session_key, threading.Lock())
    with start_lock:
        with _lock:
            sess = _sessions.get(session_key)
            if sess is not None and _reusable(sess, seg_idx):
                sess.last_access = now
                return sess
        new = _start(session_key, seg_idx, ffmpeg, source, plan, enc, can_tonemap, t0)
        with _lock:
            old = _sessions.pop(session_key, None)
            if old is not None:
                old.close()
            _make_room_locked()
            _sessions[session_key] = new
            _ensure_reaper_locked()
        return new


def _reusable(sess: _HlsSession, seg_idx: int) -> bool:
    if seg_idx < sess.start_seg or seg_idx >= sess.end_seg:
        return False
    if sess.seg_path(seg_idx).exists():
        return True
    if seg_idx > max(sess.start_seg, sess.highest_written) + 6:
        return False
    return sess.proc.poll() is None


def _drop(sess: _HlsSession) -> None:
    with _lock:
        if _sessions.get(sess.key) is sess:
            _sessions.pop(sess.key)
    sess.close()


def _remember(bucket: set, key: str) -> None:
    if len(bucket) > 500:
        bucket.clear()
    bucket.add(key)


def get_segment_bytes(
    session_key: str,
    seg_idx: int,
    ffmpeg: str,
    source_origin: str,
    plan,
    enc: Optional[tuple] = None,
    can_tonemap: bool = False,
    t0: float = 0.0,
) -> Optional[bytes]:
    """Return the MPEG-TS bytes for ``seg_idx``, starting or reusing a session.

    two things restart it: a copied picture whose segment starts outside its
    6 seconds (a long gap between keyframes: the rest is converted), and a
    GPU that fails before its first segment (the rest runs on the CPU, and
    the GPU is checked and marked)."""
    from core.player import hwaccel
    # a GPU failure is only blamed on the GPU once the CPU session works
    pending_mark = None
    for _attempt in range(3):
        sess = _session_for(session_key, seg_idx, ffmpeg, source_origin, plan, enc, can_tonemap, t0)
        target = sess.seg_path(seg_idx)
        deadline = time.monotonic() + WAIT_SEGMENT_TIMEOUT_SEC
        outcome = "timeout"
        while time.monotonic() < deadline:
            if sess.ready(seg_idx):
                if not sess.in_step(seg_idx):
                    outcome = "off_step"
                    break
                try:
                    data = target.read_bytes()
                except OSError:
                    data = b""
                if data:
                    with _lock:
                        sess.last_access = time.monotonic()
                        if seg_idx > sess.highest_written:
                            sess.highest_written = seg_idx
                    if pending_mark is not None:
                        hwaccel.mark_failed(ffmpeg, *pending_mark)
                    return data
            if sess.proc.poll() is not None and not target.exists():
                gpu = sess.enc is not None and getattr(sess.enc, "key", "cpu") != "cpu"
                outcome = "gpu_failed" if gpu and not any(Path(sess.out_dir).glob("seg_*.ts")) \
                    and sess.proc.returncode not in (0, None) else "ended"
                break
            time.sleep(0.08)
        if outcome == "off_step":
            logger.info("cast %s: keyframes too far apart at segment %d, converting the picture from here",
                        session_key, seg_idx)
            _remember(_no_copy, session_key)
            _drop(sess)
            continue
        if outcome == "gpu_failed":
            reason = hwaccel._words(sess.read_stderr_tail(2000).encode())
            pending_mark = (sess.enc, sess.hw_decode, reason)
            _remember(_cpu_only, session_key)
            _drop(sess)
            continue
        if outcome == "ended":
            err = sess.read_stderr_tail()
            if err:
                logger.warning("cast-hls ffmpeg exited (%s) for %s seg %d: %s",
                               sess.proc.returncode, session_key, seg_idx, err)
        return None
    return None
