"""Repair exact local playlist references after proven successful audio moves."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping

from utils.logging_config import get_logger

logger = get_logger("playlists.path_repair")


def _path_key(path):
    # The old audio no longer exists. Do not follow symlinks or use basename
    # matching to guess which independent copy an entry designated.
    return os.path.normcase(os.path.abspath(path))


def repair_local_m3us(moves: Mapping[str, str]) -> int:
    """Rewrite exact entries in M3Us adjacent to moved source audio.

    Leave comments, remote URLs, unknown encodings, symlinked playlists and
    unrelated entries byte-for-byte intact. Never search the library tree.
    """
    normalized = {_path_key(old): os.path.abspath(new) for old, new in moves.items()}
    repaired = 0
    for folder in {os.path.dirname(os.path.abspath(old)) for old in moves}:
        try:
            candidates = list(Path(folder).iterdir())
        except OSError:
            continue
        for playlist in candidates:
            if (playlist.suffix.lower() not in ('.m3u', '.m3u8')
                    or playlist.is_symlink() or not playlist.is_file()):
                continue
            temporary = None
            try:
                original = playlist.read_bytes()
                text = original.decode('utf-8')
                if '\x00' in text:
                    continue
                rewritten = []
                changed = False
                for line in text.splitlines(keepends=True):
                    entry = line.rstrip('\r\n')
                    newline = line[len(entry):]
                    # A BOM may precede a comment/header. Preserve it, and
                    # only interpret plain local path entries.
                    if (not entry or entry.lstrip('\ufeff').startswith('#')
                            or '://' in entry or entry != entry.strip()):
                        rewritten.append(line)
                        continue
                    old = entry if os.path.isabs(entry) else os.path.join(folder, entry)
                    new = normalized.get(_path_key(old))
                    if new is None:
                        rewritten.append(line)
                        continue
                    replacement = new if os.path.isabs(entry) else os.path.relpath(new, folder)
                    rewritten.append(replacement + newline)
                    changed = True
                if not changed:
                    continue
                updated = ''.join(rewritten).encode('utf-8')
                fd, temporary = tempfile.mkstemp(prefix='.soulsync-m3u-', dir=folder)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                shutil.copymode(playlist, temporary)
                # Preserve a playlist edited while this repair was prepared.
                if playlist.is_symlink() or playlist.read_bytes() != original:
                    continue
                os.replace(temporary, playlist)
                temporary = None
                repaired += 1
            except (OSError, UnicodeError, ValueError) as exc:
                logger.debug("M3U left unchanged at %s: %s", playlist, exc)
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
    return repaired


def repair_playlist_references(moves: Mapping[str, str], *, config_manager=None) -> int:
    """Repair local M3Us and unchanged manifest-owned materialized links."""
    repaired = repair_local_m3us(moves)
    if config_manager is not None:
        from core.imports.paths import config_root_path
        from core.playlists.materialize import update_materialized_paths

        root = config_root_path(config_manager.get('playlists.materialize_path', './Playlists'),
                                './Playlists')
        repaired += update_materialized_paths(root, moves)
    return repaired
