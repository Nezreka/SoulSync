"""Torrent client adapter contract.

``TorrentClientAdapter`` is a structural Protocol — any class with
these method signatures is treated as a valid adapter. The download
plugin layer (built in a later commit) dispatches generically against
this surface so it doesn't have to know whether the user picked
qBittorrent, Transmission, or Deluge.

The contract intentionally hides protocol-specific details:
- qBittorrent uses cookie auth + multipart form uploads.
- Transmission uses an X-Transmission-Session-Id header + JSON RPC.
- Deluge 2.x uses /json with a session cookie.

All three converge on the same eight verbs below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable


def normalize_client_url(raw: str) -> str:
    """Clean a user-entered WebUI URL into something ``requests`` accepts.

    Users routinely type a bare host like ``192.168.1.5:8080`` or
    ``qbittorrent.lan:8080`` with no scheme. ``requests`` then raises
    "No connection adapters were found for '...'" because it can't pick an
    http/https adapter (a bare ``host:port`` even gets misparsed as
    ``scheme=host``). Default a missing scheme to ``http://`` and trim a
    trailing slash. Empty input passes through unchanged.
    """
    url = (raw or '').strip()
    if not url:
        return ''
    if '://' not in url:
        url = 'http://' + url
    return url.rstrip('/')


@dataclass
class TorrentStatus:
    """Adapter-uniform view of one torrent's live state.

    Field semantics:
    - ``state`` is one of: ``queued`` | ``downloading`` | ``seeding`` |
      ``paused`` | ``stalled`` | ``error`` | ``completed``. Each
      adapter maps its native state names to this set.
    - ``progress`` is 0.0–1.0.
    - ``save_path`` is where files land on the torrent client's host.
      For remote clients this is a path on the *remote* machine.
    - ``files`` is the list of relative paths inside the torrent. Empty
      until the client has finished fetching the metadata.
    """

    id: str                          # torrent hash (qBit, Deluge) or numeric id (Transmission)
    name: str
    state: str
    progress: float
    size: int                        # total size in bytes
    downloaded: int                  # bytes downloaded so far
    download_speed: int              # bytes/sec
    upload_speed: int                # bytes/sec
    seeders: int = 0
    peers: int = 0
    eta: Optional[int] = None        # seconds, None if unknown
    save_path: Optional[str] = None
    files: Optional[List[str]] = None
    error: Optional[str] = None


@runtime_checkable
class TorrentClientAdapter(Protocol):
    """Structural contract every torrent-client adapter implements."""

    def is_configured(self) -> bool:
        """True when the adapter has a URL and any credentials it
        needs. Reads from config_manager — never raises on missing
        config, just returns False so the orchestrator can dim the
        torrent download source in the UI."""
        ...

    async def check_connection(self) -> bool:
        """Probe the client over the network. Logs in if required."""
        ...

    async def add_torrent(
        self,
        url_or_magnet: str,
        category: str = "soulsync",
        save_path: Optional[str] = None,
    ) -> Optional[str]:
        """Hand the torrent client a HTTP/HTTPS URL pointing to a
        ``.torrent`` file or a ``magnet:`` URI. Returns the torrent's
        client-side identifier (info-hash for qBit / Deluge, numeric
        id for Transmission) or ``None`` on failure."""
        ...

    async def add_torrent_file(
        self,
        file_bytes: bytes,
        category: str = "soulsync",
        save_path: Optional[str] = None,
    ) -> Optional[str]:
        """Upload a raw ``.torrent`` payload. Same return as
        ``add_torrent``. Used when the indexer doesn't expose a
        direct download URL and SoulSync had to fetch the file
        itself first."""
        ...

    async def get_status(self, torrent_id: str) -> Optional[TorrentStatus]:
        """Return live status for one torrent, or ``None`` if the
        client doesn't know about it."""
        ...

    async def get_all(self) -> List[TorrentStatus]:
        """Return live status for every torrent the client currently
        tracks. Used by the global download list."""
        ...

    async def remove(self, torrent_id: str, delete_files: bool = False) -> bool:
        """Remove the torrent from the client. ``delete_files=True``
        also deletes the downloaded data on disk."""
        ...

    async def pause(self, torrent_id: str) -> bool: ...

    async def resume(self, torrent_id: str) -> bool: ...
