"""File operation helpers for the import flow."""

from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List

from core.settings import config_manager
from core.imports.ffmpeg_errors import summarize_ffmpeg_error

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


def _apply_publish_mode(path: Path, dest_dir: Path) -> None:
    """Widen ``path``'s permissions to the audience ``dest_dir`` implies.

    An imported file must not simply inherit the staging file's mode. The
    staging file was written by a download client, and slskd/SABnzbd running in
    a container with umask 077 writes its downloads 0600 — carrying that into
    the library publishes a track only the importing user can read, while
    Plex/Jellyfin run as somebody else. That is the whole symptom the
    permission handling exists to fix (PR #1121 review), and copying the source
    mode only fixes it when the downloader already got it right.

    The destination DIRECTORY is the statement of who the library is for, so
    the read/write bits come from it — 0755 → 0644, 0775 → 0664, 0700 → 0600.
    Only ever widening (union with what the file already grants) means an
    import can never narrow a file that was already shareable, and a private
    library stays private because nobody can traverse the directory anyway.
    """
    try:
        current = os.stat(path).st_mode & 0o777
        desired = current | (os.stat(dest_dir).st_mode & 0o666) | 0o600
    except OSError as e:
        logger.debug("could not resolve publish mode for %s: %s", path, e)
        return
    if desired == current:
        return
    try:
        os.chmod(path, desired)
    except OSError as e:
        logger.debug("could not widen permissions on %s: %s", path, e)


def _atomic_cross_device_move(src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst`` across filesystems WITHOUT ever exposing a partial file at
    the final path.

    Copies into a hidden temp sibling of ``dst`` (same filesystem), fsyncs, then does an
    atomic ``os.replace`` into place, then deletes ``src``. A media-server file watcher
    (Jellyfin/Plex real-time monitoring) therefore only ever indexes the COMPLETE file —
    an incremental in-place copy was what Jellyfin could catch mid-write and cache with
    null/incomplete metadata (tracks landing with no disc). Cleans up the temp on failure.
    """
    src, dst = Path(src), Path(dst)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dst.name}.", suffix=".ssync-tmp", dir=dst.parent,
    )
    tmp = Path(tmp_name)
    try:
        with open(src, "rb") as f_src, os.fdopen(fd, "wb") as f_dst:
            fd = -1
            shutil.copyfileobj(f_src, f_dst)
            f_dst.flush()
            os.fsync(f_dst.fileno())
        try:
            shutil.copystat(str(src), str(tmp))   # preserve mtime/permissions (copy2-like)
        except OSError:
            pass
        # AFTER copystat, which re-applies the source's mode and can also fail
        # partway (its utime/xattr steps are the ones that realistically do),
        # leaving mkstemp's 0600 on the file about to be published.
        _apply_publish_mode(tmp, dst.parent)
        os.replace(str(tmp), str(dst))            # atomic within dst's filesystem
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    try:
        src.unlink()
    except OSError:
        logger.info(f"Could not delete source after cross-device move (may be owned by another process): {src}")


def safe_move_file(src, dst):
    """Atomically publish ``src`` at ``dst`` without pre-deleting ``dst``."""
    src = Path(src)
    dst = Path(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        if dst.exists():
            logger.info(f"Source gone but destination exists, file already transferred: {dst.name}")
            return
        raise FileNotFoundError(f"Source file not found and destination does not exist: {src}")

    destination_preexisted = dst.exists()
    try:
        # Same-filesystem move: an atomic rename that also overwrites dst. A media-server
        # watcher (Jellyfin/Plex real-time monitoring) therefore never sees a partial file
        # at the final name. Cross-filesystem raises EXDEV (some network mounts raise
        # EPERM/EACCES) and we copy atomically below instead of letting the move write the
        # destination incrementally — the partial-file-at-final-name is what caused tracks
        # to land in Jellyfin with null/incomplete metadata (no disc).
        # The rename keeps the source's mode, so widen BEFORE it — same inode,
        # and the published file is then never briefly visible at a mode the
        # media server cannot read.
        _apply_publish_mode(src, dst.parent)
        os.replace(str(src), str(dst))
        return
    except FileNotFoundError:
        if not destination_preexisted and not src.exists() and dst.exists():
            logger.info(f"Source moved by another thread, destination exists: {dst.name}")
            return
        raise
    except (OSError, PermissionError) as e:
        # Some filesystems report an error after committing the rename. Only
        # the disappeared source proves that happened; an existing destination
        # may simply be the old library file and must never be treated as proof.
        if not destination_preexisted and not src.exists() and dst.exists():
            logger.warning(f"Move raised {type(e).__name__} after transfer completed: {e}")
            return

        error_msg = str(e).lower()
        cross_device = (
            getattr(e, "errno", None) in (errno.EXDEV, errno.EPERM, errno.EACCES)
            or "cross-device" in error_msg
            or "operation not permitted" in error_msg
            or "permission denied" in error_msg
        )
        if cross_device:
            logger.warning(f"Cross-device move, using atomic copy+rename: {e}")
            try:
                _atomic_cross_device_move(src, dst)
                logger.info(f"Successfully moved file atomically across filesystems: {src} -> {dst}")
                return
            except Exception as fallback_error:
                logger.error(f"Atomic cross-device move failed: {fallback_error}")
                raise
        raise


def protected_root_dirs():
    """Configured root folders that must NEVER be auto-removed as 'empty'.

    The user's staging / download / transfer roots. Deleting one breaks the
    import feature — issue #976: when a staging folder is nested under the
    download folder (common on UnRaid single-share setups), the post-import
    cleanup walked up past it and `rmdir`'d the staging root, because the
    cleanups only protected the *download* root. Every empty-folder cleanup
    consults this so no configured root is ever removed, however it's nested.
    """
    roots = set()
    try:
        from core.imports.paths import config_root_path
        # `import.staging_path` is the key the settings page writes (see
        # webui/static/settings.js) and the key every other reader uses. This
        # read used to name `soulseek.staging_path`, which nothing writes: the
        # lookup returned '', the import folder never made it into the protected
        # set, and #976's whole point — the cleanup must not rmdir the staging
        # root when it sits under the download folder — silently did not apply.
        for key in ('import.staging_path', 'soulseek.download_path',
                    'soulseek.transfer_path'):
            resolved = config_root_path(config_manager.get(key, '') or '')
            if resolved:
                roots.add(resolved)
    except Exception as e:
        logger.debug(f"protected_root_dirs: could not resolve configured roots: {e}")
    return roots


def ensure_staging_dir():
    """Recreate the configured staging/import folder if it went missing.

    Belt-and-suspenders for issue #976: even though the cleanups now protect
    the staging root, if the folder is missing for any reason (an older build
    that deleted it, a manual delete, a transient hiccup) the import feature
    errors until it's back. Recreating it after a cleanup sweep means it
    self-heals within one automation cycle instead of waiting for the next
    Auto-Import scan.

    Only creates it when its PARENT already exists, so we never fabricate a
    not-yet-mounted volume path (which would mask the real mount).
    """
    try:
        from core.imports.paths import config_root_path
        # Same key correction as protected_root_dirs(): reading the key nothing
        # writes made this recreate a literal ./Staging next to the app instead
        # of the import folder the user configured.
        staging = config_root_path(
            config_manager.get('import.staging_path', './Staging') or '')
        if not staging:
            return
        if os.path.isdir(staging):
            return
        parent = os.path.dirname(staging)
        if parent and os.path.isdir(parent):
            os.makedirs(staging, exist_ok=True)
            logger.info(f"Recreated missing staging/import folder: {staging}")
    except Exception as e:
        logger.debug(f"ensure_staging_dir: could not ensure staging folder: {e}")


def cleanup_empty_directories(download_path, moved_file_path):
    """Remove empty directories after a move, ignoring hidden files.

    Never removes a configured root folder (staging/download/transfer), even
    when it is empty and nested under `download_path` (issue #976).
    """
    try:
        protected = protected_root_dirs()
        protected.add(os.path.normpath(download_path))
        current_dir = os.path.dirname(moved_file_path)
        while current_dir != download_path and current_dir.startswith(download_path):
            if os.path.normpath(current_dir) in protected:
                break  # #976: never delete a configured root, even nested + empty
            is_empty = not any(not f.startswith(".") for f in os.listdir(current_dir))
            if is_empty:
                logger.warning(f"Removing empty directory: {current_dir}")
                os.rmdir(current_dir)
                current_dir = os.path.dirname(current_dir)
            else:
                break
    except Exception as e:
        logger.error(f"An error occurred during directory cleanup: {e}")


def estimate_bitrate_kbps(*, size_bytes=None, duration_seconds=None, duration_ms=None, file_path=None):
    """Effective kbps from file size and duration.

    Used when the container has no bitrate header (Ogg Opus) and for
    already-imported library rows stored as 0.
    """
    size = size_bytes
    if not size and file_path:
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = 0
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    length = duration_seconds
    if not length and duration_ms:
        try:
            length = float(duration_ms) / 1000.0
        except (TypeError, ValueError):
            length = 0
    try:
        length = float(length or 0)
    except (TypeError, ValueError):
        length = 0
    if length > 0.05 and size > 0:
        return max(1, int(size * 8 / length / 1000))
    return None


def stream_uses_vbr_display(file_path, info=None) -> bool:
    """True when the library bitrate cell should show an average (``~N kbps``).

    Opus/Vorbis are always VBR. AAC/WMA store mutagen's average (MP4 esds
    ``avgBitrate``), not a CBR constant. MP3 is VBR only when mutagen says so.
    """
    ext = os.path.splitext(file_path or "")[1].lower()
    if ext in {".opus", ".ogg", ".m4a", ".aac", ".wma"}:
        return True
    if ext == ".mp3" and info is not None:
        try:
            from mutagen.mp3 import BitrateMode
            return getattr(info, "bitrate_mode", None) == BitrateMode.VBR
        except Exception:
            return False
    return False


def fill_missing_track_bitrate(track: dict) -> dict:
    """If the DB row has no bitrate, fill an average from size and duration.

    The enhanced library table reads ``tracks.bitrate``. Opus import used
    to store 0 because mutagen.oggopus has no bitrate attribute.
    """
    try:
        existing = int(track.get("bitrate") or 0)
    except (TypeError, ValueError):
        existing = 0
    if existing <= 0:
        kbps = estimate_bitrate_kbps(
            size_bytes=track.get("file_size"),
            duration_ms=track.get("duration"),
            file_path=track.get("file_path"),
        )
        if kbps:
            track["bitrate"] = kbps
    if stream_uses_vbr_display(track.get("file_path")):
        track["bitrate_vbr"] = 1
    return track


def _kbps_from_stream_info(info, file_path: str, *, estimate: bool = True):
    """Bitrate in kbps from mutagen, optionally a size/duration estimate.

    Ogg Opus headers have no bitrate field (mutagen.oggopus only exposes
    length + channels). YouTube remux writes ``.opus``.
    """
    if info is not None:
        raw = getattr(info, "bitrate", None)
        try:
            if raw:
                return max(1, int(raw) // 1000)
        except (TypeError, ValueError):
            pass
    if not estimate:
        return None
    length = None
    if info is not None:
        length = getattr(info, "length", None) or getattr(info, "duration", None)
    return estimate_bitrate_kbps(file_path=file_path, duration_seconds=length)


def _claim_kbps(on_disk_format: str, claimed_format, claimed_bitrate):
    """YouTube (and other streaming) itag/API bitrate, if the file codec matches.

    Mutagen cannot read Opus bitrate; the player-response probe already has it.
    Never copy an Opus/AAC claim onto an MP3 — that is the transcode case.
    """
    disk = (on_disk_format or "").lower()
    claim = (claimed_format or "").lower()
    if not claim or not disk:
        return None
    aliases = {
        "opus": {"opus", "ogg", "webm"},
        "ogg": {"opus", "ogg"},
        "aac": {"aac", "m4a", "mp4"},
        "m4a": {"aac", "m4a", "mp4"},
        "mp3": {"mp3"},
    }
    if disk not in aliases.get(claim, {claim}):
        return None
    try:
        kbps = int(claimed_bitrate)
    except (TypeError, ValueError):
        return None
    return kbps if kbps > 0 else None


def _lossy_kbps(info, file_path, on_disk_format, claimed_format, claimed_bitrate):
    """Prefer mutagen, then the source's probed claim, then a size estimate."""
    kbps = _kbps_from_stream_info(info, file_path, estimate=False)
    if kbps:
        return kbps
    claimed = _claim_kbps(on_disk_format, claimed_format, claimed_bitrate)
    if claimed:
        return claimed
    return _kbps_from_stream_info(info, file_path, estimate=True)


def _chip_lossy(fmt: str, kbps) -> str:
    if kbps:
        return f"{fmt}-{kbps}"
    return fmt


def get_audio_quality_string(file_path, *, claimed_format=None, claimed_bitrate=None):
    """Return a compact audio quality string for the given file.

    ``claimed_*`` is the pre-download probe (YouTube itags, Tidal tier, …).
    Used only when the file itself has no bitrate (Opus) and the codec matches.
    """
    try:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            sr = getattr(audio.info, "sample_rate", 0) or 0
            if sr:
                return f"FLAC {audio.info.bits_per_sample}bit/{sr / 1000:g}kHz"
            return f"FLAC {audio.info.bits_per_sample}bit"

        if ext == ".mp3":
            from mutagen.mp3 import MP3, BitrateMode

            audio = MP3(file_path)
            bitrate_kbps = _lossy_kbps(
                audio.info, file_path, "mp3", claimed_format, claimed_bitrate,
            )
            if getattr(audio.info, "bitrate_mode", None) == BitrateMode.VBR:
                return "MP3-VBR"
            return _chip_lossy("MP3", bitrate_kbps)

        if ext in (".m4a", ".aac", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            return _chip_lossy(
                "M4A",
                _lossy_kbps(audio.info, file_path, "aac", claimed_format, claimed_bitrate),
            )

        if ext == ".ogg":
            try:
                from mutagen.oggvorbis import OggVorbis
                audio = OggVorbis(file_path)
                return _chip_lossy(
                    "OGG",
                    _lossy_kbps(audio.info, file_path, "ogg", claimed_format, claimed_bitrate),
                )
            except Exception:
                from mutagen.oggopus import OggOpus
                audio = OggOpus(file_path)
                return _chip_lossy(
                    "OPUS",
                    _lossy_kbps(audio.info, file_path, "opus", claimed_format, claimed_bitrate),
                )

        if ext == ".opus":
            from mutagen.oggopus import OggOpus

            audio = OggOpus(file_path)
            return _chip_lossy(
                "OPUS",
                _lossy_kbps(audio.info, file_path, "opus", claimed_format, claimed_bitrate),
            )

        if ext in (".webm", ".weba"):
            aq = probe_audio_quality(file_path)
            kbps = (aq.bitrate if aq else None) or _claim_kbps(
                "opus", claimed_format, claimed_bitrate,
            )
            if kbps:
                return _chip_lossy("OPUS", kbps)
            if aq is None:
                return ""
            return _chip_lossy(aq.format.upper(), aq.bitrate)

        return ""
    except Exception as e:
        logger.debug(f"Could not determine audio quality for {file_path}: {e}")
        return ""


def probe_audio_quality(file_path: str):
    """Read the actual file and return an AudioQuality with real measured values.

    Uses mutagen to extract sample_rate, bit_depth, and bitrate from the
    downloaded file — these are ground-truth values, not estimates.
    Returns None when the file cannot be read.
    """
    from core.quality.model import AudioQuality
    try:
        ext = os.path.splitext(file_path)[1].lower().lstrip('.')

        if ext == 'flac':
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            return AudioQuality(
                format='flac',
                bitrate=audio.info.bitrate // 1000 if audio.info.bitrate else None,
                sample_rate=audio.info.sample_rate,
                bit_depth=audio.info.bits_per_sample,
            )

        if ext == 'mp3':
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            return AudioQuality(
                format='mp3',
                bitrate=audio.info.bitrate // 1000,
                sample_rate=audio.info.sample_rate,
            )

        if ext in ('m4a', 'aac', 'mp4'):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            # .m4a can carry AAC (lossy) OR ALAC (lossless) — only the real
            # codec tells them apart, which is why extension-based classification
            # defaults to 'aac' and we correct it here from the probed file.
            codec = (getattr(audio.info, 'codec', '') or '').lower()
            if 'alac' in codec:
                return AudioQuality(
                    format='alac',
                    bitrate=audio.info.bitrate // 1000 if audio.info.bitrate else None,
                    sample_rate=audio.info.sample_rate,
                    bit_depth=getattr(audio.info, 'bits_per_sample', None) or None,
                )
            return AudioQuality(
                format='aac',
                bitrate=audio.info.bitrate // 1000,
                sample_rate=audio.info.sample_rate,
            )

        if ext == 'ogg':
            try:
                from mutagen.oggvorbis import OggVorbis
                audio = OggVorbis(file_path)
                return AudioQuality(
                    format='ogg',
                    bitrate=_kbps_from_stream_info(audio.info, file_path),
                    sample_rate=getattr(audio.info, 'sample_rate', None),
                )
            except Exception:
                from mutagen.oggopus import OggOpus
                audio = OggOpus(file_path)
                return AudioQuality(
                    format='opus',
                    bitrate=_kbps_from_stream_info(audio.info, file_path),
                    sample_rate=getattr(audio.info, 'sample_rate', None) or 48000,
                )

        if ext in ('opus', 'webm', 'weba'):
            # .opus: FFmpegExtractAudio remux of YouTube itag 251/774.
            # mutagen.oggopus has no bitrate/sample_rate attributes.
            # .webm leftover is Matroska, not Ogg — don't fake a probe.
            info = None
            try:
                from mutagen.oggopus import OggOpus
                info = OggOpus(file_path).info
            except Exception:
                if ext != 'opus':
                    return None
            return AudioQuality(
                format='opus',
                bitrate=_kbps_from_stream_info(info, file_path),
                sample_rate=(getattr(info, 'sample_rate', None) or 48000) if info is not None else 48000,
            )

        if ext in ('wav', 'aiff', 'aif'):
            # AIFF must use mutagen.aiff.AIFF — WAVE() can't parse it and would
            # raise, making the file fail open and silently bypass the quality
            # filter. Both are uncompressed PCM, so they share the 'wav' tier.
            if ext == 'wav':
                from mutagen.wave import WAVE
                audio = WAVE(file_path)
            else:
                from mutagen.aiff import AIFF
                audio = AIFF(file_path)
            return AudioQuality(
                format='wav',
                bitrate=audio.info.bitrate // 1000 if audio.info.bitrate else None,
                sample_rate=audio.info.sample_rate,
                bit_depth=getattr(audio.info, 'bits_per_sample', None),
            )

        if ext == 'wma':
            from mutagen.asf import ASF
            audio = ASF(file_path)
            return AudioQuality(
                format='wma',
                bitrate=audio.info.bitrate // 1000 if audio.info.bitrate else None,
                sample_rate=getattr(audio.info, 'sample_rate', None),
            )

        if ext in ('dsf', 'dff'):
            # DSD (DSD Stream File / DSDIFF) — 1-bit hi-res lossless (#939). mutagen
            # reads .dsf (rate/bitrate/bit_depth); .dff has no mutagen reader, so it
            # still classifies as the lossless 'dsf' tier just without measured detail.
            sr = bd = br = None
            if ext == 'dsf':
                try:
                    from mutagen.dsf import DSF
                    info = DSF(file_path).info
                    sr = getattr(info, 'sample_rate', None)
                    bd = getattr(info, 'bits_per_sample', None)
                    br = info.bitrate // 1000 if getattr(info, 'bitrate', None) else None
                except Exception:  # noqa: S110 — unreadable DSF still classifies lossless, just without measured detail
                    pass
            return AudioQuality(format='dsf', bitrate=br, sample_rate=sr, bit_depth=bd)

        return None
    except Exception as e:
        logger.debug("probe_audio_quality failed for %s: %s", file_path, e)
        return None


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


def downsample_hires_flac(final_path, context, enabled=None):
    """Downsample a hi-res FLAC to 16-bit/44.1kHz if enabled.

    ``enabled`` comes from the item's quality profile (see
    `core/imports/pipeline.py::_resolve_context_quality_profile`); ``None``
    falls back to the legacy global setting for callers with no profile."""
    from mutagen.flac import FLAC

    if enabled is None:
        enabled = config_manager.get("lossy_copy.downsample_hires", False)
    if not enabled:
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


def m4a_codec(path):
    """Codec of an .m4a/.mp4 container ('alac', 'aac', …) or None — lets the
    lossless check tell ALAC (lossless) from AAC (lossy), since both are .m4a."""
    try:
        from mutagen.mp4 import MP4
        return (getattr(MP4(path).info, 'codec', '') or '').lower() or None
    except Exception:
        return None


def create_lossy_copy(final_path, settings=None):
    """Convert a lossless file (FLAC / ALAC / WAV / AIFF / DSD) to a lossy copy
    using the configured codec. Non-lossless inputs are skipped (#941).

    ``settings`` is an optional dict from the item's quality profile
    (``enabled``/``codec``/``bitrate``/``delete_original`` — see
    `core/imports/pipeline.py::_resolve_context_quality_profile`); missing/None
    values fall back to the legacy global settings, so callers with no profile
    (e.g. the lossy-converter repair job) keep today's behavior."""
    from core.quality.lossless import (
        is_lossless_audio_path,
        lossy_output_would_overwrite_source,
    )

    settings = settings or {}

    enabled = settings.get("enabled")
    if enabled is None:
        enabled = config_manager.get("lossy_copy.enabled", False)
    if not enabled:
        return None

    # Was FLAC-only; now any lossless source. .m4a is probed (ALAC vs AAC).
    if not is_lossless_audio_path(final_path, probe_codec=m4a_codec):
        return None

    codec = str(settings.get("codec") or config_manager.get("lossy_copy.codec", "mp3")).lower()
    bitrate = str(settings.get("bitrate") or config_manager.get("lossy_copy.bitrate", "320"))

    delete_original = settings.get("delete_original")
    if delete_original is None:
        delete_original = config_manager.get("lossy_copy.delete_original", False)

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

    # Safety invariant: never write the lossy copy over its own source (an .m4a
    # ALAC source + AAC target lands on the same .m4a path). ffmpeg runs with -y,
    # so this guard MUST precede it — the later delete-original guard is too late.
    if lossy_output_would_overwrite_source(final_path, out_path):
        logger.info(
            f"[Lossy Copy] Skipping — {codec.upper()} output would overwrite the "
            f"source: {os.path.basename(final_path)}"
        )
        return None

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

            # Honor the delete-original setting — without this the original
            # FLAC was always kept alongside the converted MP3/OPUS/AAC even
            # when the user explicitly opted into a lossy-only library
            # (Discord-reported by CAL).
            if delete_original:
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

        # The real reason lives at the END of ffmpeg's stderr (the banner is
        # printed first every run), so summarize the tail instead of stderr[:200]
        # which only ever logged the version banner (#995).
        logger.error(
            f"[Lossy Copy] ffmpeg failed for {os.path.basename(final_path)} "
            f"(rc={result.returncode}): {summarize_ffmpeg_error(result.stderr)}"
        )
        logger.debug(f"[Lossy Copy] full ffmpeg stderr:\n{result.stderr}")
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


# Sidecars that belong to ONE audio file (same filename stem) and should travel
# with it. Only synced lyrics for now: .nfo/.txt/.json alongside downloads are
# usually release junk the import correctly leaves behind, but a .lrc IS part
# of the track (SoulSync even generates them) — imports used to move the audio
# and strand the lyrics in the source folder (lilbob5769's report).
_COMPANION_SIDECAR_EXTS = ('.lrc',)


def move_companion_sidecars(src_audio, dst_audio) -> List[str]:
    """Move same-stem companion sidecars along with their track, renamed to the
    destination stem. Called after the audio file itself has moved. Best-effort
    per sidecar — a lyrics problem must never fail the track import. Returns
    the destination paths of everything moved."""
    moved: List[str] = []
    src_stem, _ = os.path.splitext(str(src_audio))
    dst_stem, _ = os.path.splitext(str(dst_audio))
    for ext in _COMPANION_SIDECAR_EXTS:
        for candidate in (src_stem + ext, src_stem + ext.upper()):
            if not os.path.isfile(candidate):
                continue
            try:
                target = dst_stem + ext
                safe_move_file(candidate, target)
                moved.append(target)
                logger.info(f"Moved companion sidecar with track: {os.path.basename(target)}")
            except Exception as e:
                logger.warning(f"Could not move sidecar {candidate}: {e}")
            break
    return moved
