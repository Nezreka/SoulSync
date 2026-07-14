"""Hand a chosen torrent/usenet release off to the SHARED download client.

MUSIC-SAFE: imports and CALLS the shared ``core.torrent_clients`` / ``core.usenet_clients``
adapters (same config the music side uses) — never edits them. The adapter methods are async;
we run them on a private event loop so the sync grab handler + monitor can call in. Returns a
small result carrying the client's tracking id (qBittorrent info-hash / SAB nzo_id), which the
video monitor polls for progress + completion.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("video.client_grab")


def _run(coro):
    """Run an async adapter call from sync code on a throwaway loop (no running loop here)."""
    return asyncio.run(coro)


def _torrent_category() -> str:
    from config.settings import config_manager
    return str(config_manager.get("torrent_client.category", "") or "soulsync")


def _usenet_category() -> str:
    from config.settings import config_manager
    return str(config_manager.get("usenet_client.category", "") or "soulsync")


def grab_torrent(url_or_magnet: str, *, save_path: Optional[str] = None) -> dict:
    """Add a magnet/.torrent URL to the active torrent client. Returns
    ``{ok, ref}`` (ref = the info-hash to poll) or ``{ok: False, error}``."""
    from core.torrent_clients import get_active_adapter
    adapter = get_active_adapter()
    if adapter is None or not adapter.is_configured():
        return {"ok": False, "error": "No torrent client configured — set it on Settings → Downloads."}
    try:
        ref = _run(adapter.add_torrent(url_or_magnet, category=_torrent_category(), save_path=save_path))
    except Exception as e:   # noqa: BLE001 - surface the client error to the grab handler
        logger.warning("torrent add failed: %s", e, exc_info=True)
        return {"ok": False, "error": "Torrent client: " + str(e)}
    if not ref:
        return {"ok": False, "error": "The torrent client didn't accept the release."}
    return {"ok": True, "ref": str(ref)}


def grab_usenet(url_or_nzb: Any, *, save_path: Optional[str] = None) -> dict:
    """Add an NZB (URL or bytes) to the active usenet client. Returns
    ``{ok, ref}`` (ref = the nzo_id/NZBID to poll) or ``{ok: False, error}``."""
    from core.usenet_clients import get_active_adapter
    adapter = get_active_adapter()
    if adapter is None or not adapter.is_configured():
        return {"ok": False, "error": "No usenet client configured — set it on Settings → Downloads."}
    try:
        ref = _run(adapter.add_nzb(url_or_nzb, category=_usenet_category(), save_path=save_path))
    except Exception as e:   # noqa: BLE001
        logger.warning("usenet add failed: %s", e, exc_info=True)
        return {"ok": False, "error": "Usenet client: " + str(e)}
    if not ref:
        return {"ok": False, "error": "The usenet client didn't accept the NZB."}
    return {"ok": True, "ref": str(ref)}


def grab(source: str, url: Any, *, save_path: Optional[str] = None) -> dict:
    """Dispatch a grab by source (torrent | usenet)."""
    if str(source).lower() == "torrent":
        return grab_torrent(url, save_path=save_path)
    if str(source).lower() == "usenet":
        return grab_usenet(url, save_path=save_path)
    return {"ok": False, "error": "Unsupported source %r" % source}
