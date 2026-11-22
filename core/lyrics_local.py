"""Lyrics that live next to, or inside, a library file.

The player's lyrics lookup asks here before it spends an LRCLib request:
first the ``.lrc`` / ``.txt`` sidecar, then the lyrics embedded in the
file's own tags (ID3 USLT and synced SYLT, Vorbis LYRICS /
UNSYNCEDLYRICS, MP4 ``\\xa9lyr``). Libraries tagged by beets or Picard
often carry embedded lyrics only.

``apply_lrc_offset`` folds an ``[offset:]`` tag into the timestamps so the
player never has to know about it, and ``cache_sidecar`` keeps an LRCLib
hit next to the file so the next play stays local.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("lyrics_local")

Lyrics = Tuple[Optional[str], Optional[str]]   # (synced LRC, plain text)

_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE | re.MULTILINE)

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus",
    ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv", ".dsf",
}


def is_synced(text: Optional[str]) -> bool:
    """True when the text carries at least one ``[mm:ss]`` line stamp."""
    return bool(text) and bool(_STAMP.search(text))


def _split(text: Optional[str]) -> Lyrics:
    body = (text or "").strip()
    if not body:
        return None, None
    return (body, None) if is_synced(body) else (None, body)


def _fmt_stamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"[{minutes:02d}:{rest:05.2f}]"


def apply_lrc_offset(text: Optional[str]) -> Optional[str]:
    """Fold an LRC ``[offset:+/-ms]`` tag into every line stamp.

    A positive offset shows the lyrics sooner (the LRC convention), so each
    stamp moves earlier by that many milliseconds; stamps never go below
    zero. The offset tag itself is removed. Text without an offset comes
    back unchanged.
    """
    if not text:
        return text
    match = _OFFSET.search(text)
    if not match:
        return text
    shift = int(match.group(1)) / 1000.0
    without_tag = _OFFSET.sub("", text)
    if not shift:
        return without_tag.strip("\n")

    def move(m: re.Match) -> str:
        return _fmt_stamp(int(m.group(1)) * 60 + float(m.group(2)) - shift)

    return _STAMP.sub(move, without_tag).strip("\n")


def read_sidecar(audio_path: str) -> Lyrics:
    """The ``.lrc`` (synced) or ``.txt`` (plain) file beside the audio."""
    stem, _ = os.path.splitext(audio_path)
    for path, synced in ((stem + ".lrc", True), (stem + ".txt", False)):
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read().strip()
        except OSError as exc:
            logger.debug("lyrics sidecar read skipped for %s: %s", path, exc)
            continue
        if body:
            # an .lrc with no stamps is plain text in the wrong file
            return _split(body) if synced else (None, body)
    return None, None


def _sylt_to_lrc(frame: Any) -> Optional[str]:
    """An ID3 SYLT frame (millisecond stamps) as an LRC string."""
    if getattr(frame, "format", 2) != 2:   # 2 = milliseconds; MPEG frames can't be mapped
        return None
    lines = []
    for item in getattr(frame, "text", None) or []:
        try:
            text, stamp = item
            lines.append(f"{_fmt_stamp(int(stamp) / 1000.0)}{str(text).strip()}")
        except (TypeError, ValueError):
            continue
    return "\n".join(lines) or None


def read_embedded(audio_path: str) -> Lyrics:
    """Lyrics stored in the file's own tags. Never raises.

    Synced wins over plain: a SYLT frame, or a USLT / LYRICS / ``\\xa9lyr``
    value that carries LRC stamps (SoulSync embeds LRCLib's synced text
    that way), is returned as synced.
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
    except Exception:  # noqa: BLE001 - mutagen missing means no tags to read
        return None, None
    try:
        audio = MutagenFile(audio_path)
    except Exception as exc:  # noqa: BLE001 - an unreadable file has no lyrics
        logger.debug("embedded lyrics read skipped for %s: %s", audio_path, exc)
        return None, None
    if audio is None or audio.tags is None:
        return None, None

    texts = []
    synced_frames = []
    try:
        if isinstance(audio.tags, ID3):
            synced_frames = [_sylt_to_lrc(f) for f in audio.tags.getall("SYLT")]
            texts = [str(f.text) for f in audio.tags.getall("USLT") if getattr(f, "text", None)]
        elif isinstance(audio, MP4):
            texts = [str(v) for v in (audio.tags.get("\xa9lyr") or [])]
        else:
            for key in ("lyrics", "unsyncedlyrics", "LYRICS", "UNSYNCEDLYRICS"):
                try:
                    values = audio.tags.get(key) if hasattr(audio.tags, "get") else None
                except Exception:  # noqa: BLE001 - odd tag containers
                    values = None
                if values:
                    texts.extend(str(v) for v in (values if isinstance(values, list) else [values]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("embedded lyrics parse skipped for %s: %s", audio_path, exc)
        return None, None

    for lrc in synced_frames:
        if lrc:
            return lrc, None
    plain = None
    for text in texts:
        synced, unsynced = _split(text)
        if synced:
            return synced, None
        plain = plain or unsynced
    return None, plain


def read_local(audio_path: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``(synced, plain, source)`` from the sidecar, then the tags.

    ``source`` is ``sidecar`` or ``embedded``; all three are None when the
    file has no local lyrics. A sidecar with plain text only still lets the
    tags supply synced lyrics, since synced is what the player wants.
    """
    if not audio_path or not os.path.isfile(audio_path):
        return None, None, None
    side_synced, side_plain = read_sidecar(audio_path)
    if side_synced:
        return apply_lrc_offset(side_synced), None, "sidecar"
    tag_synced, tag_plain = read_embedded(audio_path)
    if tag_synced:
        return apply_lrc_offset(tag_synced), None, "embedded"
    if side_plain:
        return None, side_plain, "sidecar"
    if tag_plain:
        return None, tag_plain, "embedded"
    return None, None, None


def cache_enabled(config_manager: Any) -> bool:
    """The player may save LRCLib hits beside library files.

    On unless switched off, and never when .lrc files are switched off for
    downloads (``metadata_enhancement.lrclib_enabled``).
    """
    if config_manager is None:
        return False
    try:
        if config_manager.get("metadata_enhancement.lrclib_enabled", True) is False:
            return False
        return config_manager.get("metadata_enhancement.lrclib_cache_player", True) is not False
    except Exception:  # noqa: BLE001
        return False


def cache_sidecar(audio_path: Optional[str], synced: Optional[str], plain: Optional[str],
                  config_manager: Any) -> bool:
    """Save LRCLib lyrics next to a library file. Returns True when written.

    Writes only when the setting allows it, the file is an audio file
    inside a library folder, its folder is writable, its album isn't
    locked, and no sidecar is there yet. Synced lyrics go to ``.lrc``,
    plain ones to ``.txt`` (the same split the download pipeline uses).
    The audio file itself is never touched.
    """
    body = (synced or plain or "").strip()
    if not body or not audio_path or not cache_enabled(config_manager):
        return False
    if os.path.splitext(audio_path)[1].lower() not in AUDIO_EXTENSIONS:
        return False
    if not os.path.isfile(audio_path):
        return False
    try:
        from core.library.path_resolver import library_base_dirs
        roots = library_base_dirs(config_manager)
    except Exception as exc:  # noqa: BLE001
        logger.debug("lyrics cache: library roots unavailable: %s", exc)
        return False
    real = os.path.realpath(audio_path)
    inside = False
    for root in roots or []:
        try:
            base = os.path.realpath(root)
            if os.path.commonpath([base, real]) == base:
                inside = True
                break
        except (ValueError, OSError):
            continue
    if not inside:
        return False
    folder = os.path.dirname(real)
    if not os.access(folder, os.W_OK):
        return False
    stem, _ = os.path.splitext(real)
    if os.path.exists(stem + ".lrc") or os.path.exists(stem + ".txt"):
        return False
    try:
        from core.library2 import locks
        if locks.blocks_file(real, "tags", "Lyrics"):
            return False
    except Exception as exc:  # noqa: BLE001 - a lock check never fails playback
        logger.debug("lyrics cache lock check skipped: %s", exc)
    target = stem + (".lrc" if synced else ".txt")
    try:
        # "x": never overwrite a sidecar that appeared since the check above
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(body + "\n")
    except OSError as exc:
        logger.debug("lyrics cache write skipped for %s: %s", target, exc)
        return False
    logger.info("Saved player lyrics beside %s", os.path.basename(real))
    return True
