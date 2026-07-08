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

    # ── uploaded template images (logos/ribbons a user drops in) ──────────────
    def _uploads_dir(self) -> Path:
        return self.root / "_uploads"

    def save_upload(self, data: bytes, ext: str = "png") -> str:
        """Content-address an uploaded image (dedups identical files); returns the
        stored name to reference as ``asset://<name>``."""
        ext = "".join(c for c in (ext or "png").lower().lstrip(".") if c.isalnum())[:5] or "png"
        name = sha1(data)[:16] + "." + ext
        d = self._uploads_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        if not p.is_file():
            p.write_bytes(data)
        return name

    def read_upload(self, name: str) -> bytes | None:
        # basename-only guard against path traversal
        safe = Path(str(name)).name
        p = self._uploads_dir() / safe
        return p.read_bytes() if p.is_file() else None

    # ── drop-in logo packs (field value -> brand mark), one folder per field ──
    def _logos_dir(self) -> Path:
        return self.root / "logos"

    def read_logo(self, pack: str, name: str) -> bytes | None:
        """Bytes of a logo in a drop-in pack, or None if that pack/name isn't
        present. Tries common raster extensions. basename-guarded (no traversal)."""
        safe_pack = Path(str(pack)).name
        safe_name = Path(str(name)).name
        d = self._logos_dir() / safe_pack
        for ext in ("png", "webp", "jpg", "jpeg"):
            p = d / f"{safe_name}.{ext}"
            if p.is_file():
                return p.read_bytes()
        return None

    def has_logo_pack(self, pack: str) -> bool:
        return (self._logos_dir() / Path(str(pack)).name).is_dir()

    def write_logo(self, pack: str, name: str, data: bytes, ext: str = "png") -> None:
        """Write a logo into a pack folder (used by the Kometa pack installer).
        basename-guarded; creates the pack folder on first write."""
        safe_ext = Path(str(ext)).name.lstrip(".") or "png"
        d = self._logos_dir() / Path(str(pack)).name
        d.mkdir(parents=True, exist_ok=True)
        (d / (Path(str(name)).name + "." + safe_ext)).write_bytes(data)

    def logo_pack_counts(self) -> dict:
        """{pack: file_count} across every installed logo pack — powers the
        'is a pack installed?' gate and the per-field status in the UI."""
        base = self._logos_dir()
        if not base.is_dir():
            return {}
        out = {}
        for d in base.iterdir():
            if d.is_dir():
                n = sum(1 for f in d.iterdir()
                        if f.is_file() and f.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg"))
                if n:
                    out[d.name] = n
        return out

    # ── cached gallery thumbnails (rendered once, keyed by definition hash) ────
    def _thumbs_dir(self) -> Path:
        return self.root / "_thumbs"

    def _thumb_prefix(self, template_id) -> str:
        return str(int(template_id)) + "_"

    def read_thumb(self, template_id, defhash: str) -> bytes | None:
        p = self._thumbs_dir() / (self._thumb_prefix(template_id) + str(defhash) + ".jpg")
        return p.read_bytes() if p.is_file() else None

    def write_thumb(self, template_id, defhash: str, data: bytes) -> None:
        """Cache a rendered thumbnail; drops any older cache for this template so
        stale definition-hashes don't accumulate."""
        d = self._thumbs_dir()
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob(self._thumb_prefix(template_id) + "*.jpg"):
            try:
                f.unlink()
            except OSError:
                pass
        (d / (self._thumb_prefix(template_id) + str(defhash) + ".jpg")).write_bytes(data)

    def clear_thumb(self, template_id) -> None:
        d = self._thumbs_dir()
        if not d.is_dir():
            return
        for f in d.glob(self._thumb_prefix(template_id) + "*.jpg"):
            try:
                f.unlink()
            except OSError:
                pass
