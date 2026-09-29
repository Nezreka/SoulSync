"""Sample Studio — tag rendered chops (Phase 6).

Saved chops carry their own metadata so they are self-describing outside
SoulSync (any DAW or file browser):

* FLAC → Vorbis comments (title/artist/album/comment) + embedded cover art
* WAV  → RIFF LIST INFO chunk (INAM/IART/IPRD/ICMT). mutagen's WAVE class
  only writes ID3 chunks, which most DAWs and Explorer ignore, so the INFO
  chunk is written by hand — it is a fixed, well-defined RIFF structure and
  is verified against ffprobe in tests.

Cover art comes from the source track's own embedded picture when it has
one (FLAC/MP3/MP4), falling back to the album folder's cover.jpg sidecar
that the SoulSync importer writes — never fetched over the network.
Tagging is best-effort: callers should catch and log — a chop without tags
is still a chop.
"""

from __future__ import annotations

import os
import struct
from typing import Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("sample.tags")

CHOP_ALBUM = "Sample Studio"


def build_comment(
    track_title: str,
    artist: str,
    album: str,
    pitch_st: float = 0.0,
    target_bpm: Optional[float] = None,
    source_bpm: Optional[float] = None,
    stem: Optional[str] = None,
) -> str:
    """Human-readable provenance line stored in the file's comment tag."""
    bits = [f"Chopped from '{track_title}' by {artist}"]
    if album:
        bits[-1] += f" ({album})"
    fx: list = []
    if abs(pitch_st) >= 0.01:
        fx.append(f"{pitch_st:+g} st")
    if target_bpm and source_bpm:
        fx.append(f"{source_bpm:g}→{target_bpm:g} BPM")
    if stem:
        fx.append(f"{stem} stem")
    if fx:
        bits.append(" · ".join(fx))
    bits.append("Made with SoulSync Sample Studio")
    return " — ".join(bits)


def extract_cover_art(source_path: str) -> Optional[Tuple[bytes, str]]:
    """(image bytes, mime) for the source track's cover art, or None.

    Embedded art first (FLAC pictures, MP3 APIC frames, MP4 covr atoms via
    mutagen), then the album folder's ``cover.jpg`` sidecar — the file the
    SoulSync importer writes next to the album (``cover.png``/``folder.jpg``
    are checked too, for libraries organized by other tools). Everything is
    local; nothing is fetched over the network. Never raises — returns None
    when the track has no usable picture.
    """
    art = _embedded_art(source_path)
    if art:
        return art
    try:
        directory = os.path.dirname(os.path.abspath(source_path or ""))
        for name in ("cover.jpg", "cover.png", "folder.jpg"):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "rb") as handle:
                        data = handle.read(10 * 1024 * 1024)
                except OSError:
                    continue
                if data:
                    mime = "image/png" if name.endswith(".png") else "image/jpeg"
                    return data, mime
    except Exception as e:
        logger.debug("cover art sidecar lookup failed for %s: %s", source_path, e)
    return None


def _embedded_art(source_path: str) -> Optional[Tuple[bytes, str]]:
    """Embedded picture only (no sidecar fallback)."""
    try:
        from mutagen import File as mutagen_file

        audio = mutagen_file(source_path)
        if audio is None:
            return None
        # FLAC / Ogg Vorbis pictures
        pics = getattr(audio, "pictures", None)
        if pics:
            return bytes(pics[0].data), str(pics[0].mime or "image/jpeg")
        tags = getattr(audio, "tags", None)
        if tags is not None:
            # MP3 APIC
            try:
                apic = tags.getall("APIC") if hasattr(tags, "getall") else []
            except Exception:
                apic = []
            if apic:
                return bytes(apic[0].data), str(apic[0].mime or "image/jpeg")
            # MP4 covr
            try:
                covr = tags.get("covr")
            except Exception:
                covr = None
            if covr:
                raw = bytes(covr[0])
                mime = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                return raw, mime
    except Exception as e:
        logger.debug("cover art extraction failed for %s: %s", source_path, e)
    return None


def _write_wav_info_chunk(path: str, fields: Dict[str, str]) -> None:
    """Append a LIST INFO chunk to a RIFF/WAVE file and fix the RIFF size.

    ``fields`` maps fourcc -> text (e.g. {"INAM": "title"}). Fresh renders
    only — an existing INFO chunk is left alone (readers take the first).
    """
    body = b"INFO"
    for key, value in fields.items():
        if not value:
            continue
        raw = str(value).encode("utf-8") + b"\x00"
        if len(raw) % 2:
            raw += b"\x00"  # chunk data is word-aligned
        body += key.encode("ascii") + struct.pack("<I", len(raw)) + raw
    if body == b"INFO":
        return
    chunk = b"LIST" + struct.pack("<I", len(body)) + body
    with open(path, "r+b") as f:
        head = f.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"not a RIFF/WAVE file: {path}")
        f.seek(0, os.SEEK_END)
        f.write(chunk)
        riff_size = f.tell() - 8
        f.seek(4)
        f.write(struct.pack("<I", riff_size))


def tag_flac(path: str, meta: Dict[str, str], art: Optional[Tuple[bytes, str]] = None) -> None:
    """Vorbis comments + optional cover picture on a FLAC file."""
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    if meta.get("title"):
        audio["title"] = meta["title"]
    if meta.get("artist"):
        audio["artist"] = meta["artist"]
    if meta.get("album"):
        audio["album"] = meta["album"]
    if meta.get("comment"):
        audio["comment"] = meta["comment"]
    if art:
        data, mime = art
        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = data
        # Replace any existing pictures rather than stacking duplicates.
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save()


def tag_wav(path: str, meta: Dict[str, str]) -> None:
    """INFO-chunk text tags on a WAV file. (WAV has no standard picture slot.)"""
    _write_wav_info_chunk(
        path,
        {
            "INAM": meta.get("title", ""),
            "IART": meta.get("artist", ""),
            "IPRD": meta.get("album", ""),
            "ICMT": meta.get("comment", ""),
        },
    )


def tag_chop_file(
    path: str,
    out_format: str,
    meta: Dict[str, str],
    art: Optional[Tuple[bytes, str]] = None,
) -> None:
    """Tag a freshly rendered chop. Raises on failure — callers catch/log."""
    if out_format == "flac":
        tag_flac(path, meta, art)
    else:
        tag_wav(path, meta)
