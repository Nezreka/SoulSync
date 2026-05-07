"""File operation helpers for the import flow."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable, List

from config.settings import config_manager

logger = logging.getLogger("imports.file_ops")


# slskd appends "_<19-digit unix-nanosecond timestamp>" to a downloaded
# filename when the destination already contains a file with the same
# name (concurrent downloads of the same track, partial-file retries
# after a connection drop, cancelled-then-redownloaded files, the same
# track surfacing in multiple synced playlists, etc.). The original
# canonical file usually gets imported and moved into the library while
# the timestamp-suffixed siblings sit orphaned in the downloads folder
# forever. Match the suffix conservatively (≥ 18 digits) so genuine
# user filenames containing trailing numbers don't get hit.
_SLSKD_DEDUP_SUFFIX_RE = re.compile(r"_\d{18,}$")


def _strip_slskd_dedup_suffix(stem: str) -> str:
    """Return the canonical stem with any slskd dedup suffix removed."""
    return _SLSKD_DEDUP_SUFFIX_RE.sub("", stem)


def cleanup_slskd_dedup_siblings(source_path) -> List[str]:
    """Remove orphan ``<basename>_<timestamp>.<ext>`` siblings of a just-
    imported file from the source directory.

    Call this AFTER a successful import (the canonical file has already
    moved away) using the path the canonical file came from. Looks at
    siblings in the same directory whose stem, with the slskd dedup
    suffix stripped, equals the imported file's canonical stem and the
    same extension. Deletes them.

    Returns the list of deleted paths so the caller can log a summary.
    Failures (permissions, racing reader, etc.) are swallowed
    individually so a single locked file doesn't block the rest of the
    cleanup.
    """
    source = Path(source_path)
    parent = source.parent
    if not parent.is_dir():
        return []

    canonical_name = source.name
    canonical_stem, canonical_ext = os.path.splitext(canonical_name)
    # If the imported file ITSELF already had a dedup suffix, the
    # "canonical" name is the stripped form — every other sibling that
    # also strips down to it is redundant.
    canonical_stem = _strip_slskd_dedup_suffix(canonical_stem)

    deleted: List[str] = []
    try:
        children: Iterable[Path] = list(parent.iterdir())
    except OSError as e:
        logger.debug(f"[Dedup Cleanup] could not list {parent}: {e}")
        return []

    for sibling in children:
        if not sibling.is_file():
            continue
        # Skip the imported file itself if it's still on disk (it
        # shouldn't be — caller invokes us after the move — but the
        # check is cheap and keeps the function safe to call from
        # other contexts later).
        if sibling.name == canonical_name:
            continue
        sib_stem, sib_ext = os.path.splitext(sibling.name)
        if sib_ext.lower() != canonical_ext.lower():
            continue
        sib_canonical_stem = _strip_slskd_dedup_suffix(sib_stem)
        if sib_canonical_stem != canonical_stem:
            continue
        # Defensive: don't delete a file that doesn't actually carry
        # the slskd dedup suffix — that would imply it's a legitimate
        # different file the user intentionally placed there.
        if sib_stem == sib_canonical_stem:
            continue
        try:
            sibling.unlink()
            deleted.append(str(sibling))
        except OSError as e:
            logger.debug(f"[Dedup Cleanup] could not remove {sibling}: {e}")

    if deleted:
        logger.info(
            "[Dedup Cleanup] removed %d slskd dedup orphan(s) for %r",
            len(deleted),
            canonical_name,
        )
    return deleted


def safe_move_file(src, dst):
    """Move a file safely across filesystems."""
    src = Path(src)
    dst = Path(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        if dst.exists():
            logger.info(f"Source gone but destination exists, file already transferred: {dst.name}")
            return
        raise FileNotFoundError(f"Source file not found and destination does not exist: {src}")

    if dst.exists():
        for _attempt in range(3):
            try:
                dst.unlink()
                break
            except PermissionError:
                if _attempt < 2:
                    time.sleep(1)
                else:
                    logger.warning(f"Could not remove locked destination after 3 attempts: {dst.name}")
            except Exception:
                break

    try:
        shutil.move(str(src), str(dst))
        return
    except FileNotFoundError:
        if dst.exists():
            logger.info(f"Source moved by another thread, destination exists: {dst.name}")
            return
        raise
    except (OSError, PermissionError) as e:
        error_msg = str(e).lower()

        if dst.exists() and dst.stat().st_size > 0:
            logger.warning(f"Move raised {type(e).__name__} but destination exists, treating as success: {e}")
            try:
                src.unlink()
            except Exception:
                logger.info(f"Could not delete source file (may be owned by another process): {src}")
            return

        if "cross-device" in error_msg or "operation not permitted" in error_msg or "permission denied" in error_msg:
            logger.warning(f"Cross-device move detected, using fallback copy method: {e}")
            try:
                with open(src, "rb") as f_src:
                    with open(dst, "wb") as f_dst:
                        shutil.copyfileobj(f_src, f_dst)
                        f_dst.flush()
                        os.fsync(f_dst.fileno())

                try:
                    src.unlink()
                except PermissionError:
                    logger.info(f"Could not delete source file (may be owned by another process): {src}")
                logger.info(f"Successfully moved file using fallback method: {src} -> {dst}")
                return
            except Exception as fallback_error:
                logger.error(f"Fallback copy also failed: {fallback_error}")
                raise
        raise


def cleanup_empty_directories(download_path, moved_file_path):
    """Remove empty directories after a move, ignoring hidden files."""
    try:
        current_dir = os.path.dirname(moved_file_path)
        while current_dir != download_path and current_dir.startswith(download_path):
            is_empty = not any(not f.startswith(".") for f in os.listdir(current_dir))
            if is_empty:
                logger.warning(f"Removing empty directory: {current_dir}")
                os.rmdir(current_dir)
                current_dir = os.path.dirname(current_dir)
            else:
                break
    except Exception as e:
        logger.error(f"An error occurred during directory cleanup: {e}")


def get_audio_quality_string(file_path):
    """Return a compact audio quality string for the given file."""
    try:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            return f"FLAC {audio.info.bits_per_sample}bit"

        if ext == ".mp3":
            from mutagen.mp3 import MP3, BitrateMode

            audio = MP3(file_path)
            bitrate_kbps = audio.info.bitrate // 1000
            if audio.info.bitrate_mode == BitrateMode.VBR:
                return "MP3-VBR"
            return f"MP3-{bitrate_kbps}"

        if ext in (".m4a", ".aac", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            return f"M4A-{audio.info.bitrate // 1000}"

        if ext == ".ogg":
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(file_path)
            return f"OGG-{audio.info.bitrate // 1000}"

        if ext == ".opus":
            from mutagen.oggopus import OggOpus

            audio = OggOpus(file_path)
            return f"OPUS-{audio.info.bitrate // 1000}"

        return ""
    except Exception as e:
        logger.debug(f"Could not determine audio quality for {file_path}: {e}")
        return ""


def get_quality_tier_from_extension(file_path):
    """Classify a file extension into a quality tier."""
    if not file_path:
        return ("unknown", 999)

    ext = os.path.splitext(file_path)[1].lower()
    quality_tiers = {
        "lossless": {
            "extensions": [".flac", ".ape", ".wav", ".alac", ".dsf", ".dff", ".aiff", ".aif"],
            "tier": 1,
        },
        "high_lossy": {
            "extensions": [".opus", ".ogg"],
            "tier": 2,
        },
        "standard_lossy": {
            "extensions": [".m4a", ".aac"],
            "tier": 3,
        },
        "low_lossy": {
            "extensions": [".mp3", ".wma"],
            "tier": 4,
        },
    }

    for tier_name, tier_data in quality_tiers.items():
        if ext in tier_data["extensions"]:
            return (tier_name, tier_data["tier"])

    return ("unknown", 999)


def downsample_hires_flac(final_path, context):
    """Downsample a hi-res FLAC to 16-bit/44.1kHz if enabled."""
    from mutagen.flac import FLAC

    if not config_manager.get("lossy_copy.downsample_hires", False):
        return None

    if os.path.splitext(final_path)[1].lower() != ".flac":
        return None

    try:
        audio = FLAC(final_path)
        original_bits = audio.info.bits_per_sample
        original_rate = audio.info.sample_rate
    except Exception as e:
        logger.error(f"[Downsample] Could not read FLAC info: {e}")
        return None

    if original_bits <= 16 and original_rate <= 44100:
        return None

    logger.info(f"[Downsample] Converting {original_bits}-bit/{original_rate}Hz -> 16-bit/44100Hz: {os.path.basename(final_path)}")

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        local = os.path.join(os.path.dirname(__file__), "tools", "ffmpeg")
        if os.path.isfile(local):
            ffmpeg_bin = local
        else:
            logger.warning("[Downsample] ffmpeg not found - skipping hi-res conversion")
            return None

    temp_path = final_path + ".tmp.flac"
    try:
        result = subprocess.run(
            [
                ffmpeg_bin, "-i", final_path,
                "-sample_fmt", "s16",
                "-ar", "44100",
                "-map_metadata", "0",
                "-compression_level", "8",
                "-y", temp_path,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            logger.error(f"[Downsample] ffmpeg failed: {result.stderr[:200]}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return None

        if not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
            logger.warning("[Downsample] Output file missing or empty")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return None

        verify_audio = FLAC(temp_path)
        if verify_audio.info.bits_per_sample != 16:
            logger.info(f"[Downsample] Output not 16-bit ({verify_audio.info.bits_per_sample}-bit), aborting")
            os.remove(temp_path)
            return None

        os.replace(temp_path, final_path)
        logger.info(f"[Downsample] Converted to 16-bit/44.1kHz: {os.path.basename(final_path)}")

        new_quality = "FLAC 16bit"
        try:
            updated_audio = FLAC(final_path)
            updated_audio["QUALITY"] = new_quality
            updated_audio.save()
        except Exception as tag_err:
            logger.error(f"[Downsample] Could not update QUALITY tag: {tag_err}")

        old_quality = context.get("_audio_quality", "")
        context["_audio_quality"] = new_quality

        if old_quality and old_quality != new_quality and old_quality in os.path.basename(final_path):
            new_basename = os.path.basename(final_path).replace(old_quality, new_quality)
            new_path = os.path.join(os.path.dirname(final_path), new_basename)
            try:
                os.rename(final_path, new_path)
                logger.info(f"[Downsample] Renamed: {os.path.basename(final_path)} -> {new_basename}")
                for lyrics_ext in (".lrc", ".txt"):
                    old_lyrics = os.path.splitext(final_path)[0] + lyrics_ext
                    if os.path.isfile(old_lyrics):
                        new_lyrics = os.path.splitext(new_path)[0] + lyrics_ext
                        os.rename(old_lyrics, new_lyrics)
                return new_path
            except Exception as rename_err:
                logger.error(f"[Downsample] Could not rename file: {rename_err}")

        return final_path
    except subprocess.TimeoutExpired:
        logger.info(f"[Downsample] Conversion timed out for: {os.path.basename(final_path)}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        logger.error(f"[Downsample] Conversion error: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as _e:
                logger.debug("cleanup downsample temp: %s", _e)
    return None


def create_lossy_copy(final_path):
    """Convert a FLAC file to a lossy copy using the configured codec."""
    from mutagen.flac import FLAC

    if not config_manager.get("lossy_copy.enabled", False):
        return None

    if os.path.splitext(final_path)[1].lower() != ".flac":
        return None

    codec = config_manager.get("lossy_copy.codec", "mp3").lower()
    bitrate = config_manager.get("lossy_copy.bitrate", "320")

    if codec == "opus" and int(bitrate) > 256:
        bitrate = "256"

    codec_map = {
        "mp3": ("libmp3lame", ".mp3", f"MP3-{bitrate}", ["-vn", "-id3v2_version", "3"]),
        "opus": ("libopus", ".opus", f"OPUS-{bitrate}", ["-vn", "-map", "0:a", "-vbr", "on"]),
        "aac": ("aac", ".m4a", f"AAC-{bitrate}", ["-vn", "-movflags", "+faststart"]),
    }

    if codec not in codec_map:
        logger.info(f"[Lossy Copy] Unknown codec '{codec}' - skipping conversion")
        return None

    ffmpeg_codec, out_ext, quality_label, extra_args = codec_map[codec]
    out_path = os.path.splitext(final_path)[0] + out_ext

    original_quality = get_audio_quality_string(final_path)
    if original_quality:
        out_basename = os.path.basename(out_path)
        if original_quality in out_basename:
            out_basename = out_basename.replace(original_quality, quality_label)
            out_path = os.path.join(os.path.dirname(out_path), out_basename)

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        local = os.path.join(os.path.dirname(__file__), "tools", "ffmpeg")
        if os.path.isfile(local):
            ffmpeg_bin = local
        else:
            logger.warning(f"[Lossy Copy] ffmpeg not found - skipping {codec.upper()} conversion")
            return None

    try:
        logger.info(f"[Lossy Copy] Converting to {quality_label}: {os.path.basename(final_path)}")
        cmd = [
            ffmpeg_bin, "-i", final_path,
            "-codec:a", ffmpeg_codec,
            "-b:a", f"{bitrate}k",
            "-map_metadata", "0",
        ] + extra_args + ["-y", out_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode == 0:
            logger.info(f"[Lossy Copy] Created {quality_label} copy: {os.path.basename(out_path)}")
            try:
                from mutagen import File as MutagenFile
                audio = MutagenFile(out_path)
                if audio is not None:
                    if codec == "mp3":
                        from mutagen.id3 import TXXX
                        audio.tags.add(TXXX(encoding=3, desc="QUALITY", text=[quality_label]))
                    elif codec == "opus":
                        audio["QUALITY"] = [quality_label]
                    elif codec == "aac":
                        from mutagen.mp4 import MP4FreeForm
                        audio["----:com.apple.iTunes:QUALITY"] = [MP4FreeForm(quality_label.encode("utf-8"))]
                    audio.save()
            except Exception as tag_err:
                logger.error(f"[Lossy Copy] Could not update QUALITY tag: {tag_err}")

            # Honor the lossy_copy.delete_original setting — without this
            # the original FLAC was always kept alongside the converted
            # MP3/OPUS/AAC even when the user explicitly opted into a
            # lossy-only library (Discord-reported by CAL).
            if config_manager.get("lossy_copy.delete_original", False):
                if os.path.normpath(out_path) != os.path.normpath(final_path):
                    try:
                        os.remove(final_path)
                        logger.info(
                            f"[Lossy Copy] Deleted original lossless source after conversion: "
                            f"{os.path.basename(final_path)}"
                        )
                    except FileNotFoundError:
                        # Already gone — concurrent cleanup or another worker
                        # handled it. Not an error.
                        pass
                    except Exception as del_err:
                        logger.error(
                            f"[Lossy Copy] Could not delete original after conversion "
                            f"({os.path.basename(final_path)}): {del_err}"
                        )
            return out_path

        logger.error(f"[Lossy Copy] ffmpeg failed: {result.stderr[:200]}")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception as _e:
                logger.debug("cleanup lossy copy artifact: %s", _e)
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"[Lossy Copy] Conversion timed out for: {os.path.basename(final_path)}")
    except Exception as e:
        logger.error(f"[Lossy Copy] Conversion error: {e}")
    return None
