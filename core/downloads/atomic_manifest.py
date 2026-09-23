"""On-disk manifest for an atomic album batch (#1289).

Atomic album publishing (#999) keeps a batch's finished tracks in a private
staging tree until the whole album is ready. Everything that knows what that
tree IS -- which batch owns it, which library it belongs to, which wishlist
requests its files satisfy -- lived in ``download_batches``, a plain dict in the
web process. When the process went away mid-batch, so did all of it, and the
staging tree became an anonymous pile of audio that no scanner looks at
(``.soulsync_atomic_staging`` is deliberately skipped by SoulSync's own scan,
by the repair jobs, and by every media server, because it is dot-prefixed).

This module writes the missing half to disk, next to the files it describes, so
a later process can pick the tree up and finish the job. It is deliberately
dumb: JSON, one file per batch, atomically replaced, best-effort everywhere. A
manifest that fails to write must never fail a download -- the worst it costs is
a slower recovery.

Pure mechanics, like ``atomic_album_publish``: no config reads, no globals, no
database.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from core.downloads.atomic_album_publish import MANIFEST_NAME, _STAGING_DIRNAME
from utils.logging_config import get_logger

logger = get_logger("downloads.atomic_manifest")

MANIFEST_VERSION = 1

# One lock for every manifest. Tracks in a batch finish post-processing on
# separate worker threads and several batches run at once, so the read-modify-
# write below would interleave. Manifests are tiny and written once per finished
# track, so a single global lock costs nothing measurable and removes a whole
# class of "the manifest is missing half its tracks" bug.
_lock = threading.Lock()


def manifest_path(staging_root: str) -> str:
    return os.path.join(os.path.normpath(str(staging_root)), MANIFEST_NAME)


def read_manifest(staging_root: str) -> Optional[Dict[str, Any]]:
    """The batch manifest, or None when there isn't a readable one.

    A corrupt manifest reads as absent on purpose. Recovery works without one --
    the staged layout alone is enough to publish -- so a half-written file must
    degrade to "no manifest", never to an exception that aborts the sweep and
    strands every other batch behind it.
    """
    path = manifest_path(staging_root)
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        if os.path.exists(path):
            logger.warning("[Atomic Manifest] Unreadable manifest at %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault('tracks', {})
    return data


def _write_manifest(staging_root: str, data: Dict[str, Any]) -> bool:
    """Replace the manifest atomically. Caller holds ``_lock``."""
    root = os.path.normpath(str(staging_root))
    path = manifest_path(root)
    try:
        os.makedirs(root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=MANIFEST_NAME, suffix='.tmp', dir=root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("[Atomic Manifest] Could not write %s: %s", path, exc)
        return False


def begin_batch(staging_root: str, *, batch_id: str, transfer_dir: str,
                profile_id: Any = None, album_name: str = '') -> bool:
    """Open (or refresh the header of) a batch manifest.

    Called when the pipeline decides a batch stages, so a tree that dies before
    any track finishes still says which library it maps back into.
    """
    with _lock:
        data = read_manifest(staging_root) or {}
        data.update({
            'version': MANIFEST_VERSION,
            'batch_id': str(batch_id),
            'transfer_dir': str(transfer_dir),
            'profile_id': profile_id,
            'album_name': album_name or data.get('album_name', ''),
            'created_at': data.get('created_at') or time.time(),
            'updated_at': time.time(),
        })
        data.setdefault('tracks', {})
        return _write_manifest(staging_root, data)


def record_staged_track(staging_root: str, *, staged_path: str, final_path: str,
                        source: str = '', source_ids: Optional[Dict[str, Any]] = None,
                        track_name: str = '', artist_name: str = '',
                        profile_id: Any = None) -> bool:
    """Record one finished track and the wishlist request it will satisfy.

    ``source_ids`` is the payload that matters: it is what a later process needs
    to remove the right wishlist rows after it publishes the tree, and what it
    needs to put back if it cannot. Keyed by staged path because that is the key
    the publish result hands back.
    """
    with _lock:
        data = read_manifest(staging_root) or {'tracks': {}}
        data.setdefault('version', MANIFEST_VERSION)
        data.setdefault('created_at', time.time())
        data['updated_at'] = time.time()
        tracks = data.setdefault('tracks', {})
        tracks[os.path.normpath(str(staged_path))] = {
            'final_path': os.path.normpath(str(final_path)) if final_path else '',
            'source': source or '',
            'source_ids': source_ids or {},
            'track_name': track_name or '',
            'artist_name': artist_name or '',
            'profile_id': profile_id,
            'staged_at': time.time(),
        }
        return _write_manifest(staging_root, data)


def manifest_tracks(manifest: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    tracks = (manifest or {}).get('tracks')
    return tracks if isinstance(tracks, dict) else {}


def remove_manifest(staging_root: str) -> None:
    try:
        os.remove(manifest_path(staging_root))
    except OSError:
        pass


def staging_parent(transfer_dir: str) -> str:
    """``<transfer>/.soulsync_atomic_staging`` -- the folder holding every
    batch's staging root."""
    return os.path.join(os.path.normpath(str(transfer_dir)), _STAGING_DIRNAME)


def orphan_staging_roots(transfer_dir: str, live_batch_ids=()) -> List[str]:
    """Every staging root under ``transfer_dir`` not owned by a live batch.

    At startup ``download_batches`` is empty, so everything on disk qualifies --
    the same reasoning the album-bundle sweep already relies on. Passing live ids
    keeps the function honest if it is ever called from a running process.
    """
    parent = staging_parent(transfer_dir)
    live = {str(b) for b in (live_batch_ids or ())}
    out: List[str] = []
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return out
    for name in names:
        if name in live:
            continue
        # Batch ids are never dot-prefixed, so a dot-directory here was made by
        # hand or by another tool. Treating one as a batch would publish its
        # contents into the user's library.
        if name.startswith('.'):
            continue
        root = os.path.join(parent, name)
        if os.path.isdir(root):
            out.append(root)
    return out


__all__ = [
    "MANIFEST_VERSION",
    "begin_batch",
    "manifest_path",
    "manifest_tracks",
    "orphan_staging_roots",
    "read_manifest",
    "record_staged_track",
    "remove_manifest",
    "staging_parent",
]
