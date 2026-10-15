"""Subtitle text out of whatever a provider sends back.

Providers hand back a bare .srt, a zip, or (subsource's subscene-era uploads)
a RAR. zip goes through the stdlib; RAR and 7z go through ``bsdtar``
(libarchive — in the docker image; windows ships it as tar.exe). Without
bsdtar a RAR is a miss, with one log line saying how to fix it.

Picking the file inside an archive: the first real .srt that isn't a macOS
shadow file or a commentary track (movie archives often carry the director's
commentary next to the film's own subtitles).

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import zipfile

from utils.logging_config import get_logger

logger = get_logger("video.subtitles.archive")

#: bigger than any subtitle archive; a guard against junk, not a real limit.
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
#: a single subtitle file never gets near this.
MAX_MEMBER_BYTES = 10 * 1024 * 1024

_warned_no_bsdtar = False


def pick_member(names: list[str]) -> str | None:
    """The .srt to use from an archive listing, or None. Pure.

    Skips AppleDouble shadows (``__MACOSX/._movie.srt`` also ends in .srt
    but is binary metadata), dotfiles and commentary tracks.
    """
    for n in names:
        norm = n.replace("\\", "/")
        if norm.startswith("__MACOSX/") or "/__MACOSX/" in norm:
            continue
        base = norm.rsplit("/", 1)[-1]
        if base.startswith(".") or not base.lower().endswith(".srt"):
            continue
        if "commentary" in base.lower():
            continue  # never a commentary track
        return n
    return None


def decode_srt(data: bytes) -> str | None:
    """Text of an srt payload, or None when it isn't one ('-->' timings).
    Tries utf-8 (with/without bom), then the usual western fallbacks. Pure."""
    if not data:
        return None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        if "-->" in text:
            return text
    return None


def _bsdtar() -> str | None:
    found = shutil.which("bsdtar")
    if found:
        return found
    if os.name == "nt":
        return shutil.which("tar")  # windows' tar.exe is bsdtar
    return None


def _extract_with_bsdtar(payload: bytes) -> str | None:
    global _warned_no_bsdtar
    tool = _bsdtar()
    if not tool:
        if not _warned_no_bsdtar:
            _warned_no_bsdtar = True
            logger.warning("subtitles: a provider sent a RAR/7z archive and bsdtar isn't "
                           "installed, so it's skipped (install libarchive-tools)")
        return None
    with tempfile.TemporaryDirectory(prefix="soulsync-subarc-") as tmp:
        src = os.path.join(tmp, "archive.bin")
        out = os.path.join(tmp, "out")
        os.mkdir(out)
        with open(src, "wb") as f:
            f.write(payload)
        try:
            # bsdtar refuses absolute paths and '..' members without -P, so
            # nothing lands outside the temp dir.
            proc = subprocess.run([tool, "-xf", src, "-C", out],
                                  capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        members = []
        for root, _dirs, files in os.walk(out):
            for name in files:
                members.append(os.path.relpath(os.path.join(root, name), out))
        member = pick_member(sorted(members))
        if not member:
            return None
        path = os.path.join(out, member)
        try:
            if os.path.getsize(path) > MAX_MEMBER_BYTES:
                return None
            with open(path, "rb") as f:
                return decode_srt(f.read())
        except OSError:
            return None


def extract_srt(payload: bytes) -> str | None:
    """SRT text from a provider payload: zip, RAR/7z or a bare .srt. None
    when there's no usable srt inside. Never raises."""
    try:
        if not payload or len(payload) > MAX_ARCHIVE_BYTES:
            return None
        if payload[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                member = pick_member(zf.namelist())
                if not member or zf.getinfo(member).file_size > MAX_MEMBER_BYTES:
                    return None
                return decode_srt(zf.read(member))
        if payload[:4] == b"Rar!" or payload[:6] == b"7z\xbc\xaf\x27\x1c":
            return _extract_with_bsdtar(payload)
        return decode_srt(payload)
    except Exception:  # noqa: BLE001 - a bad archive is a miss
        return None


__all__ = ["extract_srt", "pick_member", "decode_srt"]
