"""Amazon Music download source plugin backed by a T2Tunes proxy.

NOT yet wired into the app registry — validated in isolation only.
See tests/tools/test_amazon_download_client.py.

Filename encoding (the DownloadSourcePlugin dispatch contract):
    "{asin}||{display_name}"
    e.g. "B09XYZ1234||Kendrick Lamar - Not Like Us"

Codec preference order: FLAC → Opus → EAC3.

Download flow (from Tubifarry reference implementation):
    1. GET stream_url → encrypted bytes on disk
    2. FFmpeg -decryption_key <hex> to decrypt in place
    3. Embed metadata tags (handled by the app's standard post-processing)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests as http_requests

from config.settings import config_manager
from core.amazon_client import AmazonClient, AmazonClientError
from core.download_plugins.base import DownloadSourcePlugin
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult
from utils.logging_config import get_logger

logger = get_logger("amazon_download_client")

# Quality / codec helpers
CODEC_PREFERENCE = ["flac", "opus", "eac3"]

_CODEC_EXTENSIONS: Dict[str, str] = {
    "flac": "flac",
    "ogg_vorbis": "ogg",
    "opus": "opus",
    "eac3": "eac3",
    "mp4": "m4a",
    "aac": "m4a",
    "mp3": "mp3",
}

MIN_AUDIO_BYTES = 512 * 1024  # 512 KB — anything smaller is a broken stream


def _codec_key(codec: str) -> str:
    return codec.lower().replace("-", "_").replace(" ", "_")


def _file_extension(codec: str) -> str:
    return _CODEC_EXTENSIONS.get(_codec_key(codec), "bin")


def _quality_label(codec: str, sample_rate: Optional[int] = None) -> str:
    ck = _codec_key(codec)
    if ck == "flac":
        if sample_rate and sample_rate > 48000:
            return "Hi-Res"
        return "Lossless"
    return "Lossy"


class AmazonDownloadClient(DownloadSourcePlugin):
    """DownloadSourcePlugin — Amazon Music via T2Tunes proxy."""

    def __init__(self, download_path: Optional[str] = None) -> None:
        if download_path is None:
            download_path = config_manager.get("soulseek.download_path", "./downloads")
        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        self._quality = config_manager.get("amazon_download.quality", "flac")
        self._allow_fallback = config_manager.get("amazon_download.allow_fallback", True)

        self._client = AmazonClient(preferred_codec=self._quality)
        self.session = http_requests.Session()
        self.session.headers.update({
            "User-Agent": "SoulSync/1.0",
            "Accept": "*/*",
        })

        self._engine: Any = None
        self.shutdown_check: Any = None

    # ------------------------------------------------------------------
    # DownloadSourcePlugin — lifecycle
    # ------------------------------------------------------------------

    def set_engine(self, engine) -> None:
        """Engine callback — wires the central thread worker + state store."""
        self._engine = engine

    def set_shutdown_check(self, check_callable) -> None:
        self.shutdown_check = check_callable

    def is_configured(self) -> bool:
        # T2Tunes has a public default instance; no credentials required.
        # Return True unconditionally so the source shows as available.
        return True

    async def check_connection(self) -> bool:
        try:
            return self._client.is_authenticated()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # DownloadSourcePlugin — search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        progress_callback: Any = None,
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        try:
            items = self._client.search_raw(query, types="track,album")
        except AmazonClientError as exc:
            logger.warning(f"Amazon search failed for {query!r}: {exc}")
            return [], []

        track_results: List[TrackResult] = []
        album_map: Dict[str, AlbumResult] = {}
        album_order: List[str] = []
        preferred = self._client.preferred_codec

        for item in items:
            quality = _quality_label(preferred)
            if item.is_track:
                track_results.append(TrackResult(
                    username="amazon",
                    filename=f"{item.asin}||{item.artist_name} - {item.title}",
                    size=0,
                    bitrate=None,
                    duration=item.duration_seconds * 1000 if item.duration_seconds else None,
                    quality=quality,
                    free_upload_slots=999,
                    upload_speed=999_999,
                    queue_length=0,
                    artist=item.artist_name,
                    title=item.title,
                    album=item.album_name,
                    _source_metadata={
                        "asin": item.asin,
                        "album_asin": item.album_asin,
                        "isrc": item.isrc,
                    },
                ))
            elif item.is_album:
                album_asin = item.album_asin or item.asin
                if album_asin not in album_map:
                    placeholder = TrackResult(
                        username="amazon",
                        filename=f"{item.asin}||{item.artist_name} - {item.title}",
                        size=0,
                        bitrate=None,
                        duration=None,
                        quality=quality,
                        free_upload_slots=999,
                        upload_speed=999_999,
                        queue_length=0,
                        artist=item.artist_name,
                        title=item.title,
                        album=item.album_name,
                    )
                    album_map[album_asin] = AlbumResult(
                        username="amazon",
                        album_path=album_asin,
                        album_title=item.album_name or item.title,
                        artist=item.artist_name,
                        track_count=0,
                        total_size=0,
                        tracks=[placeholder],
                        dominant_quality=quality,
                    )
                    album_order.append(album_asin)

        return track_results, [album_map[k] for k in album_order]

    # ------------------------------------------------------------------
    # DownloadSourcePlugin — download dispatch
    # ------------------------------------------------------------------

    async def download(
        self,
        username: str,
        filename: str,
        file_size: int = 0,
    ) -> Optional[str]:
        if "||" not in filename:
            logger.error(f"Invalid Amazon filename format (no '||'): {filename!r}")
            return None
        if self._engine is None:
            raise RuntimeError(
                "AmazonDownloadClient._engine is not set — "
                "client not connected to download infrastructure"
            )
        asin, display_name = filename.split("||", 1)
        asin = asin.strip()
        display_name = display_name.strip()
        return self._engine.worker.dispatch(
            source_name="amazon",
            target_id=asin,
            display_name=display_name,
            original_filename=filename,
            impl_callable=self._download_sync,
        )

    def _download_sync(
        self,
        download_id: str,
        target_id: str,
        display_name: str,
    ) -> Optional[str]:
        asin = str(target_id)
        codecs = CODEC_PREFERENCE if self._allow_fallback else [self._quality]
        for codec in codecs:
            try:
                streams = self._client.media_from_asin(asin, codec=codec)
            except AmazonClientError as exc:
                logger.warning(f"media_from_asin({asin!r}, {codec}) failed: {exc}")
                continue

            stream = next(
                (s for s in streams if s.streamable and s.stream_url),
                None,
            )
            if not stream:
                logger.debug(f"No streamable result for {asin} at codec={codec}")
                continue

            ext = _file_extension(stream.codec or codec)
            safe = "".join(
                ch if ch.isalnum() or ch in " -_." else "_"
                for ch in display_name
            )[:80]
            # T2Tunes always serves audio in an encrypted MP4 container.
            # The codec extension (.flac, .opus, .eac3) is only for the
            # final decrypted output.
            enc_ext = "mp4" if stream.decryption_key else ext
            enc_path = self._unique_path(self.download_path / f"{safe}.enc.{enc_ext}")
            out_path = self._unique_path(self.download_path / f"{safe}.{ext}")

            if self._engine is not None:
                self._engine.update_record(
                    "amazon", download_id, {"state": "downloading", "progress": 0.0}
                )
            try:
                downloaded = self._stream_to_file(stream.stream_url, enc_path, download_id)
            except Exception as exc:
                logger.warning(f"Stream download failed for {asin} ({codec}): {exc}")
                enc_path.unlink(missing_ok=True)
                continue

            if downloaded < MIN_AUDIO_BYTES:
                logger.warning(
                    f"File too small ({downloaded} B) for {asin} at {codec} — trying next"
                )
                enc_path.unlink(missing_ok=True)
                continue

            if stream.decryption_key:
                if self._engine is not None:
                    self._engine.update_record(
                        "amazon", download_id, {"state": "decrypting", "progress": 1.0}
                    )
                try:
                    self._decrypt_with_ffmpeg(enc_path, out_path, stream.decryption_key)
                    enc_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.error(f"Decryption failed for {asin} ({codec}): {exc}")
                    enc_path.unlink(missing_ok=True)
                    out_path.unlink(missing_ok=True)
                    continue
            else:
                enc_path.rename(out_path)

            logger.info(
                f"Amazon download complete ({codec}): {out_path} "
                f"({out_path.stat().st_size / (1024 * 1024):.1f} MB)"
            )
            return str(out_path)

        logger.error(f"All codec tiers exhausted for '{display_name}' ({asin})")
        return None

    def _decrypt_with_ffmpeg(
        self, enc_path: Path, out_path: Path, hex_key: str
    ) -> None:
        """Decrypt a T2Tunes encrypted audio file using FFmpeg -decryption_key.

        T2Tunes uses CENC (Common Encryption) for DRM-protected tracks.
        FFmpeg supports decryption via the -decryption_key flag when the
        key is provided in hex.
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            tools_dir = Path(__file__).parent.parent / "tools"
            candidate = tools_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if candidate.exists():
                ffmpeg = str(candidate)
            else:
                raise RuntimeError(
                    "ffmpeg is required for Amazon Music decryption. "
                    "Install ffmpeg and ensure it is on your PATH."
                )
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-decryption_key", hex_key,
            "-i", str(enc_path),
            "-map", "0:a:0",   # extract first audio stream (FLAC/Opus/EAC3 inside MP4)
            "-c", "copy",
            str(out_path),
        ]
        logger.debug(f"Decrypting {enc_path.name} → {out_path.name}")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg decryption failed (exit {result.returncode}): {stderr}")

    def _stream_to_file(self, url: str, out_path: Path, download_id: str) -> int:
        resp = self.session.get(url, stream=True, timeout=60)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        last_report = time.monotonic()
        shutdown_triggered = False

        with out_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if self.shutdown_check and self.shutdown_check():
                    shutdown_triggered = True
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if self._engine and now - last_report >= 0.5:
                    self._engine.update_record(
                        "amazon",
                        download_id,
                        {
                            "transferred": downloaded,
                            "size": total,
                            "progress": downloaded / total if total else 0.0,
                        },
                    )
                    last_report = now

        if shutdown_triggered:
            out_path.unlink(missing_ok=True)
            raise RuntimeError("Shutdown requested mid-download")

        return downloaded

    # ------------------------------------------------------------------
    # DownloadSourcePlugin — status interface
    # ------------------------------------------------------------------

    async def get_all_downloads(self) -> List[DownloadStatus]:
        if self._engine is None:
            return []
        try:
            records = self._engine.get_all_records("amazon")
            return [self._record_to_status(dl_id, rec) for dl_id, rec in records.items()]
        except Exception:
            return []

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        if self._engine is None:
            return None
        try:
            rec = self._engine.get_record("amazon", download_id)
            return self._record_to_status(download_id, rec) if rec is not None else None
        except Exception:
            return None

    async def cancel_download(
        self,
        download_id: str,
        username: Optional[str] = None,
        remove: bool = False,
    ) -> bool:
        if self._engine is None:
            return False
        try:
            return self._engine.cancel_record("amazon", download_id, remove=remove)
        except Exception:
            return False

    async def clear_all_completed_downloads(self) -> bool:
        if self._engine is None:
            return False
        try:
            self._engine.clear_completed("amazon")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        for i in range(1, 100):
            candidate = path.with_name(f"{stem} ({i}){suffix}")
            if not candidate.exists():
                return candidate
        return path.with_name(f"{stem}_{uuid.uuid4().hex[:8]}{suffix}")

    @staticmethod
    def _record_to_status(download_id: str, rec: Dict[str, Any]) -> DownloadStatus:
        return DownloadStatus(
            id=download_id,
            filename=str(rec.get("original_filename", "")),
            username="amazon",
            state=str(rec.get("state", "queued")),
            progress=float(rec.get("progress", 0.0)),
            size=int(rec.get("size", 0)),
            transferred=int(rec.get("transferred", 0)),
            speed=int(rec.get("speed", 0)),
            time_remaining=rec.get("time_remaining"),
            file_path=rec.get("file_path"),
        )
