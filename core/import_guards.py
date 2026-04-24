"""Import post-processing guards and quarantine helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.import_context import (
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_artist,
    get_import_original_search,
    get_import_track_info,
    normalize_import_context,
)
from core.import_file_ops import safe_move_file
from database.music_database import MusicDatabase
from utils.logging_config import get_logger


logger = get_logger("import_guards")


def _get_config_manager():
    from config.settings import config_manager

    return config_manager


def move_to_quarantine(file_path: str, context: dict, reason: str, automation_engine=None) -> str:
    """Move a file to the quarantine folder and write a metadata sidecar."""
    config_manager = _get_config_manager()
    download_dir = config_manager.get("soulseek.download_path", "./downloads")
    quarantine_dir = Path(download_dir) / "ss_quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_name = Path(file_path).stem
    file_ext = Path(file_path).suffix

    quarantine_filename = f"{timestamp}_{original_name}{file_ext}.quarantined"
    quarantine_path = quarantine_dir / quarantine_filename

    safe_move_file(file_path, str(quarantine_path))

    metadata_path = quarantine_dir / f"{timestamp}_{original_name}.json"
    context = normalize_import_context(context)
    original_search = get_import_original_search(context)
    artist_context = get_import_context_artist(context)

    metadata = {
        "original_filename": Path(file_path).name,
        "quarantine_reason": reason,
        "timestamp": datetime.now().isoformat(),
        "expected_track": get_import_clean_title(context, default=original_search.get("title", "Unknown")),
        "expected_artist": get_import_clean_artist(context, default=(artist_context.get("name", "") if isinstance(artist_context, dict) else "Unknown")),
        "context_key": context.get("context_key", "unknown"),
    }

    try:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Failed to write quarantine metadata: %s", exc)

    logger.warning("File quarantined: %s - Reason: %s", quarantine_path, reason)

    if automation_engine:
        try:
            ti = context.get("track_info", {})
            artists = ti.get("artists", [])
            artist_name = ""
            if artists:
                first = artists[0]
                artist_name = first.get("name", str(first)) if isinstance(first, dict) else str(first)
            automation_engine.emit(
                "download_quarantined",
                {
                    "artist": artist_name,
                    "title": ti.get("name", ""),
                    "reason": reason or "Unknown",
                },
            )
        except Exception:
            pass

    return str(quarantine_path)


def check_flac_bit_depth(file_path: str, context: dict) -> Optional[str]:
    """Return a rejection message if a FLAC file violates the configured bit depth."""
    if not context.get("_audio_quality", "").startswith("FLAC"):
        return None

    config_manager = _get_config_manager()
    quality_profile = MusicDatabase().get_quality_profile()
    flac_config = quality_profile.get("qualities", {}).get("flac", {})
    flac_pref = flac_config.get("bit_depth", "any")
    if flac_pref == "any":
        return None

    actual_bits = context["_audio_quality"].replace("FLAC ", "").replace("bit", "")
    if actual_bits == flac_pref:
        return None

    flac_fallback = flac_config.get("bit_depth_fallback", True)
    downsample_enabled = config_manager.get("lossy_copy.downsample_hires", False)
    track_info = context.get("track_info", {})
    track_name = track_info.get("name", os.path.basename(file_path))

    if flac_fallback or downsample_enabled:
        if downsample_enabled:
            logger.info("[FLAC Downsample] Accepted %s-bit FLAC (will be downsampled to %s-bit): %s", actual_bits, flac_pref, track_name)
        else:
            logger.warning("[FLAC Fallback] Accepted %s-bit FLAC (preferred %s-bit): %s", actual_bits, flac_pref, track_name)
        return None

    return f"FLAC bit depth mismatch: file is {actual_bits}-bit, preference is {flac_pref}-bit"
