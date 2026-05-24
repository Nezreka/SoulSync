"""Torrent client adapters.

Each adapter wraps one BitTorrent client (qBittorrent, Transmission,
Deluge) behind the ``TorrentClientAdapter`` Protocol so the rest of
SoulSync can talk to whichever client the user picked through one
uniform surface.

The active adapter is selected at runtime by the ``torrent_client.type``
config key. See ``get_active_adapter()`` for the factory.
"""

from __future__ import annotations

from typing import Optional

from config.settings import config_manager

from core.torrent_clients.base import TorrentClientAdapter, TorrentStatus
from core.torrent_clients.deluge import DelugeAdapter
from core.torrent_clients.qbittorrent import QBittorrentAdapter
from core.torrent_clients.transmission import TransmissionAdapter

__all__ = [
    "TorrentClientAdapter",
    "TorrentStatus",
    "QBittorrentAdapter",
    "TransmissionAdapter",
    "DelugeAdapter",
    "get_active_adapter",
    "adapter_for_type",
]


def adapter_for_type(client_type: str) -> Optional[TorrentClientAdapter]:
    """Build a fresh adapter instance for the given client type string.

    ``None`` for unknown types so callers can present a helpful error
    rather than crashing on a typo'd config value.
    """
    if client_type == "qbittorrent":
        return QBittorrentAdapter()
    if client_type == "transmission":
        return TransmissionAdapter()
    if client_type == "deluge":
        return DelugeAdapter()
    return None


def get_active_adapter() -> Optional[TorrentClientAdapter]:
    """Return an adapter for whichever torrent client the user has
    selected in Settings. Reads ``torrent_client.type`` each call so a
    settings change is picked up without restarting the process."""
    client_type = (config_manager.get('torrent_client.type', '') or '').strip().lower()
    if not client_type:
        return None
    return adapter_for_type(client_type)
