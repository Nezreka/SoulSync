"""Config export/import — one portable bundle for both sides (Kazimir's
"checkout" menu for migrating installs).

A SoulSync install's settings live in two places: the music side's
``config.json`` (connections, download sources, enrichment, organization…)
and the video side's ``video_settings`` KV (quality profiles, custom
formats, import lists, notifications, organization templates…). This
assembles BOTH into a single JSON bundle so a reinstall/migration is one
export → one import, no two-pipeline dance.

Secrets (API keys, tokens, passwords) are REDACTED by default — the export
is safe to share/store. ``include_secrets=True`` embeds the real values for
a true one-click migration; the caller (endpoint + UI) gates that behind an
explicit opt-in + warning because the file is then plaintext credentials.

Pure assembly + a strict validator; the endpoint owns request/response and
the actual config writes. No web_server imports.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from utils.logging_config import get_logger

logger = get_logger("config_export")

BUNDLE_MARKER = "soulsync_config_export"
BUNDLE_VERSION = 1

# video_settings keys that are one-time flags / internal bookkeeping — never
# migrate these (they'd re-suppress a fresh install's own first-run logic).
_VIDEO_EXCLUDE = frozenset({
    "studio_network_links_seeded", "avail_dates_logic", "details_synced_heal",
    "video_last_scan_at",
})


def build_bundle(config_manager, video_db, *, include_secrets: bool,
                 exported_at: str, app_version: str = "") -> Dict[str, Any]:
    """Assemble the portable config bundle. ``exported_at``/``app_version`` are
    passed in (no clock/global reads here). Music config is the full decrypted
    dict when ``include_secrets`` else the redacted one."""
    music = (config_manager.get_full_config() if include_secrets
             else config_manager.redacted_config())
    try:
        video = video_db.all_video_settings(exclude=_VIDEO_EXCLUDE)
    except Exception:   # noqa: BLE001 - a video-side hiccup shouldn't sink a music export
        logger.exception("config export: video settings dump failed")
        video = {}
    return {
        BUNDLE_MARKER: True,
        "bundle_version": BUNDLE_VERSION,
        "app_version": app_version,
        "exported_at": exported_at,
        "includes_secrets": bool(include_secrets),
        "music": music,
        "video": video,
    }


def validate_bundle(data: Any) -> Tuple[bool, str]:
    """Is ``data`` a SoulSync config bundle we can import? Returns (ok, reason)."""
    if not isinstance(data, dict):
        return False, "Not a JSON object."
    if not data.get(BUNDLE_MARKER):
        return False, "This file isn't a SoulSync config export."
    ver = data.get("bundle_version")
    if not isinstance(ver, int) or ver > BUNDLE_VERSION:
        return False, "This export was made by a newer SoulSync; update first."
    if not isinstance(data.get("music"), dict) or not isinstance(data.get("video"), dict):
        return False, "The export is missing its music/video sections."
    return True, ""


def apply_bundle(config_manager, video_db, data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a validated bundle to this install. Music config merges through the
    config_manager (its set() already ignores round-tripped REDACTED masks, so
    importing a secrets-redacted bundle never blanks an existing secret); video
    settings upsert. Returns a small summary."""
    ok, reason = validate_bundle(data)
    if not ok:
        raise ValueError(reason)
    music_keys = config_manager.apply_config_dict(data.get("music") or {})
    video_keys = video_db.replace_video_settings(data.get("video") or {})
    return {"music_keys": music_keys, "video_keys": video_keys}


__all__ = ["BUNDLE_MARKER", "BUNDLE_VERSION", "build_bundle",
           "validate_bundle", "apply_bundle"]
