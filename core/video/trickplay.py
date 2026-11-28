"""Video Trickplay seek thumbnails in the Jellyfin 10.9 sidecar layout:

    <video_stem>.trickplay/320 - 10x10/0.jpg, 1.jpg, ...

Files are the source of truth: sprite sheets live right beside the video file
so Jellyfin and SoulSync share the exact same .trickplay folder without ever
analyzing a video twice or rewriting the video file.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.trickplay")

DEFAULT_WIDTH = 320
DEFAULT_COLS = 10
DEFAULT_ROWS = 10
DEFAULT_INTERVAL_S = 10

_DIR_RE = re.compile(r"^(\d+)\s*-\s*(\d+)x(\d+)$")


def trickplay_root(video_path: str) -> Path:
    p = Path(video_path)
    return p.parent / f"{p.stem}.trickplay"


def trickplay_folder(
    video_path: str,
    *,
    width: int = DEFAULT_WIDTH,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> Path:
    return trickplay_root(video_path) / f"{width} - {cols}x{rows}"


def get_trickplay_manifest(video_path: str, *, interval_s: int = DEFAULT_INTERVAL_S) -> Optional[Dict[str, Any]]:
    """Read an existing Jellyfin-compatible .trickplay folder beside ``video_path``."""
    root = trickplay_root(video_path)
    if not root.is_dir():
        return None
    best_dir = None
    best_meta = None
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        m = _DIR_RE.match(child.name.strip())
        if not m:
            continue
        w, cols, rows = int(m.group(1)), int(m.group(2)), int(m.group(3))
        sprites = sorted(
            [f for f in child.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg") and f.stem.isdigit()],
            key=lambda f: int(f.stem),
        )
        if not sprites:
            continue
        best_dir = child
        best_meta = (w, cols, rows, sprites)
        if w == DEFAULT_WIDTH:
            break

    if not best_dir or not best_meta:
        return None

    w, cols, rows, sprites = best_meta
    tile_h = int(round(w * 9 / 16))
    try:
        from PIL import Image
        with Image.open(sprites[0]) as im:
            sw, sh = im.size
            if cols > 0 and rows > 0:
                w = sw // cols
                tile_h = sh // rows
    except Exception:  # noqa: BLE001
        pass

    return {
        "folder": str(best_dir),
        "tile_width": w,
        "tile_height": tile_h,
        "cols": cols,
        "rows": rows,
        "interval_s": interval_s,
        "sprite_count": len(sprites),
        "sprites": [s.name for s in sprites],
    }


def sprite_file_path(video_path: str, sprite_index: int) -> Optional[Path]:
    manifest = get_trickplay_manifest(video_path)
    if not manifest:
        return None
    if sprite_index < 0 or sprite_index >= manifest["sprite_count"]:
        return None
    candidate = Path(manifest["folder"]) / f"{sprite_index}.jpg"
    return candidate if candidate.is_file() else None


def generate_trickplay(
    video_path: str,
    *,
    width: int = DEFAULT_WIDTH,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    interval_s: int = DEFAULT_INTERVAL_S,
    force: bool = False,
) -> Dict[str, Any]:
    """Generate Jellyfin-compatible sprite sheets beside ``video_path`` using ffmpeg."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not force:
        existing = get_trickplay_manifest(video_path, interval_s=interval_s)
        if existing:
            return {"status": "exists", **existing}

    out_dir = trickplay_folder(video_path, width=width, cols=cols, rows=rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass

    from core.player.transcode import ffmpeg_path
    ff = ffmpeg_path() or "ffmpeg"
    pattern = str(out_dir / "%d.jpg")
    vf = f"fps=1/{max(1, int(interval_s))},scale={int(width)}:-2,tile={int(cols)}x{int(rows)}"
    cmd = [
        ff, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(video_path),
        "-vf", vf,
        "-q:v", "6",
        "-start_number", "0",
        pattern,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600, check=False)  # noqa: S603
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        raise RuntimeError(f"ffmpeg trickplay generation failed: {err}")

    manifest = get_trickplay_manifest(video_path, interval_s=interval_s)
    if not manifest:
        raise RuntimeError("No trickplay sprites were produced")
    return {"status": "generated", **manifest}


def tile_for_timestamp(manifest: Dict[str, Any], seconds: float) -> Dict[str, int]:
    """Compute which sprite index and pixel (x, y) offset correspond to ``seconds``."""
    interval = max(1, int(manifest.get("interval_s") or DEFAULT_INTERVAL_S))
    cols = max(1, int(manifest.get("cols") or DEFAULT_COLS))
    rows = max(1, int(manifest.get("rows") or DEFAULT_ROWS))
    tw = int(manifest.get("tile_width") or DEFAULT_WIDTH)
    th = int(manifest.get("tile_height") or 180)
    per_sprite = cols * rows
    max_idx = max(0, int(manifest.get("sprite_count") or 1) * per_sprite - 1)
    idx = min(max_idx, max(0, int(math.floor(max(0.0, seconds) / interval))))
    sprite = idx // per_sprite
    within = idx % per_sprite
    col = within % cols
    row = within // cols
    return {
        "sprite": sprite,
        "x": col * tw,
        "y": row * th,
        "width": tw,
        "height": th,
    }
