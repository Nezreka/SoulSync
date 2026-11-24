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
        cmd += ["-c:v", "copy"]
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
    last_access: float = field(default_factory=time.monotonic)
    highest_written: int = -1

    def seg_path(self, idx: int) -> Path:
        return Path(self.out_dir) / f"seg_{idx}.ts"

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.out_dir, ignore_errors=True)


_lock = threading.Lock()
_sessions: Dict[str, _HlsSession] = {}


def _prune_locked(now: float) -> None:
    stale = [k for k, s in _sessions.items() if now - s.last_access > IDLE_TIMEOUT_SEC]
    for k in stale:
        _sessions.pop(k).close()
    while len(_sessions) >= MAX_SESSIONS:
        oldest_key = min(_sessions, key=lambda k: _sessions[k].last_access)
        _sessions.pop(oldest_key).close()


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
            out_dir = tempfile.mkdtemp(prefix="ss_cast_hls_")
            cmd = hls_command(
                ffmpeg, source_origin, plan, seg_idx, out_dir,
                enc=enc, can_tonemap=can_tonemap,
            )
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            sess = _HlsSession(
                key=session_key,
                start_seg=seg_idx,
                out_dir=out_dir,
                proc=proc,
                last_access=now,
                highest_written=seg_idx - 1,
            )
            _sessions[session_key] = sess

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
            try:
                err = sess.proc.stderr.read().decode("utf-8", "replace").strip() if sess.proc.stderr else ""
                if err:
                    logger.warning("cast-hls ffmpeg exited (%s) for %s seg %d: %s",
                                   sess.proc.returncode, session_key, seg_idx, err[-300:])
            except Exception:  # noqa: BLE001
                pass
            return None
        time.sleep(0.08)
    return None
