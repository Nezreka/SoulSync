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
- On the first segment request (or a far seek), a single rolling ``ffmpeg -f hls``
  process starts at ``start_seg * SEGMENT_SECONDS`` with ``-hls_flags temp_file``
  so each ``seg_<N>.ts`` appears atomically on disk with seamless audio/video
  continuity and zero per-segment restart pops.
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


def hls_command(
    ffmpeg: str,
    source: str,
    plan,
    start_seg: int,
    out_dir: str,
    enc: Optional[tuple] = None,
    can_tonemap: bool = False,
    seg_sec: int = SEGMENT_SECONDS,
) -> List[str]:
    """Build the rolling ffmpeg HLS command starting at ``start_seg``."""
    from core.player import transcode as T

    enc = enc or ("libx264", "CPU", [], "")
    start_sec = max(0.0, float(start_seg * seg_sec))
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if not plan.copy_video:
        cmd += list(enc[2])
    if start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    cmd += ["-i", source]
    cmd += [
        "-map", "[v]" if plan.burn is not None and not plan.copy_video else "0:v:0",
        "-map", f"0:a:{plan.audio}?",
        "-sn", "-dn",
    ]
    if plan.copy_video:
        cmd += ["-c:v", "copy", "-bsf:v", "h264_mp4toannexb"]
    else:
        cmd += T._video_args(enc, plan, plan.tonemap and can_tonemap)
    if plan.copy_audio and not plan.delay_ms:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", T.AUDIO_KBPS, "-ac", "2"]
    seg_pattern = os.path.join(out_dir, "seg_%d.ts")
    playlist_path = os.path.join(out_dir, "live.m3u8")
    cmd += [
        "-output_ts_offset", f"{start_sec:.3f}",
        "-f", "hls",
        "-hls_time", str(seg_sec),
        "-hls_list_size", "0",
        "-hls_segment_type", "mpegts",
        "-start_number", str(start_seg),
        "-hls_flags", "temp_file+independent_segments",
        "-hls_segment_filename", seg_pattern,
        playlist_path,
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

    def seg_path(self, idx: int) -> Path:
        return Path(self.out_dir) / f"seg_{idx}.ts"

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
    while len(_sessions) >= MAX_SESSIONS:
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


def get_segment_bytes(
    session_key: str,
    seg_idx: int,
    ffmpeg: str,
    source_origin: str,
    plan,
    enc: Optional[tuple] = None,
    can_tonemap: bool = False,
) -> Optional[bytes]:
    """Return the MPEG-TS bytes for ``seg_idx``, starting or reusing a rolling HLS session."""
    now = time.monotonic()
    with _lock:
        _prune_locked(now)
        sess = _sessions.get(session_key)
        need_new = False
        if sess is None:
            need_new = True
        else:
            # Reuse if segment already exists OR is right ahead of where ffmpeg is encoding
            if sess.seg_path(seg_idx).exists():
                sess.last_access = now
            elif seg_idx < sess.start_seg or seg_idx > max(sess.start_seg, sess.highest_written) + 6:
                need_new = True
            elif sess.proc.poll() is not None and not sess.seg_path(seg_idx).exists():
                need_new = True
            else:
                sess.last_access = now

        if need_new:
            old = _sessions.pop(session_key, None)
            if old is not None:
                old.close()
            _make_room_locked()
            out_dir = tempfile.mkdtemp(prefix="ss_cast_hls_")
            cmd = hls_command(
                ffmpeg, source_origin, plan, seg_idx, out_dir,
                enc=enc, can_tonemap=can_tonemap,
            )
            # Log stderr to a temp file inside out_dir rather than subprocess.PIPE:
            # Windows pipes have a 4 KB buffer and deadlock ffmpeg on noisy files.
            err_path = Path(out_dir) / "ffmpeg.log"
            err_file = open(err_path, "wb")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=err_file,
                )
            except Exception:
                err_file.close()
                shutil.rmtree(out_dir, ignore_errors=True)
                raise
            sess = _HlsSession(
                key=session_key,
                start_seg=seg_idx,
                out_dir=out_dir,
                proc=proc,
                err_path=err_path,
                err_file=err_file,
                last_access=now,
                highest_written=seg_idx - 1,
            )
            _sessions[session_key] = sess
            _ensure_reaper_locked()

    assert sess is not None
    target = sess.seg_path(seg_idx)
    deadline = time.monotonic() + WAIT_SEGMENT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if target.exists():
            # temp_file flag guarantees atomic rename once segment is complete
            try:
                data = target.read_bytes()
                if data:
                    with _lock:
                        sess.last_access = time.monotonic()
                        if seg_idx > sess.highest_written:
                            sess.highest_written = seg_idx
                    return data
            except OSError:
                pass
        if sess.proc.poll() is not None:
            if target.exists():
                try:
                    return target.read_bytes()
                except OSError:
                    return None
            err = sess.read_stderr_tail()
            if err:
                logger.warning("cast-hls ffmpeg exited (%s) for %s seg %d: %s",
                               sess.proc.returncode, session_key, seg_idx, err)
            return None
        time.sleep(0.08)
    return None
