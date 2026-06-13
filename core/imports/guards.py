"""Import post-processing guards and quarantine helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import config_manager
from core.imports.context import (
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_artist,
    get_import_original_search,
    get_import_track_info,
    normalize_import_context,
)
from core.imports.file_ops import safe_move_file
from database.music_database import MusicDatabase
from utils.logging_config import get_logger


logger = get_logger("imports.guards")


def _get_config_manager():
    return config_manager


def move_to_quarantine(file_path: str, context: dict, reason: str, automation_engine=None, *, trigger: str = "unknown") -> str:
    """Move a file to the quarantine folder and write a metadata sidecar.

    `trigger` identifies which check fired (`integrity` / `acoustid` /
    `bit_depth` / `unknown`) and is persisted in the sidecar so
    one-click Approve can set the matching `_skip_quarantine_check`
    bypass when re-running the pipeline.

    Sidecar also persists a JSON-safe snapshot of the full `context`
    dict via `serialize_quarantine_context`, enabling in-place approve
    without losing the matched-track metadata. Legacy sidecars (written
    before this expansion) lack the `context` field — Approve falls
    back to `recover_to_staging` for those.
    """
    from core.imports.quarantine import serialize_quarantine_context

    download_dir = _get_config_manager().get("soulseek.download_path", "./downloads")
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
        "trigger": trigger,
        "context": serialize_quarantine_context(context),
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
        except Exception as e:
            logger.debug("emit download_quarantined failed: %s", e)

    return str(quarantine_path)


def check_flac_bit_depth(file_path: str, context: dict) -> Optional[str]:
    """Legacy wrapper — delegates to check_quality_target.

    Kept for callers that still pass trigger='bit_depth'; the new guard
    covers bit_depth as part of the full quality target check.
    """
    return check_quality_target(file_path, context)


def check_quality_target(file_path: str, context: dict) -> Optional[str]:
    """Return a rejection message when the downloaded file does not satisfy
    the user's quality priority list.

    Probes the actual file with mutagen (ground-truth sample_rate,
    bit_depth, bitrate) and checks it against the profile's
    ``ranked_targets``.  Falls back gracefully when fallback_enabled=True.

    Works for all formats and all download sources — no Soulseek-specific
    logic here.
    """
    from core.imports.file_ops import probe_audio_quality
    from core.quality.model import QualityTarget, rank_candidate, v2_qualities_to_ranked_targets

    aq = probe_audio_quality(file_path)
    if aq is None:
        logger.debug("[QualityGuard] Could not probe %s — skipping check", os.path.basename(file_path))
        return None

    profile = MusicDatabase().get_quality_profile()
    raw_targets = profile.get("ranked_targets")
    if not raw_targets and "qualities" in profile:
        raw_targets = v2_qualities_to_ranked_targets(profile["qualities"])

    if not raw_targets:
        return None

    targets = [QualityTarget.from_dict(t) for t in raw_targets]
    fallback_enabled = profile.get("fallback_enabled", True)
    downsample_enabled = _get_config_manager().get("lossy_copy.downsample_hires", False)

    target_idx, _ = rank_candidate(aq, targets)
    matched = target_idx < len(targets)

    track_info = context.get("track_info", {})
    track_name = track_info.get("name", os.path.basename(file_path))
    actual_label = aq.label()

    if matched:
        logger.info("[QualityGuard] %s matched target '%s': %s", track_name, targets[target_idx].label, actual_label)
        return None

    # No target matched
    best_label = targets[0].label if targets else "?"
    if fallback_enabled or downsample_enabled:
        logger.warning(
            "[QualityGuard] %s did not match any target (got %s, wanted %s) — accepting via fallback",
            track_name, actual_label, best_label,
        )
        return None

    return (
        f"Quality mismatch: file is {actual_label}, "
        f"does not satisfy any configured target (best wanted: {best_label})"
    )
