"""Per-item overlay asset store.

Overlays are burned into the poster the server displays, so to re-render or remove
them non-destructively we keep our own layers on disk: the clean BASE we composite
onto, and a one-time first-touch BACKUP of whatever art was there before we ever
overlaid. Lives beside the video DB on the persisted data volume — no new mount:

    <data>/video_poster_assets/<kind>/<id>/base.jpg      # clean source layer
    <data>/video_poster_assets/<kind>/<id>/backup.jpg    # first-touch original

Keyed by DB id (not disk path) so it survives library folder renames/moves and
works for server-only items with no local folder.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("video.overlays.assets")


def default_root() -> Path:
    """The assets directory beside the video DB (same persisted volume)."""
    db = os.environ.get("VIDEO_DATABASE_PATH", "database/video_library.db")
    return Path(db).resolve().parent / "video_poster_assets"


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class AssetStore:
    """Base/backup files for library items. Construct with an explicit root (tests)
    or via .default() for the real data-dir location."""

    def __init__(self, root):
        self.root = Path(root)

    @classmethod
    def default(cls) -> "AssetStore":
        return cls(default_root())

    def _dir(self, kind: str, item_id) -> Path:
        return self.root / str(kind) / str(int(item_id))

    def base_path(self, kind, item_id) -> Path:
        return self._dir(kind, item_id) / "base.jpg"

    def backup_path(self, kind, item_id) -> Path:
        return self._dir(kind, item_id) / "backup.jpg"

    def has_base(self, kind, item_id) -> bool:
        return self.base_path(kind, item_id).is_file()

    def write_base(self, kind, item_id, data: bytes) -> str:
        """Store (replace) the clean base layer; returns its sha1."""
        p = self.base_path(kind, item_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return sha1(data)

    def read_base(self, kind, item_id) -> bytes | None:
        p = self.base_path(kind, item_id)
        return p.read_bytes() if p.is_file() else None

    def ensure_backup(self, kind, item_id, data: bytes) -> bool:
        """Write the first-touch backup only if one doesn't exist yet. Returns True
        if it wrote (i.e. this was the first time we touched the item)."""
        p = self.backup_path(kind, item_id)
        if p.is_file():
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return True

    def read_backup(self, kind, item_id) -> bytes | None:
        p = self.backup_path(kind, item_id)
        return p.read_bytes() if p.is_file() else None

    def clear(self, kind, item_id) -> None:
        d = self._dir(kind, item_id)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
