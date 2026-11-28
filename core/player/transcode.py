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
ffmpeg; ``stream`` runs it, at most the admin's slots at once, and kills
ffmpeg when the viewer goes away. the GPU side (which backend, its
arguments, decoding on the card, falling back to the CPU) is hwaccel.py.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from utils.logging_config import get_logger

logger = get_logger("player.transcode")

MAX_JOBS = 3  # the default; the admin's slots setting (hwaccel.settings) wins
CHUNK = 256 * 1024
# what a run reads before the first byte goes out: a GPU job that dies on its
# first frame is retried on the CPU while the viewer has seen nothing yet
PRIME_BYTES = 64 * 1024
MAX_HEIGHT = 1080
VIDEO_CRF = "21"
AUDIO_KBPS = "192k"
MIME = "video/mp4"
# audio sync goes this far either way
MAX_DELAY_MS = 10_000

# h264 profiles a browser, a phone or a chromecast can't decode
DEEP_H264_PROFILES = ("high 10", "high 4:2:2", "high 4:4:4", "high 10 intra", "high 4:2:2 intra",
                      "high 4:4:4 intra", "high 4:4:4 predictive", "cavlc 4:4:4")


class _Slots:
    """how many conversions run at once: the admin's number, read each time
    so a change applies without a restart"""

    def __init__(self):
        self._lock = threading.Lock()
        self.in_use = 0

    def acquire(self, blocking: bool = False) -> bool:
        from core.player import hwaccel
        limit = hwaccel.settings()["slots"]
        with self._lock:
            if self.in_use >= limit:
                return False
            self.in_use += 1
            return True

    def release(self) -> None:
        with self._lock:
            self.in_use = max(0, self.in_use - 1)


_slots: Any = _Slots()
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
    # the source picture, for decoding it on the GPU: codec, bit depth, pixels
    src_codec: Optional[str] = None
    src_bit_depth: Optional[int] = None
    src_pix_fmt: Optional[str] = None

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
# bitrate so a slow connection keeps up (None: as good as the source allows).
# the ceiling holds for a copied stream too: one over it is converted
QUALITIES = {
    "original": (MAX_HEIGHT, None),
    "1080p": (1080, "8M"),
    "720p": (720, "4M"),
    "480p": (480, "1500k"),
    # a chromecast: 1080p at most, no bitrate ceiling on the home network
    "cast": (1080, None),
}


def _kbps(cap: Optional[str]) -> Optional[int]:
    from core.player.hwaccel import _cap_bits
    bits = _cap_bits(cap)
    return bits // 1000 if bits else None


def _mbps(kbps: float) -> str:
    return f"{kbps / 1000:.1f}".rstrip("0").rstrip(".") + " Mbps"


def _too_deep(codec: str, facts: Dict[str, Any]) -> Optional[str]:
    """h264 that only a PC decodes: 10-bit (Hi10P anime), 4:2:2, 4:4:4. why, or None"""
    if codec not in ("h264", "avc", "avc1", "x264"):
        return None
    depth = facts.get("bit_depth") or 8
    profile = str(facts.get("profile") or "").lower()
    pix = str(facts.get("pix_fmt") or "")
    if depth > 8 or "high 10" in profile:
        return f"{depth if depth > 8 else 10}-bit H.264 to 8-bit H.264"
    if "422" in pix or "4:2:2" in profile:
        return "4:2:2 H.264 to 4:2:0 H.264"
    if "444" in pix or "4:4:4" in profile:
        return "4:4:4 H.264 to 4:2:0 H.264"
    return None


def plan(video_codec: Optional[str], audio_codec: Optional[str], *,
         quality: str = "original", hdr: Optional[str] = None,
         height: Optional[int] = None, audio: int = 0, burn: Optional[int] = None,
         delay_ms: int = 0, codec_facts: Optional[Dict[str, Any]] = None,
         copyable: Optional[tuple] = None) -> Plan:
    """copy what a browser plays, convert the rest. unknown counts as
    convert: a guess that fails is a silent picture. a quality below the
    source's own height converts the video too (it has to shrink), and HDR
    being converted is tone mapped so it doesn't come out washed out.

    codec_facts (the probed video stream: bit_depth, profile, pix_fmt,
    bitrate_kbps): 10-bit or 4:2:2 h264 is converted, and a copy over the
    quality's bitrate ceiling is converted down to it.
    copyable: (video codecs, audio codecs) another client plays, in place
    of the browser table (a chromecast)."""
    from core.video.direct_play import _norm, stream_verdicts
    facts = codec_facts or {}
    if copyable is not None:
        vids, auds = copyable
        v = {"video": "yes" if _norm(video_codec) in vids else "no",
             "audio": "yes" if _norm(audio_codec) in auds else "no"}
    else:
        v = stream_verdicts(video_codec, audio_codec)
    quality = quality if quality in QUALITIES else "original"
    max_h, cap = QUALITIES[quality]
    qlabel = f"{max_h}p" if quality == "cast" else quality
    # an unknown height shrinks for a picked quality (it may be bigger); a
    # chromecast copies it (a guess that converts every cast is worse)
    shrink = quality != "original" and (height > max_h if height else quality != "cast")
    deep = _too_deep(_norm(video_codec), facts)
    cap_kbps = _kbps(cap)
    src_kbps = facts.get("bitrate_kbps")
    over = bool(cap_kbps and src_kbps and src_kbps > cap_kbps)
    # burning a subtitle in draws on the picture: the video is converted
    copy_video = v["video"] == "yes" and not shrink and burn is None and not deep and not over
    copy_audio = v["audio"] == "yes"
    reasons = []
    if not copy_video:
        if deep and v["video"] == "yes":
            reasons.append(deep + (f" at {qlabel}" if shrink else "")
                           + (" with subtitles drawn in" if burn is not None else ""))
        else:
            reasons.append(f"{(video_codec or 'unknown').upper()} video to H.264"
                           + (f" at {qlabel}" if shrink or over else "")
                           + (f" (the source's {_mbps(src_kbps)} is over {_mbps(cap_kbps)})" if over else "")
                           + (" with subtitles drawn in" if burn is not None else ""))
    if not copy_audio:
        reasons.append(f"{(audio_codec or 'unknown').upper()} audio to AAC")
    delay_ms = max(-MAX_DELAY_MS, min(MAX_DELAY_MS, int(delay_ms or 0)))
    if delay_ms:
        reasons.append(f"sound moved {'later' if delay_ms > 0 else 'earlier'} by {abs(delay_ms)} ms")
    return Plan(copy_video, copy_audio, reasons, quality=quality,
                tonemap=bool(hdr) and not copy_video, audio=max(0, int(audio or 0)), burn=burn,
                delay_ms=delay_ms, src_codec=(facts.get("codec") or _norm(video_codec) or None),
                src_bit_depth=facts.get("bit_depth"), src_pix_fmt=facts.get("pix_fmt"))


# ── the encoder: the GPU's when it works, else the CPU's ────────────────

def _works(ffmpeg: str, name: str, pre: List[str], tail: str) -> bool:
    """a real one-frame encode (kept for callers of the old shape)"""
    from core.player import hwaccel
    return hwaccel.try_encode(ffmpeg, hwaccel.enc_for((name, name, pre, tail)))[0]


def encoder(ffmpeg: str):
    """the encoder to use: an Enc (indexable like the old 4-tuple:
    encoder, label, input options, filter tail). the admin's GPU setting,
    tested once per setting change"""
    from core.player import hwaccel
    return hwaccel.detect(ffmpeg)


_filters: dict = {}


def has_filter(ffmpeg: str, name: str) -> bool:
    """is this ffmpeg built with that filter (zscale needs libzimg), asked once"""
    key = (ffmpeg, name)
    if key not in _filters:
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,  # noqa: S603
                                 text=True, errors="replace", timeout=20).stdout
            _filters[key] = any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())
        except Exception:  # noqa: BLE001 - can't tell: no
            _filters[key] = False
    return _filters[key]


def _video_args(enc: Any, p: Plan, tonemap: bool, gpu_frames: bool = False,
                keyframes: Optional[List[str]] = None) -> List[str]:
    """the video encode. gpu_frames: the decoder left the frames on the GPU,
    so the GPU's own scaler is used and nothing comes back to the CPU.
    keyframes: in place of a keyframe every 2 seconds (HLS cuts)"""
    from core.player import hwaccel
    enc = hwaccel.enc_for(enc)
    max_h, cap = QUALITIES[p.quality]
    if gpu_frames:
        args = ["-vf", hwaccel.gpu_scale(enc, max_h), "-c:v", enc.name]
    else:
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
            graph = f"[0:v:0][0:s:{p.burn}]overlay," + ",".join(chain) + enc.tail + "[v]"
            args = ["-filter_complex", graph, "-c:v", enc.name]
        else:
            args = ["-vf", ",".join(chain) + enc.tail, "-c:v", enc.name]
    args += hwaccel.rate_args(enc, cap, max_h, VIDEO_CRF)
    if keyframes:
        args += keyframes
    else:
        # a keyframe every 2 seconds: fragments stay small, starts stay quick
        args += ["-g", "48", "-keyint_min", "48", "-sc_threshold", "0"]
    return args


@dataclass
class Job:
    """a command and what it needs if the GPU lets it down"""
    cmd: List[str]
    enc: Any = None
    # (codec, bit depth) decoded on the GPU, or None
    hw_decode: Optional[tuple] = None
    # the same job on the CPU, for a GPU that fails before the first byte
    cpu_cmd: Optional[List[str]] = None
    ffmpeg: str = "ffmpeg"

    @property
    def label(self) -> str:
        return self.enc.label if self.enc is not None else "CPU"


def gpu_input(enc: Any, p: Plan, tonemap: bool) -> tuple:
    """(input options, frames stay on the GPU, (codec, depth) decoded there or None)"""
    from core.player import hwaccel
    enc = hwaccel.enc_for(enc)
    if p.copy_video:
        return [], False, None
    pre = list(enc.pre)
    if enc.key == "cpu" or not hwaccel.can_decode(enc.key, p.src_codec, p.src_bit_depth, p.src_pix_fmt):
        return pre, False, None
    # tone mapping and burn-in still run on the CPU: the GPU decodes, the
    # frames come back for those filters
    on_gpu = not tonemap and p.burn is None and hwaccel.gpu_scale(enc, 1080) is not None
    pre += hwaccel.decode_args(enc, on_gpu)
    return pre, on_gpu, (str(p.src_codec).lower(), p.src_bit_depth or 8)


def build(ffmpeg: str, source: str, p: Plan, start: float = 0.0, enc: Any = None,
          can_tonemap: bool = False) -> Job:
    """the ffmpeg job for ``source`` (a path, or a media server url) from
    ``start`` seconds, decoded and encoded on the GPU where it can be"""
    from core.player import hwaccel
    enc = hwaccel.enc_for(enc)
    tonemap = p.tonemap and can_tonemap
    pre, on_gpu, hw_decode = gpu_input(enc, p, tonemap)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    cmd += pre
    if start > 0:
        # before -i: a fast seek to the keyframe at or before it
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", source]
    cmd += [
            # the picture (or the burned-in graph's), the picked audio track (a
            # file with no audio still plays), no subtitle or data streams out
            "-map", "[v]" if p.burn is not None and not p.copy_video else "0:v:0",
            "-map", f"0:a:{p.audio}?", "-sn", "-dn"]
    cmd += ["-c:v", "copy"] if p.copy_video else _video_args(enc, p, tonemap, on_gpu)
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
    gpu = not p.copy_video and enc.key != "cpu"
    cpu_cmd = build(ffmpeg, source, p, start, hwaccel.CPU, can_tonemap).cmd if gpu else None
    return Job(cmd, enc if not p.copy_video else None, hw_decode, cpu_cmd, ffmpeg)


def command(ffmpeg: str, source: str, p: Plan, start: float = 0.0,
            enc: Optional[tuple] = None, can_tonemap: bool = False) -> List[str]:
    """build()'s command alone. ``enc`` is encoder()'s answer (the CPU's when
    left out); ``can_tonemap``: this ffmpeg has zscale (without it HDR plays,
    untouched)."""
    return build(ffmpeg, source, p, start, enc, can_tonemap).cmd


def _err_tail(err_file) -> str:
    try:
        err_file.seek(0, 2)
        size = err_file.tell()
        err_file.seek(max(0, size - 2000))
        return err_file.read().decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001 - no words is still an answer
        return ""


class _Run:
    """ffmpeg's output as a response body. ``close`` (werkzeug calls it when
    the response ends or the viewer goes away, read or not) kills ffmpeg
    and frees the slot, exactly once.

    a GPU job that ends in an error before PRIME_BYTES came out is run again
    on the CPU, in the same slot: the viewer sees a slower start, not a
    dead stream. the GPU is checked and marked (hwaccel.mark_failed)."""

    def __init__(self, proc: subprocess.Popen, err_file=None, job: Optional[Job] = None):
        self.proc = proc
        # ffmpeg's stderr goes to a temp file, not a pipe: nobody reads a pipe
        # until the end, and a noisy file filled it and stalled ffmpeg
        self.err_file = err_file
        self.closed = False
        self.owner: Optional[str] = None
        self.job = job
        self.primed = False
        self.used = {"encoder": job.label if job else None,
                     "hw_decode": bool(job and job.hw_decode), "fell_back": None}

    def __iter__(self):
        return self

    def _prime(self) -> bytes:
        buf = bytearray()
        while len(buf) < PRIME_BYTES:
            chunk = self.proc.stdout.read(CHUNK)
            if not chunk:
                break
            buf += chunk
        return bytes(buf)

    def _failed(self) -> bool:
        try:
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - still running: not a failure we can see
            return False
        return self.proc.poll() not in (0, None)

    def _fall_back(self) -> bool:
        """the GPU run died before anything went out: the CPU's turn. the GPU
        is only marked once the CPU run works: a file that fails everywhere
        (a broken file, a muxer that won't take its sound) isn't the GPU's fault"""
        job = self.job
        if not job or not job.cpu_cmd or self.closed:
            return False
        from core.player import hwaccel
        self._gpu_failure = (job, hwaccel._words(_err_tail(self.err_file).encode())
                             if self.err_file is not None else "")
        import tempfile
        err_file = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(job.cpu_cmd, stdout=subprocess.PIPE, stderr=err_file,  # noqa: S603 - our own argv
                                    stdin=subprocess.DEVNULL, bufsize=0)
        except Exception:  # noqa: BLE001 - no CPU run either: the stream ends
            err_file.close()
            logger.exception("couldn't start the CPU retry")
            return False
        if self.err_file is not None:
            try:
                self.err_file.close()
            except OSError:
                pass
        self.proc, self.err_file, self.job = proc, err_file, None
        self.used = {"encoder": "CPU", "hw_decode": False, "fell_back": "the GPU failed, so the CPU took over"}
        return True

    def _blame_gpu(self) -> None:
        """the CPU run worked where the GPU's didn't: now the GPU is checked and marked"""
        failed = getattr(self, "_gpu_failure", None)
        if not failed:
            return
        self._gpu_failure = None
        from core.player import hwaccel
        job, reason = failed
        try:
            self.used["fell_back"] = hwaccel.mark_failed(job.ffmpeg, hwaccel.enc_for(job.enc), job.hw_decode, reason)
        except Exception:  # noqa: BLE001 - marking never breaks the stream
            logger.debug("couldn't mark the GPU", exc_info=True)

    def __next__(self) -> bytes:
        if self.closed:
            raise StopIteration
        if not self.primed:
            data = self._prime()
            if (len(data) < PRIME_BYTES and self.job is not None and self.job.cpu_cmd
                    and self._failed() and self._fall_back()):
                data = self._prime()
                if data and not (len(data) < PRIME_BYTES and self._failed()):
                    self._blame_gpu()
            self.primed = True
            if data:
                return data
        else:
            data = self.proc.stdout.read(CHUNK)
            if data:
                return data
        self._report()
        self.close()
        raise StopIteration

    def _report(self):
        try:
            if self.proc.poll() not in (0, None) and self.err_file is not None:
                err = _err_tail(self.err_file)
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
            if self.err_file is not None:
                try:
                    self.err_file.close()
                except OSError:
                    logger.debug("couldn't close ffmpeg's error log", exc_info=True)


def stream(cmd: Any, owner: Optional[str] = None) -> Iterator[bytes]:
    """run it (a command, or a Job that can fall back to the CPU) and hand
    back its output as it comes (an iterator with a close, for a response
    body). raises Busy when every slot is taken.

    owner: the screen asking (profile and player tab). its earlier conversion
    stops first: a screen watches one thing, and hopping channels used to
    leave the old ones running until every slot was taken."""
    job = cmd if isinstance(cmd, Job) else None
    argv = job.cmd if job else cmd
    if owner:
        with _owners_lock:
            before = _owners.get(owner)
        if before is not None:
            before.close()
    if not _slots.acquire(blocking=False):
        raise Busy()
    err_file = None
    try:
        import tempfile
        err_file = tempfile.TemporaryFile()
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=err_file,  # noqa: S603 - our own argv
                                stdin=subprocess.DEVNULL, bufsize=0)
    except Exception:
        _slots.release()
        if err_file is not None:
            err_file.close()
        raise
    run = _Run(proc, err_file, job)
    if owner:
        run.owner = owner
        with _owners_lock:
            _owners[owner] = run
    return run


def running(owner: str) -> Optional[Dict[str, Any]]:
    """what this screen's conversion really runs on (after any CPU retry)"""
    with _owners_lock:
        run = _owners.get(owner)
    return dict(run.used) if run is not None and not run.closed else None


def stop_owner(owner: str) -> bool:
    """end this screen's running conversion (an admin stopped it). True when
    one was running"""
    with _owners_lock:
        run = _owners.get(owner)
    if run is None:
        return False
    run.close()
    return True


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
        out = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30).stdout  # noqa: S603 - our own argv
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
