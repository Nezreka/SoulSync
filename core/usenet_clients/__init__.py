"""Usenet client adapters.

Each adapter wraps one Usenet downloader (SABnzbd, NZBGet) behind
the ``UsenetClientAdapter`` Protocol so the rest of SoulSync can
talk to whichever client the user picked through one uniform
surface.

The active adapter is selected at runtime by the
``usenet_client.type`` config key. See ``get_active_adapter()``
for the factory.
"""

from __future__ import annotations

from typing import Optional

from config.settings import config_manager

from core.usenet_clients.base import UsenetClientAdapter, UsenetStatus
from core.usenet_clients.nzbget import NZBGetAdapter
from core.usenet_clients.sabnzbd import SABnzbdAdapter

__all__ = [
    "UsenetClientAdapter",
    "UsenetStatus",
    "SABnzbdAdapter",
    "NZBGetAdapter",
    "get_active_adapter",
    "adapter_for_type",
]


def adapter_for_type(client_type: str) -> Optional[UsenetClientAdapter]:
    """Build a fresh adapter instance for the given client type string.
    ``None`` for unknown types."""
    if client_type == "sabnzbd":
        return SABnzbdAdapter()
    if client_type == "nzbget":
        return NZBGetAdapter()
    return None


def get_active_adapter() -> Optional[UsenetClientAdapter]:
    """Return an adapter for whichever usenet client the user has
    selected in Settings. Reads ``usenet_client.type`` each call."""
    client_type = (config_manager.get('usenet_client.type', '') or '').strip().lower()
    if not client_type:
        return None
    return adapter_for_type(client_type)
