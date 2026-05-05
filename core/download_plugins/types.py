"""Shared dataclasses for the download-plugin contract.

Every download source returns the same shape for search hits and
download status — the four classes here are the canonical types
that the ``DownloadSourcePlugin`` Protocol exchanges. Living in
this neutral module (rather than ``core/soulseek_client.py`` where
they grew up by accident) means a new plugin doesn't have to import
from a sibling source just to satisfy the contract.

Move history: extracted from ``core.soulseek_client`` so plugins
import from a neutral package per Cin's contract-first standard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.imports.filename import parse_filename_metadata


@dataclass
class SearchResult:
    """Base class for search results"""
    username: str
    filename: str
    size: int
    bitrate: Optional[int]
    duration: Optional[int]  # Duration in milliseconds (converted from slskd's seconds)
    quality: str
    free_upload_slots: int
    upload_speed: int
    queue_length: int
    result_type: str = "track"  # "track" or "album"

    @property
    def quality_score(self) -> float:
        quality_weights = {
            'flac': 1.0,
            'mp3': 0.8,
            'ogg': 0.7,
            'aac': 0.6,
            'wma': 0.5
        }

        base_score = quality_weights.get(self.quality.lower(), 0.3)

        if self.bitrate:
            if self.bitrate >= 320:
                base_score += 0.2
            elif self.bitrate >= 256:
                base_score += 0.1
            elif self.bitrate < 128:
                base_score -= 0.2

        # Free upload slots
        if self.free_upload_slots == 0:
            base_score -= 0.15
        elif self.free_upload_slots > 0:
            base_score += 0.05

        # Upload speed in bytes/sec (tiered)
        if self.upload_speed >= 5_000_000:      # ~5 MB/s / 40 Mbps
            base_score += 0.15
        elif self.upload_speed >= 1_000_000:    # ~1 MB/s / 8 Mbps
            base_score += 0.10
        elif self.upload_speed >= 500_000:      # ~500 KB/s / 4 Mbps
            base_score += 0.05
        elif self.upload_speed < 100_000:       # ~100 KB/s / 800 kbps
            base_score -= 0.05

        # Queue length (graduated penalty)
        if self.queue_length > 50:
            base_score -= 0.25
        elif self.queue_length > 20:
            base_score -= 0.15
        elif self.queue_length > 10:
            base_score -= 0.10

        return min(base_score, 1.0)


@dataclass
class TrackResult(SearchResult):
    """Individual track search result"""
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = None
    _source_metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        self.result_type = "track"
        # Try to extract metadata from filename if not provided
        if not self.title or not self.artist:
            self._parse_filename_metadata()

    def _parse_filename_metadata(self):
        """Extract artist, title, album from filename patterns"""
        parsed = parse_filename_metadata(self.filename)
        if not self.artist and parsed.get("artist"):
            self.artist = parsed["artist"]
        if not self.title and parsed.get("title"):
            self.title = parsed["title"]
        if not self.album and parsed.get("album"):
            self.album = parsed["album"]
        if self.track_number is None:
            track_number = parsed.get("track_number")
            if track_number is not None:
                self.track_number = track_number


@dataclass
class AlbumResult:
    """Album/folder search result containing multiple tracks"""
    username: str
    album_path: str  # Directory path
    album_title: str
    artist: Optional[str]
    track_count: int
    total_size: int
    tracks: List[TrackResult]
    dominant_quality: str  # Most common quality in album
    year: Optional[str] = None
    free_upload_slots: int = 0
    upload_speed: int = 0
    queue_length: int = 0
    result_type: str = "album"

    @property
    def quality_score(self) -> float:
        """Calculate album quality score based on dominant quality and track count"""
        quality_weights = {
            'flac': 1.0,
            'mp3': 0.8,
            'ogg': 0.7,
            'aac': 0.6,
            'wma': 0.5
        }

        base_score = quality_weights.get(self.dominant_quality.lower(), 0.3)

        # Bonus for complete albums (typically 8-15 tracks)
        if 8 <= self.track_count <= 20:
            base_score += 0.1
        elif self.track_count > 20:
            base_score += 0.05

        # Free upload slots
        if self.free_upload_slots == 0:
            base_score -= 0.15
        elif self.free_upload_slots > 0:
            base_score += 0.05

        # Upload speed in bytes/sec (tiered)
        if self.upload_speed >= 5_000_000:      # ~5 MB/s / 40 Mbps
            base_score += 0.15
        elif self.upload_speed >= 1_000_000:    # ~1 MB/s / 8 Mbps
            base_score += 0.10
        elif self.upload_speed >= 500_000:      # ~500 KB/s / 4 Mbps
            base_score += 0.05
        elif self.upload_speed < 100_000:       # ~100 KB/s / 800 kbps
            base_score -= 0.05

        # Queue length (graduated penalty)
        if self.queue_length > 50:
            base_score -= 0.25
        elif self.queue_length > 20:
            base_score -= 0.15
        elif self.queue_length > 10:
            base_score -= 0.10

        return min(base_score, 1.0)

    @property
    def size_mb(self) -> int:
        """Album size in MB"""
        return self.total_size // (1024 * 1024)

    @property
    def average_track_size_mb(self) -> float:
        """Average track size in MB"""
        if self.track_count > 0:
            return self.size_mb / self.track_count
        return 0


@dataclass
class DownloadStatus:
    id: str
    filename: str
    username: str
    state: str
    progress: float
    size: int
    transferred: int
    speed: int
    time_remaining: Optional[int] = None
    file_path: Optional[str] = None
