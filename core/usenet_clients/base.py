"""Usenet client adapter contract.

``UsenetClientAdapter`` mirrors ``TorrentClientAdapter`` in shape so
the download plugin layer can reuse the same dispatch pattern.
Differences from the torrent side:

- No magnet URI equivalent — usenet jobs are always ``.nzb`` files
  or URLs that resolve to one.
- No seed/peer counts — usenet is a download-only protocol.
- Status values reflect usenet semantics: ``downloading`` /
  ``extracting`` / ``verifying`` / ``repairing`` / ``completed`` /
  ``failed`` / ``paused``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable


@dataclass
class UsenetStatus:
    """Adapter-uniform view of one usenet job.

    Field semantics:
    - ``state`` is one of: ``queued`` | ``downloading`` | ``extracting``
      | ``verifying`` | ``repairing`` | ``completed`` | ``failed`` |
      ``paused``. Each adapter maps its native names to this set.
    - ``progress`` is 0.0–1.0 across the entire job (download + par2 +
      unpack), so a job stalled at the verify step still shows < 1.0.
    """

    id: str                          # SAB nzo_id / NZBGet NZBID
    name: str
    state: str
    progress: float
    size: int                        # total size in bytes
    downloaded: int                  # bytes downloaded so far
    download_speed: int              # bytes/sec
    eta: Optional[int] = None        # seconds, None if unknown
    save_path: Optional[str] = None
    # In-progress / pre-move directory (SAB ``incomplete_path``). Kept
    # SEPARATE from ``save_path`` on purpose: it points at the staging
    # dir SAB uses BEFORE its post-process move, so it must never be
    # treated as the final path on a normal completion. The poll loops
    # only fall back to it as a LAST RESORT — after waiting the full
    # completed-but-no-save_path window — to recover the (#721) case
    # where SAB finished, the files are physically on disk, but the
    # final ``storage`` field never lands. See ``poll_album_download``.
    incomplete_path: Optional[str] = None
    category: Optional[str] = None
    files: Optional[List[str]] = None
    error: Optional[str] = None


@runtime_checkable
class UsenetClientAdapter(Protocol):
    """Structural contract every usenet-client adapter implements."""

    def is_configured(self) -> bool: ...

    async def check_connection(self) -> bool: ...

    async def add_nzb(
        self,
        url_or_bytes,
        category: str = "soulsync",
        save_path: Optional[str] = None,
    ) -> Optional[str]:
        """Hand the usenet client either a ``.nzb`` HTTP URL (``str``)
        or the raw payload (``bytes``). Returns the client-side job id
        on success, ``None`` on failure."""
        ...

    async def get_status(self, job_id: str) -> Optional[UsenetStatus]: ...

    async def get_all(self) -> List[UsenetStatus]: ...

    async def remove(self, job_id: str, delete_files: bool = False) -> bool: ...

    async def pause(self, job_id: str) -> bool: ...

    async def resume(self, job_id: str) -> bool: ...
