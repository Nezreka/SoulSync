"""Shared low-level helpers for metadata enrichment."""

from __future__ import annotations

import os
import shutil
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


def _raw_audio_save(audio_file: Any, symbols: Any, target: Any = None) -> None:
    """The plain mutagen save with the format-specific kwargs. ``target`` None →
    save in place (the exact call used before #819, byte-for-byte unchanged); a
    path → save into that file (the atomic temp copy)."""
    if isinstance(audio_file.tags, symbols.ID3):
        audio_file.save(v1=0, v2_version=4) if target is None else audio_file.save(target, v1=0, v2_version=4)
    elif isinstance(audio_file, symbols.FLAC):
        audio_file.save(deleteid3=True) if target is None else audio_file.save(target, deleteid3=True)
    else:
        audio_file.save() if target is None else audio_file.save(target)


class _AudioIntegrityError(Exception):
    """Raised internally when a written temp file's AUDIO differs from the
    original — i.e. the tag write damaged the stream. Never propagates out of
    save_audio_file; it triggers an abort (original left untouched)."""


def _flac_audio_offset(path: str):
    """Byte offset where a FLAC's audio frames start (just past the last metadata
    block). None if the file isn't a plain 'fLaC' stream (e.g. ID3-prefixed) so
    the caller falls back to a structural check instead of a byte compare."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"fLaC":
                return None
            while True:
                hdr = f.read(4)
                if len(hdr) < 4:
                    return None
                is_last = hdr[0] & 0x80
                length = int.from_bytes(hdr[1:4], "big")
                f.seek(length, 1)
                if is_last:
                    return f.tell()
    except OSError:
        return None


def _flac_audio_identical(orig_path: str, new_path: str):
    """Compare the FLAC audio frames of two files (each from its own audio
    offset, so a metadata-size change doesn't matter). Editing tags never
    touches frame bytes, so True == provably intact, False == the write mangled
    the audio, None == couldn't locate frames (fall back to a structural check)."""
    o_off = _flac_audio_offset(orig_path)
    n_off = _flac_audio_offset(new_path)
    if o_off is None or n_off is None:
        return None
    try:
        with open(orig_path, "rb") as fo, open(new_path, "rb") as fn:
            fo.seek(o_off)
            fn.seek(n_off)
            while True:
                bo = fo.read(1 << 20)
                bn = fn.read(1 << 20)
                if bo != bn:
                    return False
                if not bo:
                    return True
    except OSError:
        return None


def _audio_stream_signature(info: Any):
    """Structural identity of the AUDIO stream (not tags) — the fields that must
    NOT change when only tags are edited. Used for non-FLAC formats."""
    if info is None:
        return None
    sig = {}
    for attr in ("sample_rate", "channels", "bits_per_sample", "total_samples"):
        v = getattr(info, attr, None)
        if v is not None:
            sig[attr] = v
    return sig or None


def _signatures_match(before, after) -> bool:
    """True when the structural signatures agree (or either is unknown — we don't
    block on what we can't compare)."""
    if not before or not after:
        return True
    for k, bv in before.items():
        av = after.get(k)
        if av is not None and av != bv:
            return False
    return True


def _audio_intact(before: Any, after: Any) -> bool:
    """Non-FLAC integrity: the structural signature must match, and if the
    original reported a real duration the new one must be within tolerance —
    which catches the #819 'empty shell' truncation (real length → 0). An
    unknown original duration can't be judged, so we allow it (the file already
    parsed as audio)."""
    if after is None:
        return False
    if not _signatures_match(_audio_stream_signature(before), _audio_stream_signature(after)):
        return False
    bl = getattr(before, "length", None) if before is not None else None
    al = getattr(after, "length", None)
    if bl and bl > 0:
        if not al or abs(float(bl) - float(al)) > max(0.25, float(bl) * 0.02):
            return False
    return True


def save_audio_file(audio_file: Any, symbols: Any) -> bool:
    """Persist mutagen tag changes ATOMICALLY and only if the audio survives it
    (#819 + #1000). Returns True if the file was written, False if the write was
    aborted because it would have damaged the audio (original left untouched).

    mutagen's in-place ``save()`` rewrites the file; if it's interrupted, or the
    filesystem/mutagen mangles the stream while inserting metadata, the file is
    left truncated or with skipping/mute audio. Instead: copy the original to a
    temp in the same directory, write the new tags into that copy, VERIFY the
    audio is byte-for-byte intact (FLAC frames compared directly; other formats
    checked structurally), then ``os.replace`` it in atomically. The original is
    never touched until that final swap.

    Two failure modes, handled differently:
      * The atomic path can't *run* (no filename, copy fails, a format mutagen
        can't save-to-path) → fall back to the plain in-place save, exactly as
        before, so nothing regresses.
      * The atomic path ran but the temp's AUDIO differs from the original → the
        write itself is corrupting. Do NOT retry in place (same writer + same
        filesystem would corrupt the real file too). Abort: drop the temp, leave
        the original exactly as it was, return False.
    """
    path = getattr(audio_file, "filename", None)
    try:
        path = os.fspath(path) if path else None
    except TypeError:
        path = None
    if not path or not os.path.isfile(path):
        _raw_audio_save(audio_file, symbols)
        return True

    tmp = f"{path}.sstmp"
    try:
        shutil.copy2(path, tmp)               # snapshot original (audio + tags)
        _raw_audio_save(audio_file, symbols, target=tmp)  # write new tags into the copy

        check = symbols.File(tmp)             # 1) still a parseable audio file?
        if check is None:
            raise _AudioIntegrityError("temp is not a parseable audio file")

        # 2) audio stream intact? FLAC → exact frame byte-compare (definitive,
        #    and robust to legit unknown-length STREAMINFO); other formats →
        #    structural signature + duration vs the ORIGINAL.
        flac_cls = getattr(symbols, "FLAC", None)
        if flac_cls is not None and isinstance(audio_file, flac_cls):
            identical = _flac_audio_identical(path, tmp)
            if identical is False:
                raise _AudioIntegrityError("FLAC audio frames changed by the tag write")
            if identical is None and not _audio_intact(
                    getattr(audio_file, "info", None), getattr(check, "info", None)):
                raise _AudioIntegrityError("audio stream changed")
        elif not _audio_intact(getattr(audio_file, "info", None), getattr(check, "info", None)):
            raise _AudioIntegrityError("audio stream changed")

        os.replace(tmp, path)                 # atomic swap — original safe until here
        return True
    except _AudioIntegrityError as bad:
        # The write produced a corrupt file. Abort — original untouched, no
        # in-place retry (it would corrupt the real file the same way).
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        logger.error("[Atomic Save] integrity check FAILED for %s (%s) — original left "
                     "untouched, tags NOT written", os.path.basename(path), bad)
        return False
    except Exception as atomic_err:
        # Atomic path couldn't run (copy/format/replace error). Fall back to the
        # plain in-place save so any edge the atomic path can't handle behaves
        # exactly as before #819.
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        logger.warning("[Atomic Save] atomic path failed (%s) — in-place fallback for %s",
                       atomic_err, os.path.basename(path))
        _raw_audio_save(audio_file, symbols)
        return True


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
    except Exception as e:
        logger.debug("parse JPEG dimensions failed: %s", e)
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
