"""Shared low-level helpers for metadata enrichment."""

from __future__ import annotations

import os
import threading
import weakref
from types import SimpleNamespace
from typing import Any

from utils.logging_config import get_logger as _create_logger


logger = _create_logger("metadata.common")

__all__ = [
    "get_logger",
    "get_config_manager",
    "get_mutagen_symbols",
    "get_file_lock",
    "is_ogg_opus",
    "is_vorbis_like",
    "save_audio_file",
    "get_image_dimensions",
    "strip_all_non_audio_tags",
    "verify_metadata_written",
    "wipe_source_tags",
]

_FILE_LOCKS: "weakref.WeakValueDictionary[str, threading.Lock]" = weakref.WeakValueDictionary()
_FILE_LOCKS_LOCK = threading.Lock()


class _NullConfigManager:
    def get(self, _key: str, default: Any = None) -> Any:
        return default


def get_logger():
    return logger


def get_config_manager():
    try:
        from config.settings import config_manager as settings_config_manager

        return settings_config_manager
    except Exception:
        return _NullConfigManager()


def get_mutagen_symbols():
    """Lazy mutagen import so tests can monkeypatch this without the package installed."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.apev2 import APEv2, APENoHeaderError
        from mutagen.flac import FLAC, Picture
        from mutagen.id3 import (
            APIC,
            ID3,
            TBPM,
            TCOP,
            TDOR,
            TDRC,
            TCON,
            TIT2,
            TALB,
            TPE1,
            TPE2,
            TPOS,
            TPUB,
            TRCK,
            TSRC,
            TXXX,
            UFID,
            TMED,
        )
        from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
        from mutagen.oggvorbis import OggVorbis
        try:
            from mutagen.oggopus import OggOpus
        except Exception:
            OggOpus = None
    except Exception as exc:
        logger.debug("Mutagen unavailable for metadata enrichment: %s", exc)
        return None

    return SimpleNamespace(
        File=MutagenFile,
        APEv2=APEv2,
        APENoHeaderError=APENoHeaderError,
        FLAC=FLAC,
        Picture=Picture,
        ID3=ID3,
        APIC=APIC,
        TBPM=TBPM,
        TCOP=TCOP,
        TDOR=TDOR,
        TDRC=TDRC,
        TCON=TCON,
        TIT2=TIT2,
        TALB=TALB,
        TPE1=TPE1,
        TPE2=TPE2,
        TPOS=TPOS,
        TPUB=TPUB,
        TRCK=TRCK,
        TSRC=TSRC,
        TXXX=TXXX,
        UFID=UFID,
        TMED=TMED,
        MP4=MP4,
        MP4Cover=MP4Cover,
        MP4FreeForm=MP4FreeForm,
        OggVorbis=OggVorbis,
        OggOpus=OggOpus,
    )


def get_file_lock(file_path: str) -> threading.Lock:
    # Keep a per-path lock while it is actively referenced, but let it
    # fall out of the cache once nobody is using it anymore.
    with _FILE_LOCKS_LOCK:
        lock = _FILE_LOCKS.get(file_path)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[file_path] = lock
        return lock


def is_ogg_opus(audio_file: Any) -> bool:
    return type(audio_file).__name__ == "OggOpus"


def is_vorbis_like(audio_file: Any, symbols: Any) -> bool:
    vorbis_classes = tuple(
        cls for cls in (
            getattr(symbols, "FLAC", None),
            getattr(symbols, "OggVorbis", None),
        ) if cls is not None
    )
    return bool(vorbis_classes) and isinstance(audio_file, vorbis_classes) or is_ogg_opus(audio_file)


def save_audio_file(audio_file: Any, symbols: Any) -> None:
    if isinstance(audio_file.tags, symbols.ID3):
        audio_file.save(v1=0, v2_version=4)
    elif isinstance(audio_file, symbols.FLAC):
        audio_file.save(deleteid3=True)
    else:
        audio_file.save()


def get_image_dimensions(data: bytes):
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            import struct

            w, h = struct.unpack(">II", data[16:24])
            return w, h
        if data[:2] == b"\xff\xd8":
            import struct

            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return w, h
                length = struct.unpack(">H", data[i + 2 : i + 4])[0]
                i += 2 + length
    except Exception:
        pass
    return None, None


def strip_all_non_audio_tags(file_path: str) -> dict:
    summary = {"apev2_stripped": False, "apev2_tag_count": 0}
    if os.path.splitext(file_path)[1].lower() != ".mp3":
        return summary

    symbols = get_mutagen_symbols()
    if not symbols:
        return summary

    try:
        apev2_tags = symbols.APEv2(file_path)
        tag_count = len(apev2_tags)
        tag_keys = list(apev2_tags.keys())
        apev2_tags.delete(file_path)
        summary["apev2_stripped"] = True
        summary["apev2_tag_count"] = tag_count
        logger.info("Stripped %s APEv2 tags: %s", tag_count, ", ".join(tag_keys[:10]))
    except symbols.APENoHeaderError:
        pass
    except Exception as exc:
        logger.error("Could not strip APEv2 tags (non-fatal): %s", exc)
    return summary


def verify_metadata_written(file_path: str) -> bool:
    symbols = get_mutagen_symbols()
    if not symbols:
        return False

    try:
        check = symbols.File(file_path)
        if check is None or check.tags is None:
            logger.info("[VERIFY] Tags are None after save: %s", file_path)
            return False

        title_found = False
        artist_found = False
        if isinstance(check.tags, symbols.ID3):
            title_found = bool(check.tags.getall("TIT2"))
            artist_found = bool(check.tags.getall("TPE1"))
            try:
                symbols.APEv2(file_path)
                logger.info("[VERIFY] APEv2 tags still present after processing!")
                return False
            except symbols.APENoHeaderError:
                pass
        elif is_vorbis_like(check, symbols):
            title_found = bool(check.get("title"))
            artist_found = bool(check.get("artist"))
        elif isinstance(check, symbols.MP4):
            title_found = bool(check.get("\xa9nam"))
            artist_found = bool(check.get("\xa9ART"))

        if not title_found or not artist_found:
            logger.warning("[VERIFY] Missing metadata - title:%s artist:%s", title_found, artist_found)
            return False

        logger.info("[VERIFY] Metadata verified OK")
        return True
    except Exception as exc:
        logger.error("[VERIFY] Verification error (non-fatal): %s", exc)
        return False


def wipe_source_tags(file_path: str) -> bool:
    try:
        strip_all_non_audio_tags(file_path)
        symbols = get_mutagen_symbols()
        if not symbols:
            return False

        audio = symbols.File(file_path)
        if audio is None:
            return False
        if hasattr(audio, "clear_pictures"):
            audio.clear_pictures()
        if audio.tags is not None:
            tag_count = len(audio.tags)
            audio.tags.clear()
        else:
            audio.add_tags()
            tag_count = 0
        save_audio_file(audio, symbols)
        if tag_count > 0:
            logger.info("[Tag Wipe] Stripped %s source tags from: %s", tag_count, os.path.basename(file_path))
        return True
    except Exception as exc:
        logger.error("[Tag Wipe] Failed (non-fatal): %s", exc)
        return False
