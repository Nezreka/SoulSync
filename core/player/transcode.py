"""playing a video file a browser can't play as it is: ffmpeg turns it into
something it can, streamed as it's made.

the least work that plays:
  * a stream a browser already plays (h264, vp9, av1 video; aac, mp3, opus
    audio) is copied, untouched
  * anything else is converted: video to h264 (1080p at most, 8 bit), audio
    to stereo aac
so a common mkv of h264 with ac3 sound costs only an audio conversion.

out comes fragmented mp4 on a pipe: playable from the first byte, no file
on disk. seeking starts a new ffmpeg at the new time (``start``); the
player knows the stream began there and adds it to the clock.

the plan and the command are plain functions so they're tested without
ffmpeg; ``stream`` runs it, at most MAX_JOBS at once, and kills ffmpeg when
the viewer goes away.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from utils.logging_config import get_logger

logger = get_logger("player.transcode")

MAX_JOBS = 3
CHUNK = 256 * 1024
MAX_HEIGHT = 1080
VIDEO_CRF = "21"
AUDIO_KBPS = "192k"
MIME = "video/mp4"
# audio sync goes this far either way
MAX_DELAY_MS = 10_000

_slots = threading.BoundedSemaphore(MAX_JOBS)
# each screen's running conversion, so its next one can stop it
_owners: Dict[str, Any] = {}
_owners_lock = threading.Lock()


class Busy(Exception):
    """every transcode slot is taken"""


@dataclass(frozen=True)
class Plan:
    copy_video: bool
    copy_audio: bool
    reasons: List[str] = field(default_factory=list)
    quality: str = "original"
    tonemap: bool = False
    # which audio track (its number among the file's audio), and an image
    # subtitle to burn into the picture (its number among the subtitles)
    audio: int = 0
    burn: Optional[int] = None
    # audio sync: the sound moved this many milliseconds (later +, earlier -)
    delay_ms: int = 0

    @property
    def needed(self) -> bool:
        return not (self.copy_video and self.copy_audio)

    def public(self, encoder_label: str = "") -> dict:
        """what the player shows about it"""
        return {"video": "copy" if self.copy_video else "h264",
                "audio": "copy" if self.copy_audio else "aac", "reasons": self.reasons,
                "quality": self.quality, "tonemap": self.tonemap,
                "encoder": None if self.copy_video else encoder_label}


# what a picked quality means: the tallest picture, and a ceiling on the
# bitrate so a slow connection keeps up (None: as good as the source allows)
QUALITIES = {
    "original": (MAX_HEIGHT, None),
    "1080p": (1080, "8M"),
    "720p": (720, "4M"),
    "480p": (480, "1500k"),
}


def plan(video_codec: Optional[str], audio_codec: Optional[str], *,
         quality: str = "original", hdr: Optional[str] = None,
         height: Optional[int] = None, audio: int = 0, burn: Optional[int] = None,
         delay_ms: int = 0) -> Plan:
    """copy what a browser plays, convert the rest. unknown counts as
    convert: a guess that fails is a silent picture. a quality below the
    source's own height converts the video too (it has to shrink), and HDR
    being converted is tone mapped so it doesn't come out washed out."""
    from core.video.direct_play import stream_verdicts
    v = stream_verdicts(video_codec, audio_codec)
    max_h = QUALITIES.get(quality, QUALITIES["original"])[0]
    shrink = quality != "original" and (height is None or height > max_h)
    # burning a subtitle in draws on the picture: the video is converted
    copy_video = v["video"] == "yes" and not shrink and burn is None
    copy_audio = v["audio"] == "yes"
    reasons = []
    if not copy_video:
        reasons.append(f"{(video_codec or 'unknown').upper()} video to H.264"
                       + (f" at {quality}" if shrink else "")
                       + (" with subtitles drawn in" if burn is not None else ""))
    if not copy_audio:
        reasons.append(f"{(audio_codec or 'unknown').upper()} audio to AAC")
    delay_ms = max(-MAX_DELAY_MS, min(MAX_DELAY_MS, int(delay_ms or 0)))
    if delay_ms:
        reasons.append(f"sound moved {'later' if delay_ms > 0 else 'earlier'} by {abs(delay_ms)} ms")
    return Plan(copy_video, copy_audio, reasons, quality=quality if quality in QUALITIES else "original",
                tonemap=bool(hdr) and not copy_video, audio=max(0, int(audio or 0)), burn=burn,
                delay_ms=delay_ms)


# ── the encoder: the GPU's when it works, else the CPU's ────────────────

# tried in order; each is (ffmpeg encoder, what to call it, input options, filter tail)
ENCODERS = (
    ("h264_nvenc", "NVIDIA GPU", [], ""),
    ("h264_qsv", "Intel Quick Sync", [], ""),
    ("h264_vaapi", "VA-API GPU", ["-vaapi_device", "/dev/dri/renderD128"], ",format=nv12,hwupload"),
    ("h264_videotoolbox", "Apple VideoToolbox", [], ""),
)
_encoder: Optional[tuple] = None
_encoder_checked = False
_encoder_lock = threading.Lock()


def _works(ffmpeg: str, name: str, pre: List[str], tail: str) -> bool:
    """a real one-frame encode: an encoder listed but without its hardware fails here"""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *pre,
           "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.1",
           "-vf", "format=yuv420p" + tail, "-frames:v", "1", "-c:v", name, "-f", "null", "-"]
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # noqa: S603
                              timeout=20).returncode == 0
    except Exception:  # noqa: BLE001 - not there, not working: the next one
        return False


def encoder(ffmpeg: str) -> tuple:
    """(encoder, label, input options, filter tail), found once per run"""
    global _encoder, _encoder_checked
    with _encoder_lock:
        if not _encoder_checked:
            _encoder = next((e for e in ENCODERS if _works(ffmpeg, e[0], e[2], e[3])), None)
            _encoder_checked = True
            logger.info("player transcoding on %s", _encoder[1] if _encoder else "the CPU (libx264)")
        return _encoder or ("libx264", "CPU", [], "")


_filters: dict = {}


def has_filter(ffmpeg: str, name: str) -> bool:
    """is this ffmpeg built with that filter (zscale needs libzimg), asked once"""
    key = (ffmpeg, name)
    if key not in _filters:
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,  # noqa: S603
                                 text=True, timeout=20).stdout
            _filters[key] = any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())
        except Exception:  # noqa: BLE001 - can't tell: no
            _filters[key] = False
    return _filters[key]


def _video_args(enc: tuple, p: Plan, tonemap: bool) -> List[str]:
    name, _label, _pre, tail = enc
    max_h, cap = QUALITIES[p.quality]
    chain: List[str] = []
    if tonemap:
        # HDR to SDR: linearise, map the highlights down, back to bt709
        chain.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                     "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
    # never upscale; anything taller comes down to the quality's height
    chain.append(f"scale=-2:'min({max_h},ih)'")
    chain.append("format=yuv420p")
    if p.burn is not None:
        # the subtitle's pictures over the video, then the same chain
        graph = f"[0:v:0][0:s:{p.burn}]overlay," + ",".join(chain) + tail + "[v]"
        args = ["-filter_complex", graph, "-c:v", name]
    else:
        args = ["-vf", ",".join(chain) + tail, "-c:v", name]
    if name == "libx264":
        args += ["-preset", "veryfast", "-crf", VIDEO_CRF, "-profile:v", "high"]
    elif name == "h264_nvenc":
        args += ["-preset", "p4", "-cq", VIDEO_CRF, "-profile:v", "high"]
    elif name == "h264_qsv":
        args += ["-preset", "faster", "-global_quality", VIDEO_CRF]
    if cap:
        args += ["-maxrate", cap, "-bufsize", cap]
    # a keyframe every 2 seconds: fragments stay small, starts stay quick
    args += ["-g", "48", "-keyint_min", "48", "-sc_threshold", "0"]
    return args


def command(ffmpeg: str, source: str, p: Plan, start: float = 0.0,
            enc: Optional[tuple] = None, can_tonemap: bool = False) -> List[str]:
    """the ffmpeg command for ``source`` (a path, or a media server url) from
    ``start`` seconds. ``enc`` is encoder()'s answer (the CPU's when left out);
    ``can_tonemap``: this ffmpeg has zscale (without it HDR plays, untouched)."""
    enc = enc or ("libx264", "CPU", [], "")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if not p.copy_video:
        cmd += list(enc[2])
    if start > 0:
        # before -i: a fast seek to the keyframe at or before it
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", source]
    cmd += [
            # the picture (or the burned-in graph's), the picked audio track (a
            # file with no audio still plays), no subtitle or data streams out
            "-map", "[v]" if p.burn is not None and not p.copy_video else "0:v:0",
            "-map", f"0:a:{p.audio}?", "-sn", "-dn"]
    cmd += ["-c:v", "copy"] if p.copy_video else _video_args(enc, p, p.tonemap and can_tonemap)
    if p.copy_audio and not p.delay_ms:
        cmd += ["-c:a", "copy"]
    else:
        if p.delay_ms > 0:
            # audio sync, later: silence in front (a muxer would undo a timestamp shift)
            cmd += ["-af", f"adelay={p.delay_ms}:all=1"]
        elif p.delay_ms < 0:
            # earlier: the first part of the sound cut off
            cmd += ["-af", f"atrim=start={-p.delay_ms / 1000:.3f},asetpts=PTS-STARTPTS"]
        cmd += ["-c:a", "aac", "-b:a", AUDIO_KBPS, "-ac", "2"]
    cmd += ["-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", "pipe:1"]
    return cmd


class _Run:
    """ffmpeg's output as a response body. ``close`` (werkzeug calls it when
    the response ends or the viewer goes away, read or not) kills ffmpeg
    and frees the slot, exactly once."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.closed = False
        self.owner: Optional[str] = None

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        chunk = self.proc.stdout.read(CHUNK) if not self.closed else b""
        if chunk:
            return chunk
        self._report()
        self.close()
        raise StopIteration

    def _report(self):
        try:
            if self.proc.poll() not in (0, None):
                err = self.proc.stderr.read().decode("utf-8", "replace").strip()
                if err:
                    logger.warning("transcode ended with an error: %s", err[-500:])
        except Exception:  # noqa: BLE001 - reporting never breaks the stream's end
            logger.debug("couldn't read ffmpeg's error output", exc_info=True)

    def close(self):
        with _owners_lock:
            if self.closed:
                return
            self.closed = True
            if self.owner and _owners.get(self.owner) is self:
                del _owners[self.owner]
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - it's gone or going; the slot is what matters
            logger.debug("ffmpeg didn't exit after kill", exc_info=True)
        finally:
            _slots.release()


def stream(cmd: List[str], owner: Optional[str] = None) -> Iterator[bytes]:
    """run it and hand back its output as it comes (an iterator with a close,
    for a response body). raises Busy when every slot is taken.

    owner: the screen asking (profile and player tab). its earlier conversion
    stops first: a screen watches one thing, and hopping channels used to
    leave the old ones running until every slot was taken."""
    if owner:
        with _owners_lock:
            before = _owners.get(owner)
        if before is not None:
            before.close()
    if not _slots.acquire(blocking=False):
        raise Busy()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  # noqa: S603 - our own argv
                                stdin=subprocess.DEVNULL, bufsize=0)
    except Exception:
        _slots.release()
        raise
    run = _Run(proc)
    if owner:
        run.owner = owner
        with _owners_lock:
            _owners[owner] = run
    return run


def ffprobe_for(ffmpeg: str) -> str:
    """ffprobe sits beside ffmpeg in every build we ship or find"""
    import os
    folder, name = os.path.split(ffmpeg)
    probe = name.lower().replace("ffmpeg", "ffprobe")
    return os.path.join(folder, probe) if folder else probe


def parse_keyframes(text: str) -> List[float]:
    out = []
    for line in text.splitlines():
        value = line.strip().split(",")[0]
        try:
            out.append(float(value))
        except ValueError:
            continue
    return out


# how far past a keyframe to ask ffmpeg to seek, so a copied stream lands on
# it: a keyframe's decode time sits a frame or two before its display time
# (b-frames), and asking for the display time exactly lands on the one before
SEEK_PAST = 0.3


def seek_point(ffprobe: str, source: str, t: float) -> tuple:
    """(start, seek) for a copied video asked to start at ``t``: ``start`` is
    the last keyframe at or before it (where the stream really begins, the
    player's clock), ``seek`` what to hand ffmpeg so it lands there: a little
    past the keyframe, never more than halfway to the next. reads only around
    ``t``. can't tell: (t, t)."""
    if t <= 0:
        return 0.0, 0.0
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
           "-show_entries", "frame=pts_time", "-of", "csv=p=0",
           "-read_intervals", f"{max(0.0, t - 30):.3f}%{t + 15:.3f}", source]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout  # noqa: S603 - our own argv
    except Exception:  # noqa: BLE001 - no answer: start where asked
        logger.debug("keyframe probe failed for %s", source, exc_info=True)
        return t, t
    keys = sorted(parse_keyframes(out))
    before = [k for k in keys if k <= t + 0.001]
    if not before:
        return t, t
    start = before[-1]
    after = [k for k in keys if k > start + 0.001]
    room = (after[0] - start) / 2 if after else SEEK_PAST
    return start, start + min(SEEK_PAST, room)


def ffmpeg_path() -> Optional[str]:
    """the project's ffmpeg (found once, shared with the music-app transcoder)"""
    from core.subsonic.transcode import ffmpeg
    return ffmpeg()
