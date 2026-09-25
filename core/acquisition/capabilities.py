"""Explicit download-source capabilities (ADR-08).

Decision code asks this registry what a source can do. It must never infer a
source family from usernames, filenames, or result-shape accidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class SourceCapabilities:
    source: str
    recording_download: bool
    release_bundle_download: bool
    search_by_id: bool = False
    client_queue: bool = False
    supports_cancel_remove: bool = False
    supports_quality_metadata: bool = False

    def __post_init__(self) -> None:
        if self.recording_download == self.release_bundle_download:
            raise ValueError(
                "a source must declare exactly one acquisition content scope")

    @property
    def content_scope(self) -> str:
        return "recording" if self.recording_download else "release_bundle"


_SOURCES: Dict[str, SourceCapabilities] = {
    "soulseek": SourceCapabilities(
        "soulseek", True, False, client_queue=True,
        supports_cancel_remove=True, supports_quality_metadata=True),
    "usenet": SourceCapabilities(
        "usenet", False, True, search_by_id=True, client_queue=True,
        supports_cancel_remove=True, supports_quality_metadata=True),
    "torrent": SourceCapabilities(
        "torrent", False, True, search_by_id=True, client_queue=True,
        supports_cancel_remove=True, supports_quality_metadata=True),
    "lidarr": SourceCapabilities(
        "lidarr", False, True, search_by_id=True, client_queue=True,
        supports_cancel_remove=True, supports_quality_metadata=True),
    "tidal": SourceCapabilities(
        "tidal", True, False, search_by_id=True,
        supports_quality_metadata=True),
    "deezer": SourceCapabilities(
        "deezer", True, False, search_by_id=True,
        supports_quality_metadata=True),
    "qobuz": SourceCapabilities(
        "qobuz", True, False, search_by_id=True,
        supports_quality_metadata=True),
    "hifi": SourceCapabilities(
        "hifi", True, False, supports_quality_metadata=True),
    "amazon": SourceCapabilities(
        "amazon", True, False, supports_quality_metadata=True),
    "youtube": SourceCapabilities("youtube", True, False),
    "soundcloud": SourceCapabilities("soundcloud", True, False),
}


def get_source_capabilities(source: str) -> Optional[SourceCapabilities]:
    return _SOURCES.get(str(source or "").strip().lower())


def require_source_capabilities(source: str) -> SourceCapabilities:
    capabilities = get_source_capabilities(source)
    if capabilities is None:
        raise ValueError(f"download source has no declared capabilities: {source!r}")
    return capabilities


def register_source_capabilities(capabilities: SourceCapabilities) -> None:
    """Register/replace a source declaration (primarily plugin bootstrap/tests)."""
    key = str(capabilities.source or "").strip().lower()
    if not key:
        raise ValueError("source capability name is required")
    _SOURCES[key] = capabilities


__all__ = [
    "SourceCapabilities",
    "get_source_capabilities",
    "register_source_capabilities",
    "require_source_capabilities",
]
