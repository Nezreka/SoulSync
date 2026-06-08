"""Apply album art to existing library files.

Two jobs, both reusing the post-processing standard so the user's
``album_art_order`` preference is honored and embedded art matches cover.jpg:

- Detect whether an album already has art ON DISK (embedded in the audio file
  or a cover.jpg/folder.jpg sidecar) — the Cover Art Filler previously only
  looked at the DB ``thumb_url``, so albums whose files were artless but whose
  DB row had a URL were never flagged.
- Embed found art into the album's audio files (``embed_album_art_metadata``)
  and write a cover.jpg (``download_cover_art``). Only ADDS art — it does not
  clear or rewrite the user's existing tags.
"""

from __future__ import annotations

import contextlib
import errno
import os
from typing import Iterable, Optional

from core.metadata.artwork import download_cover_art, embed_album_art_metadata
from core.metadata.common import get_mutagen_symbols
from utils.logging_config import get_logger

logger = get_logger("metadata.art_apply")

# Folder-level cover files recognised across players (matches soulsync_client).
_COVER_SIDECARS = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
)


def folder_has_cover_sidecar(folder: str) -> bool:
    """True if the album folder already carries a cover.jpg/folder.jpg sidecar."""
    if not folder:
        return False
    try:
        for name in _COVER_SIDECARS:
            if os.path.isfile(os.path.join(folder, name)):
                return True
    except OSError:
        return False
    return False


def file_has_embedded_art(file_path: str) -> bool:
    """True if the audio file already has embedded cover art (FLAC picture,
    ID3 APIC, MP4 covr, or a Vorbis metadata_block_picture)."""
    if not file_path or not os.path.isfile(file_path):
        return False
    symbols = get_mutagen_symbols()
    if not symbols:
        return False
    try:
        return _audio_has_art(symbols.File(file_path), symbols)
    except Exception as exc:
        logger.debug("art presence check failed for %s: %s", file_path, exc)
        return False


def _audio_has_art(audio, symbols) -> bool:
    """True if an already-open mutagen object carries embedded cover art."""
    if audio is None:
        return False
    # FLAC / Ogg expose picture blocks directly.
    if getattr(audio, "pictures", None):
        return True
    if isinstance(audio, symbols.MP4):
        return bool(audio.get("covr"))
    tags = getattr(audio, "tags", None)
    if tags is None:
        return False
    with contextlib.suppress(Exception):
        if isinstance(tags, symbols.ID3):
            return bool(tags.getall("APIC"))
    with contextlib.suppress(Exception):
        if "metadata_block_picture" in tags:
            return True
    return False


def extract_embedded_art(file_path: str) -> Optional[bytes]:
    """Return the first embedded cover-art image bytes from an audio file, or
    None. Used to write a cover.jpg sidecar from the album's OWN art — no API
    call, and the sidecar matches what's embedded (#813/Sokhi)."""
    if not file_path or not os.path.isfile(file_path):
        return None
    symbols = get_mutagen_symbols()
    if not symbols:
        return None
    try:
        audio = symbols.File(file_path)
        if audio is None:
            return None
        pics = getattr(audio, "pictures", None)   # FLAC / Ogg
        if pics:
            return bytes(pics[0].data)
        if isinstance(audio, symbols.MP4):
            covr = audio.get("covr")
            if covr:
                return bytes(covr[0])
        tags = getattr(audio, "tags", None)
        if tags is not None:
            with contextlib.suppress(Exception):
                if isinstance(tags, symbols.ID3):
                    apics = tags.getall("APIC")
                    if apics:
                        return bytes(apics[0].data)
    except Exception as exc:
        logger.debug("embedded-art extract failed for %s: %s", file_path, exc)
    return None


def album_has_art_on_disk(rep_file_path: str) -> bool:
    """Does this album have art on disk?

    Checks the folder for a cover sidecar first (cheap stat) and only opens the
    representative audio file when there's no sidecar. Returns True when there's
    no local file to inspect (e.g. a media-server-only album) so such albums
    aren't wrongly flagged as missing file art.
    """
    if not rep_file_path:
        return True
    folder = os.path.dirname(rep_file_path)
    if folder_has_cover_sidecar(folder):
        return True
    return file_has_embedded_art(rep_file_path)


def apply_art_to_album_files(
    file_paths: Iterable[str],
    metadata: dict,
    album_info: dict,
    folder: str = None,
    context: dict = None,
) -> dict:
    """Embed art into each audio file + write cover.jpg, reusing the standard.

    ``metadata`` feeds ``embed_album_art_metadata`` (needs album_artist/artist/
    album, optionally musicbrainz_release_id and album_art_url as the fallback
    URL). ``album_info`` feeds ``download_cover_art`` (album_name/album_image_url/
    musicbrainz_release_id). Existing tags are preserved — only art is added.

    Returns counts; never raises (unwritable/read-only files are skipped).
    ``read_only_fs`` is True when the target filesystem itself rejects writes
    (a real EROFS from an actual write — a ':ro' volume, a read-only host/NFS/
    SMB mount, or a read-only underlying fs) so callers can tell the user the
    real cause instead of a generic failure.

    NOTE: read-only is detected from an ACTUAL write raising EROFS, never from
    statvfs/mount flags — union/FUSE/network filesystems (mergerfs, rclone,
    NFS) common in self-hosted setups misreport those flags, which would
    false-block a perfectly writable library (Sokhi: read-only error with no
    ':ro' in compose). The write itself is the only honest test.
    """
    result = {"embedded": 0, "failed": 0, "skipped": 0, "cover_written": False,
              "read_only_fs": False}
    symbols = get_mutagen_symbols()
    paths = [p for p in (file_paths or []) if p]
    if not symbols:
        return result

    for fp in paths:
        if not os.path.isfile(fp):
            result["skipped"] += 1
            continue
        try:
            audio = symbols.File(fp)
            if audio is None:
                result["skipped"] += 1
                continue
            # Purely additive: never touch a file that already has art. Embedding
            # again would APPEND a duplicate picture on FLAC (add_picture doesn't
            # replace), so leave already-arted files alone.
            if _audio_has_art(audio, symbols):
                result["skipped"] += 1
                continue
            # ID3 needs a tag container before APIC can be added.
            if getattr(audio, "tags", None) is None and hasattr(audio, "add_tags"):
                with contextlib.suppress(Exception):
                    audio.add_tags()
            if embed_album_art_metadata(audio, metadata):
                audio.save()
                result["embedded"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:
            # Read-only mounts / permission errors land here — skip, don't crash.
            # A real EROFS = the mount is read-only; flag it and stop trying the
            # rest (fast-fail without the unreliable statvfs guess).
            if getattr(exc, "errno", None) == errno.EROFS:
                result["read_only_fs"] = True
                logger.warning("Could not embed art into %s: read-only filesystem", fp)
                result["failed"] += len(paths) - paths.index(fp)  # remaining all fail too
                break
            logger.warning("Could not embed art into %s: %s", fp, exc)
            result["failed"] += 1

    target_dir = folder or (os.path.dirname(paths[0]) if paths else None)
    if target_dir and os.path.isdir(target_dir) and not folder_has_cover_sidecar(target_dir):
        # Prefer the album's OWN embedded art for the cover.jpg sidecar: it's
        # always present once the files are arted (we may have just embedded it),
        # needs no API call, and the sidecar matches the files exactly
        # (#813/Sokhi: files have art, just no cover.jpg). Fall back to a fresh
        # download only when there's nothing embedded to extract.
        cover_path = os.path.join(target_dir, "cover.jpg")
        art_bytes = None
        for fp in paths:
            art_bytes = extract_embedded_art(fp)
            if art_bytes:
                break
        if art_bytes:
            try:
                with open(cover_path, "wb") as handle:
                    handle.write(art_bytes)
                result["cover_written"] = True
            except OSError as exc:
                if getattr(exc, "errno", None) == errno.EROFS:
                    result["read_only_fs"] = True
                logger.warning("cover.jpg sidecar write failed for %s: %s", target_dir, exc)

        if not result["cover_written"] and not result["read_only_fs"]:
            # No embedded art to extract → fetch it. download_cover_art swallows
            # its own write errors, so it records read-only on the context dict
            # (EROFS detection gap, Sokhi). force=True bypasses the import-time
            # "Download cover.jpg" toggle — running the filler is an explicit ask.
            cover_ctx = context if isinstance(context, dict) else {}
            try:
                download_cover_art(album_info, target_dir, cover_ctx, force=True)
                result["cover_written"] = folder_has_cover_sidecar(target_dir)
            except Exception as exc:
                if getattr(exc, "errno", None) == errno.EROFS:
                    result["read_only_fs"] = True
                logger.warning("cover.jpg write failed for %s: %s", target_dir, exc)
            if cover_ctx.get("_cover_read_only"):
                result["read_only_fs"] = True
    return result
