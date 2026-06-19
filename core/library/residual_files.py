"""What counts as a *residual* file — a leftover with no value once the audio it
accompanied is gone: OS junk, cover/scan images, and lyric/metadata sidecars.

Single source of truth shared by:
  * the **Reorganize** cleanup, which strips these from a source dir after every
    track has moved out (so the empty-dir pruner can take the folder), and
  * the **Empty Folder Cleaner** job, which can optionally treat a folder holding
    ONLY residual files as removable (#891).

Defining "disposable" in one place keeps the two features agreeing on what a "dead
folder" is. Pure predicates — no filesystem access — so they're unit-tested in
isolation. The whitelist is deliberately conservative: anything NOT recognized here
(a booklet ``.pdf``, a video, a ``.txt`` note) is treated as real content and kept.
"""

from __future__ import annotations

import os

# OS / tooling junk.
JUNK_FILES = {'.ds_store', 'thumbs.db', 'desktop.ini', '.directory', 'album.nfo~'}
# Cover art + booklet scans.
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
# Lyric / metadata / playlist sidecars that are worthless without their audio.
SIDECAR_EXTS = {'.lrc', '.nfo', '.cue', '.m3u', '.m3u8'}


def _ext(name: str) -> str:
    return os.path.splitext(name or '')[1].lower()


def is_junk(name: str) -> bool:
    return (name or '').lower() in JUNK_FILES


def is_image(name: str) -> bool:
    return _ext(name) in IMAGE_EXTS


def is_sidecar(name: str) -> bool:
    return _ext(name) in SIDECAR_EXTS


def is_disposable(name: str) -> bool:
    """True if this file is junk, a cover/scan image, or a lyric/metadata sidecar —
    i.e. safe to delete from a folder that has no audio left."""
    return is_junk(name) or is_image(name) or is_sidecar(name)


__all__ = [
    'JUNK_FILES', 'IMAGE_EXTS', 'SIDECAR_EXTS',
    'is_junk', 'is_image', 'is_sidecar', 'is_disposable',
]
