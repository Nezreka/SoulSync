"""Hand a chosen audiobook release to the shared torrent / usenet client.

MUSIC-SAFE, in the same sense core/video/client_grab.py is: this imports and
CALLS the shared ``core.torrent_clients`` / ``core.usenet_clients`` adapters —
the same clients, the same configuration the music side uses — and never edits
them. Nothing here touches the music download batches, the music worker pool, or
the music wishlist.

The adapters are async; Flask's handlers and the wishlist worker are not, so the
call runs on a throwaway loop. Returns the client's own tracking id (a
qBittorrent info-hash, a SABnzbd nzo_id) which the audiobook download monitor
polls for progress.

Downloads land in the audiobook download directory rather than the music one, so
a half-finished book never appears under a music root where the library scanner
would find it and file chapter files as an album.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_grab")


def _run(coro):
    """Run one async adapter call from sync code on a throwaway loop."""
    return asyncio.run(coro)


def _category(kind: str) -> str:
    """Downloader category for audiobook grabs.

    Its own category, defaulting to "audiobooks", so a user can route them to a
    different folder in qBittorrent/SAB and so a completed book is never
    mistaken for a music release by anything watching the music category.
    """
    from core.settings import config_manager
    configured = config_manager.get(f"audiobooks.{kind}_category", "") or ""
    return str(configured) or "audiobooks"


def audiobook_download_path() -> Optional[str]:
    """Where audiobook downloads should land, or None to let the client decide.

    Kept apart from the music download directory on purpose: a part-downloaded
    book under a music root is a folder of loose chapter files, which is exactly
    what the library scanner will try to import as an album.
    """
    try:
        from core.settings import config_manager
        path = config_manager.get("audiobooks.download_path", "") or ""
    except Exception:                                       # noqa: BLE001
        return None
    path = str(path).strip()
    if not path:
        return None
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create the audiobook download path %s: %s", path, exc)
        return None
    return path


def grab_torrent(url_or_magnet: str, *, save_path: Optional[str] = None,
                 fallback_magnet: Optional[str] = None) -> Dict[str, Any]:
    """Add a magnet or .torrent URL to the active torrent client.

    Returns ``{ok, ref}`` where ref is the info-hash to poll, or
    ``{ok: False, error}`` with a message meant for the user.

    ``fallback_magnet`` is the same release's magnet carried from the search
    hit. The .torrent URL goes first so it can be fetched server-side, but a URL
    this process cannot reach would be a dead end where the magnet still works.
    """
    from core.torrent_clients import get_active_adapter

    adapter = get_active_adapter()
    if adapter is None or not adapter.is_configured():
        return {"ok": False,
                "error": "No torrent client configured — set one on Settings → Downloads."}
    try:
        from core.torrent_clients.base import add_torrent_smart
        ref = _run(add_torrent_smart(
            adapter, url_or_magnet,
            category=_category("torrent"),
            save_path=save_path,
            fallback_magnet=fallback_magnet,
        ))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Audiobook torrent add failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"Torrent client: {exc}"}
    if not ref:
        return {"ok": False, "error": "The torrent client didn't accept the release."}
    return {"ok": True, "ref": str(ref)}


def grab_usenet(url_or_nzb: Any, *, save_path: Optional[str] = None) -> Dict[str, Any]:
    """Add an NZB (URL or bytes) to the active usenet client.

    Returns ``{ok, ref}`` where ref is the nzo_id to poll, or
    ``{ok: False, error}``.
    """
    from core.usenet_clients import get_active_adapter

    adapter = get_active_adapter()
    if adapter is None or not adapter.is_configured():
        return {"ok": False,
                "error": "No usenet client configured — set one on Settings → Downloads."}
    try:
        ref = _run(adapter.add_nzb(
            url_or_nzb, category=_category("usenet"), save_path=save_path,
        ))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Audiobook usenet add failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"Usenet client: {exc}"}
    if not ref:
        return {"ok": False, "error": "The usenet client didn't accept the NZB."}
    return {"ok": True, "ref": str(ref)}


def grab_release(release: Any, save_path: Optional[str] = None) -> Dict[str, Any]:
    """Send one AudiobookRelease to whichever client its protocol needs.

    Accepts the dataclass or the dict form, so an API handler can pass a payload
    straight back from the client without rebuilding it.

    Torrents prefer the .torrent URL with the magnet as a fallback; usenet has
    no such pair and takes the URL alone.
    """
    protocol = str(_field(release, "protocol") or "").lower()
    download_url = _field(release, "download_url")
    magnet = _field(release, "magnet_uri")
    target = save_path if save_path is not None else audiobook_download_path()

    if protocol == "torrent":
        primary = download_url or magnet
        if not primary:
            return {"ok": False, "error": "That release has no download link."}
        return grab_torrent(primary, save_path=target,
                            fallback_magnet=magnet if magnet != primary else None)

    if protocol == "usenet":
        if not download_url:
            return {"ok": False, "error": "That release has no NZB link."}
        return grab_usenet(download_url, save_path=target)

    return {"ok": False, "error": f"Unsupported release protocol {protocol!r}."}


def _field(release: Any, name: str) -> Any:
    """Read a field off either the dataclass or its dict form."""
    if isinstance(release, dict):
        return release.get(name)
    return getattr(release, name, None)
