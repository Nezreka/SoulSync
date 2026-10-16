"""Embedded subtitle extraction as a chain provider.

Videos often carry subtitles inside the container already — extracting them
is free, instant, and perfectly timed, so it beats downloading when the right
language is embedded. One candidate per matching-language text subtitle
stream; bitmap-based streams (PGS/DVD) need OCR and are skipped with no
candidate rather than half-supported.

Local like Whisper (``burns_network_quota = False`` — never touches or waits
on the network quota), but FAST (seconds): ``background_download_only`` stays
False so the import hook can extract inline instead of deferring to the
worker.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery, flag_filter


def _tools_dir() -> Path:
    """<repo>/tools — the same convention Sample Studio uses
    (``core/sample/analyze.py``): ffmpeg on PATH, else the copy here."""
    return Path(__file__).resolve().parents[4] / "tools"


def resolve_ffmpeg() -> str | None:
    """ffmpeg on PATH, else the copy SoulSync keeps in tools/. Never raises."""
    try:
        found = shutil.which("ffmpeg")
        if found:
            return found
        names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
        for name in names:
            cand = _tools_dir() / name
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_ffprobe(ffmpeg_bin: str | None = None) -> str | None:
    """ffprobe on PATH, else the binary sitting next to the resolved ffmpeg.
    Never raises."""
    try:
        found = shutil.which("ffprobe")
        if found:
            return found
        if ffmpeg_bin:
            name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
            sibling = Path(ffmpeg_bin).parent / name
            if sibling.is_file() and os.access(sibling, os.X_OK):
                return str(sibling)
    except Exception:  # noqa: BLE001
        pass
    return None


#: Subtitle codecs ffmpeg can extract to SRT text. Bitmap-based codecs
#: (hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle, xsub, …) need OCR — they
#: get NO candidate rather than a half-working one. Unknown codecs fail
#: closed (skipped) for the same reason.
_TEXT_SUBTITLE_CODECS = frozenset({
    "subrip", "ass", "ssa", "webvtt", "mov_text",
})


def _stream_language(stream: dict) -> str | None:
    """The stream's ISO 639-1 language, or None when unknown/untagged."""
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return None
    from ..languages import to_code
    return to_code(tags.get("language"))


def _probe_subtitle_streams(ffprobe: str, file_path: str) -> list[dict]:
    """ffprobe's subtitle streams for the file, in file order. [] on any
    failure — a failed probe is a miss, never fatal."""
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "s",
             "-show_entries",
             "stream=index,codec_name:stream_disposition=default,forced,hearing_impaired"
             ":stream_tags=language,title",
             "-of", "json", str(file_path)],
            capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return []
    streams = data.get("streams")
    return [s for s in streams if isinstance(s, dict)]


class EmbeddedProvider(SubtitleProvider):
    """Extract text subtitles already inside the video container.

    Double-checked like Whisper: ``is_configured()`` requires a usable ffmpeg
    AND ffprobe every call. ``search()`` is one ffprobe (fast); the actual
    extraction happens in ``download()`` (seconds — safe inline).
    """

    id = "embedded"
    display_name = "Embedded"

    burns_network_quota = False
    background_download_only = False

    def __init__(self) -> None:
        self._ffmpeg: str | None = None
        self._ffprobe: str | None = None

    @property
    def needs_key(self) -> bool:
        return False

    def is_configured(self, get_setting: Callable[[str, Any], Any]) -> bool:
        try:
            self._ffmpeg = resolve_ffmpeg()
            self._ffprobe = resolve_ffprobe(self._ffmpeg)
            return self._ffmpeg is not None and self._ffprobe is not None
        except Exception:  # noqa: BLE001 - a broken check reads as unconfigured
            self._ffmpeg = self._ffprobe = None
            return False

    def _binaries(self) -> tuple[str | None, str | None]:
        """Lazily (re)resolve — search() must work even if is_configured()
        wasn't called first (defense in depth; the chain always calls it)."""
        if not self._ffprobe or not self._ffmpeg:
            ffmpeg = resolve_ffmpeg()
            self._ffmpeg = ffmpeg
            self._ffprobe = resolve_ffprobe(ffmpeg)
        return self._ffmpeg, self._ffprobe

    def search(self, query: SubtitleQuery) -> list[SubtitleCandidate]:
        _ffmpeg, ffprobe = self._binaries()
        if not ffprobe:
            return []
        file_path = getattr(query, "file_path", None)
        if not file_path or not os.path.isfile(str(file_path)):
            return []
        want = str(getattr(query, "language", None) or "en").strip().lower()
        out: list[SubtitleCandidate] = []
        # sub_idx is the SUBTITLE-relative index (0:s:N) — ffprobe's absolute
        # stream index would mis-map when audio/video streams come first.
        for sub_idx, stream in enumerate(
                _probe_subtitle_streams(ffprobe, str(file_path))):
            codec = str(stream.get("codec_name") or "").strip().lower()
            if codec not in _TEXT_SUBTITLE_CODECS:
                continue  # bitmap/unknown: no candidate, never half-supported
            if _stream_language(stream) != want:
                continue
            disp = stream.get("disposition")
            disp = disp if isinstance(disp, dict) else {}
            hi = bool(disp.get("hearing_impaired"))
            forced = bool(disp.get("forced"))
            tags = stream.get("tags")
            title = ""
            if isinstance(tags, dict):
                title = str(tags.get("title") or "").strip()
            out.append(SubtitleCandidate(
                provider_id=self.id,
                language=want,
                hi=hi,
                forced=forced,
                title=title or "Embedded %s subtitles (%s)" % (want, codec),
                download_ref={"file_path": str(file_path),
                              "sub_index": sub_idx},
                # Extracting from the file itself is stronger than a
                # hash match: perfect timing, guaranteed language. Without
                # this the filename-similarity score gates every embedded
                # candidate out and the provider can never fire.
                hash_match=True,
            ))
        # same flag rules as the network providers: a forced-only stream
        # must never land as the plain sidecar.
        return flag_filter(out, query)

    def download(self, candidate: SubtitleCandidate) -> str | None:
        ffmpeg, _ffprobe = self._binaries()
        if not ffmpeg:
            return None
        ref = getattr(candidate, "download_ref", None)
        if not isinstance(ref, dict):
            return None
        file_path = ref.get("file_path")
        try:
            sub_index = int(ref.get("sub_index"))
        except (TypeError, ValueError):
            return None
        if not file_path or not os.path.isfile(str(file_path)) \
                or sub_index < 0:
            return None
        try:
            proc = subprocess.run(
                [ffmpeg, "-v", "error", "-i", str(file_path),
                 "-map", "0:s:%d" % sub_index, "-f", "srt", "-"],
                capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        try:
            text = proc.stdout.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
        return text if text and text.strip() else None


__all__ = [
    "EmbeddedProvider", "resolve_ffmpeg", "resolve_ffprobe",
]
