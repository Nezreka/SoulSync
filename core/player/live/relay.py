"""Shared per-channel HLS relay with tuner sharing and idle reaping.

One upstream ffmpeg process serves every viewer tuned to the same channel, so
two people watching 5.1 use one tuner. Refcounted viewers + idle reaper stops
ffmpeg and releases the tuner ~45s after the last viewer leaves. Enforces each
tuner's TunerCount and explains who holds the tuners when all are busy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("player.live.relay")

IDLE_STOP_SECONDS = 45.0
HLS_TIME = 3
HLS_LIST_SIZE = 8


class TunerBusyError(RuntimeError):
    """Raised when every tuner on a source is already held by other channels."""

    def __init__(self, message: str, holders: List[Dict[str, Any]]):
        super().__init__(message)
        self.holders = holders


@dataclass
class LiveSession:
    channel_id: str
    source_id: str
    channel_name: str
    stream_url: str
    work_dir: str
    process: Optional[Any] = None
    viewers: Set[str] = field(default_factory=set)
    started_at: float = field(default_factory=time.time)
    last_touch: float = field(default_factory=time.time)
    empty_since: Optional[float] = None


def _default_ffmpeg_cmd(channel: Dict[str, Any], out_dir: str) -> List[str]:
    """Build a resilient sliding-window HLS ffmpeg command for a live channel."""
    from core.player.transcode import ffmpeg_binary
    ff = ffmpeg_binary() or "ffmpeg"
    url = str(channel["stream_url"])
    headers = channel.get("headers") or {}
    cmd = [ff, "-hide_banner", "-loglevel", "warning", "-nostdin"]
    if url.startswith(("http://", "https://")):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if headers.get("User-Agent"):
            cmd += ["-user_agent", str(headers["User-Agent"])]
        if headers.get("Referer"):
            cmd += ["-headers", f"Referer: {headers['Referer']}\r\n"]
    cmd += [
        "-fflags", "+genpts+discardcorrupt",
        "-i", url,
    ]
    # OTA MPEG-2 from HDHomeRun needs deinterlace + H.264 + AAC for browsers;
    # IPTV HLS/H.264 can remux cheaply when not an HDHomeRun source.
    if channel.get("source_kind") == "hdhomerun":
        cmd += [
            "-vf", "yadif=0:-1:0",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "22",
            "-c:a", "aac", "-ac", "2", "-b:a", "192k",
        ]
    else:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-ac", "2", "-b:a", "192k"]
    playlist = os.path.join(out_dir, "stream.m3u8")
    seg_pattern = os.path.join(out_dir, "seg_%05d.ts")
    cmd += [
        "-f", "hls",
        "-hls_time", str(HLS_TIME),
        "-hls_list_size", str(HLS_LIST_SIZE),
        "-hls_flags", "delete_segments+temp_file+independent_segments",
        "-hls_segment_filename", seg_pattern,
        playlist,
    ]
    return cmd


class LiveRelaySupervisor:
    """Owns all active live-channel HLS sessions and their idle reaper."""

    def __init__(
        self,
        *,
        cache_root: Optional[str] = None,
        idle_seconds: float = IDLE_STOP_SECONDS,
        spawner: Optional[Callable[[Dict[str, Any], str], Any]] = None,
    ):
        self._lock = threading.RLock()
        self._sessions: Dict[str, LiveSession] = {}
        self._idle_seconds = float(idle_seconds)
        self._cache_root = cache_root or os.path.join("cache", "live")
        self._spawner = spawner or self._spawn_ffmpeg

    def _spawn_ffmpeg(self, channel: Dict[str, Any], work_dir: str):
        cmd = _default_ffmpeg_cmd(channel, work_dir)
        logger.info("live relay start: %s (%s)", channel.get("display_name") or channel["id"], channel["id"])
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def join(self, channel: Dict[str, Any], viewer_id: str) -> Dict[str, Any]:
        """Attach a viewer to a channel's shared HLS session, starting it if needed.
        Enforces source tuner_count when > 0."""
        cid = str(channel["id"])
        sid = str(channel["source_id"])
        tuner_limit = int(channel.get("tuner_count") or 0)
        now = time.time()

        with self._lock:
            existing = self._sessions.get(cid)
            if existing is not None:
                existing.viewers.add(viewer_id)
                existing.last_touch = now
                existing.empty_since = None
                return self._public(existing)

            # Enforce tuner budget on sources that report a finite tuner_count
            if tuner_limit > 0:
                held = [s for s in self._sessions.values() if s.source_id == sid]
                if len(held) >= tuner_limit:
                    holders = [
                        {"channel_id": s.channel_id, "channel_name": s.channel_name, "viewers": len(s.viewers)}
                        for s in held
                    ]
                    names = ", ".join(h["channel_name"] for h in holders)
                    raise TunerBusyError(
                        f"All {tuner_limit} tuners are busy ({names}).",
                        holders=holders,
                    )

            work_dir = os.path.join(self._cache_root, cid)
            os.makedirs(work_dir, exist_ok=True)
            proc = self._spawner(channel, work_dir)
            sess = LiveSession(
                channel_id=cid,
                source_id=sid,
                channel_name=channel.get("display_name") or channel.get("name") or cid,
                stream_url=str(channel["stream_url"]),
                work_dir=work_dir,
                process=proc,
                viewers={viewer_id},
                started_at=now,
                last_touch=now,
            )
            self._sessions[cid] = sess
            return self._public(sess)

    def leave(self, channel_id: str, viewer_id: str) -> bool:
        with self._lock:
            sess = self._sessions.get(channel_id)
            if not sess:
                return False
            sess.viewers.discard(viewer_id)
            if not sess.viewers and sess.empty_since is None:
                sess.empty_since = time.time()
            return True

    def touch(self, channel_id: str, viewer_id: Optional[str] = None) -> Optional[str]:
        """Mark a session active (called when serving HLS playlist/segments)
        and return its work_dir, or None if not running."""
        with self._lock:
            sess = self._sessions.get(channel_id)
            if not sess:
                return None
            sess.last_touch = time.time()
            if viewer_id:
                sess.viewers.add(viewer_id)
                sess.empty_since = None
            return sess.work_dir

    def reap_idle(self, now: Optional[float] = None) -> List[str]:
        """Stop sessions with no viewers past idle_seconds. Returns stopped channel_ids."""
        ts = time.time() if now is None else float(now)
        stopped: List[str] = []
        with self._lock:
            for cid, sess in list(self._sessions.items()):
                idle = (sess.empty_since is not None and (ts - sess.empty_since) >= self._idle_seconds)
                stale = (ts - sess.last_touch) >= max(self._idle_seconds * 4, 180.0)
                if idle or stale:
                    self._stop_locked(cid)
                    stopped.append(cid)
        return stopped

    def stop_channel(self, channel_id: str) -> bool:
        with self._lock:
            return self._stop_locked(channel_id)

    def stop_all(self) -> None:
        with self._lock:
            for cid in list(self._sessions.keys()):
                self._stop_locked(cid)

    def active_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._public(s) for s in self._sessions.values()]

    def _stop_locked(self, channel_id: str) -> bool:
        sess = self._sessions.pop(channel_id, None)
        if not sess:
            return False
        proc = sess.process
        if proc is not None and hasattr(proc, "terminate"):
            try:
                proc.terminate()
                if hasattr(proc, "wait"):
                    proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    if hasattr(proc, "kill"):
                        proc.kill()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("live relay kill failed for %s: %s", channel_id, exc)
        try:
            shutil.rmtree(sess.work_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("live relay cleanup failed for %s: %s", channel_id, exc)
        logger.info("live relay stopped: %s", channel_id)
        return True

    @staticmethod
    def _public(sess: LiveSession) -> Dict[str, Any]:
        return {
            "channel_id": sess.channel_id,
            "source_id": sess.source_id,
            "channel_name": sess.channel_name,
            "viewers": len(sess.viewers),
            "playlist_url": f"/api/live/stream/{sess.channel_id}/stream.m3u8",
            "started_at": sess.started_at,
        }


_SUPERVISOR: Optional[LiveRelaySupervisor] = None
_SUP_LOCK = threading.Lock()


def get_supervisor() -> LiveRelaySupervisor:
    global _SUPERVISOR
    with _SUP_LOCK:
        if _SUPERVISOR is None:
            _SUPERVISOR = LiveRelaySupervisor()
        return _SUPERVISOR
