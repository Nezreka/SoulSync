"""Podcast Download Client — downloads podcast episode audio files to disk.

Handles:
  - Extension detection from enclosure URL and MIME type
  - Collision-safe filenames (appends " (N)" suffix)
  - Streaming download with optional progress callback
  - Cleanup of partial files on failure
  - Minimum file-size guard against truncated/error responses

No authentication required — podcast enclosures are publicly accessible URLs.
The default download_path mirrors the soulseek.download_path config key as a
staging area; callers can override per-download via dest_dir.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, Optional

import requests

from utils.logging_config import get_logger
from core.podcast_client import PodcastEpisode

logger = get_logger("podcast_download")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Read chunk size — 64 KB balances memory use and syscall overhead.
_CHUNK_SIZE = 64 * 1024

# Seconds to wait for the server to start sending response bytes.
_DEFAULT_TIMEOUT = 30

# Illegal filename characters on Windows (superset of POSIX restrictions).
_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# MIME type -> file extension for common podcast formats.
_MIME_TO_EXT: Dict[str, str] = {
    "audio/mpeg":     ".mp3",
    "audio/mp3":      ".mp3",
    "audio/mp4":      ".m4a",
    "audio/x-m4a":    ".m4a",
    "audio/aac":      ".aac",
    "audio/ogg":      ".ogg",
    "audio/vorbis":   ".ogg",
    "audio/opus":     ".opus",
    "audio/wav":      ".wav",
    "audio/x-wav":    ".wav",
    "audio/flac":     ".flac",
    "video/mp4":      ".mp4",
    "video/x-m4v":    ".m4v",
}

# URL path suffixes that unambiguously identify the extension (checked before query string).
_URL_SUFFIXES = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".mp4", ".m4v", ".mov")

# Files smaller than this are treated as error responses and removed.
_MIN_FILE_SIZE = 10 * 1024  # 10 KB


# ---------------------------------------------------------------------------
# Pure helpers — module-level, each independently testable
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Remove characters illegal in Windows/Linux filenames and trim length.

    HTML entities are unescaped first so "&amp;" becomes "&" before
    sanitisation — matching the order used in the original podcast_downloader.py.
    Leading/trailing whitespace and dots are stripped. Empty input returns the
    sentinel "podcast_episode" rather than an empty string, which would cause
    an OS error on file creation.
    """
    import html as _html
    name = _html.unescape(name or "")
    name = _ILLEGAL_CHARS_RE.sub("_", name)
    # Collapse runs of underscores that illegal-char substitution may create.
    name = re.sub(r"_+", "_", name)
    name = name.strip("_. ")
    if not name:
        return "podcast_episode"
    # OS path-length safety: leave room for directory path + counter + extension.
    return name[:180]


def detect_extension(url: str, mime_type: str = "") -> str:
    """Determine the best file extension for an episode enclosure.

    Resolution order:
      1. Explicit file extension in the URL path (before any query string).
      2. MIME type from the enclosure element.
      3. Default to .mp3 — the overwhelmingly dominant podcast audio format.
    """
    url_path = url.split("?")[0].lower()
    for suffix in _URL_SUFFIXES:
        if url_path.endswith(suffix):
            return suffix

    if mime_type:
        # MIME types may carry parameters: "audio/mpeg; codecs=mp3"
        base_mime = mime_type.split(";")[0].strip().lower()
        if base_mime in _MIME_TO_EXT:
            return _MIME_TO_EXT[base_mime]

    return ".mp3"


def _collision_safe_path(filepath: Path) -> Path:
    """Append a " (N)" counter suffix until a non-existing path is found.

    Follows the same collision pattern as the original podcast_downloader.py
    and the Windows Explorer convention.
    """
    if not filepath.exists():
        return filepath
    stem = filepath.stem
    suffix = filepath.suffix
    parent = filepath.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


_COLLAPSIBLE_PODCAST_SEGMENT_RE = re.compile(
    r"^(season|series|episodes?|vol|volume)\s*[.\-\u2013\u2014:]?\s*$", re.IGNORECASE
)


def render_podcast_path_template(
    template: str,
    episode: PodcastEpisode,
    show_title: Optional[str] = None,
    author: Optional[str] = None,
) -> tuple[list[str], str]:
    """Render a podcast path template into (folder_segments, filename_base).

    Supported variables:
        $show / $podcast: Show title
        $author / $artist: Show author/creator
        $title: Episode title
        $season: Zero-padded season (e.g. '01') or empty if omitted
        $seasonnum: Unpadded season (e.g. '1') or empty if omitted
        $episode: Zero-padded episode (e.g. '01') or empty if omitted
        $episodenum: Unpadded episode (e.g. '1') or empty if omitted
        $year: 4-digit release year or empty
        $date: YYYY-MM-DD release date or empty
        $type: Episode type ('full', 'bonus', 'trailer')
    """
    show_val = (show_title or getattr(episode, "show_title", None) or "").strip()
    author_val = (author or getattr(episode, "author", None) or "").strip()
    title_val = (episode.title or "Episode").strip()

    season_raw = getattr(episode, "season", None)
    season_str = f"{season_raw:02d}" if season_raw is not None else ""
    seasonnum_str = str(season_raw) if season_raw is not None else ""

    ep_raw = getattr(episode, "episode_number", None)
    episode_str = f"{ep_raw:02d}" if ep_raw is not None else ""
    episodenum_str = str(ep_raw) if ep_raw is not None else ""

    pub_date = getattr(episode, "pub_date", None)
    year_str = str(pub_date.year) if pub_date else ""
    date_str = pub_date.strftime("%Y-%m-%d") if pub_date else ""

    type_str = (getattr(episode, "episode_type", None) or "full").strip()

    var_map = [
        ("seasonnum", seasonnum_str),
        ("season", season_str),
        ("episodenum", episodenum_str),
        ("episode", episode_str),
        ("podcast", show_val),
        ("show", show_val),
        ("artist", author_val),
        ("author", author_val),
        ("title", title_val),
        ("year", year_str),
        ("date", date_str),
        ("type", type_str),
    ]

    effective_template = template or "$show/Season $season/$title"
    result = effective_template
    for var_name, val in var_map:
        result = result.replace("${" + var_name + "}", val)
        result = result.replace("$" + var_name, val)

    parts = result.replace("\\", "/").split("/")
    folder_parts = parts[:-1]
    filename_part = parts[-1]

    cleaned_folders: list[str] = []
    had_season_var = any(token in effective_template for token in ("$season", "$seasonnum", "${season", "${seasonnum"))
    had_episode_var = any(token in effective_template for token in ("$episode", "$episodenum", "${episode", "${episodenum"))
    for part in folder_parts:
        part = re.sub(r"\s*\[\s*\]", "", part)
        part = re.sub(r"\s*\(\s*\)", "", part)
        part = re.sub(r"\s*\{\s*\}", "", part)
        part = re.sub(r"\s*-\s*$", "", part)
        part = re.sub(r"^\s*-\s*", "", part)
        part = re.sub(r"\s+", " ", part).strip()
        # Collapse dangling season/series/episode segment if variable was empty
        if had_season_var and not seasonnum_str and _COLLAPSIBLE_PODCAST_SEGMENT_RE.match(part):
            continue
        if had_episode_var and not episodenum_str and _COLLAPSIBLE_PODCAST_SEGMENT_RE.match(part):
            continue
        if part:
            sanitized = sanitize_filename(part)
            if sanitized:
                cleaned_folders.append(sanitized)

    filename_part = re.sub(r"\s*\[\s*\]", "", filename_part)
    filename_part = re.sub(r"\s*\(\s*\)", "", filename_part)
    filename_part = re.sub(r"\s*\{\s*\}", "", filename_part)
    filename_part = re.sub(r"\s*-\s*$", "", filename_part)
    filename_part = re.sub(r"^\s*-\s*", "", filename_part)
    filename_part = re.sub(r"\s+", " ", filename_part).strip()

    final_filename = sanitize_filename(filename_part) or sanitize_filename(episode.title) or "Episode"

    return cleaned_folders, final_filename


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class PodcastDownloadClient:
    """Downloads podcast episode enclosures to disk.

    is_configured() always returns True — no credentials are required.
    """

    def __init__(self, download_path: Optional[str] = None) -> None:
        self._explicit_path = download_path is not None
        if download_path is None:
            download_path = self._resolve_default_path()
        self.download_path = Path(download_path)
        try:
            self.download_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not verify download path %s: %s", self.download_path, exc)

        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    @staticmethod
    def _resolve_default_path() -> str:
        try:
            from core.settings import config_manager
            from core.imports.paths import docker_resolve_path
            raw = (
                config_manager.get("podcasts.download_path")
                or config_manager.get("library.podcasts_path")
                or "./podcasts"
            )
            return docker_resolve_path(raw)
        except Exception:
            return "./podcasts"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Podcast downloads require no credentials — always True."""
        return True

    def download_episode(
        self,
        episode: PodcastEpisode,
        dest_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        show_title: Optional[str] = None,
        author: Optional[str] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        """Download an episode enclosure to disk and return the final file path.

        Args:
            episode:           The PodcastEpisode to download.
            dest_dir:          Override destination directory. Defaults to
                               self.download_path.
            progress_callback: Called with (bytes_downloaded, total_bytes) after
                               each chunk. total_bytes is 0 when the server omits
                               Content-Length. Errors in the callback are logged
                               and swallowed — a broken callback must never abort
                               a download.
            show_title:        Optional show title override for templating.
            author:            Optional show author override for templating.
            is_cancelled:      Optional callable returning True if the download was
                               cancelled and should abort immediately.

        Returns:
            Absolute path string of the saved file, or None on failure.
        """
        if dest_dir:
            dest = Path(dest_dir)
        elif not self._explicit_path:
            dest = Path(self._resolve_default_path())
        else:
            dest = self.download_path

        ext = detect_extension(episode.enclosure_url, episode.enclosure_type)

        folders: list[str] = []
        filename = sanitize_filename(episode.title)

        org_enabled = True
        template = "$show/Season $season/$title"
        try:
            from core.settings import config_manager
            if config_manager:
                org_enabled = config_manager.get("file_organization.enabled", True)
                template = (
                    config_manager.get("file_organization.templates.podcast_path")
                    or config_manager.get("podcasts.folder_template")
                    or "$show/Season $season/$title"
                )
        except Exception as cfg_err:
            logger.debug("Failed to load podcast template config, using default: %s", cfg_err)

        if org_enabled and template:
            try:
                folders, filename = render_podcast_path_template(
                    template,
                    episode,
                    show_title=show_title,
                    author=author,
                )
            except Exception as tmpl_exc:
                logger.warning("Error rendering podcast template %r: %s", template, tmpl_exc)
                folders = []
                filename = sanitize_filename(episode.title)

        target_dir = dest
        for f in folders:
            target_dir = target_dir / f

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Cannot create destination directory %s: %s", target_dir, exc)
            return None

        filepath = _collision_safe_path(target_dir / (filename + ext))

        logger.info("Downloading episode %r -> %s", episode.title, filepath)

        def _prune_empty_parents(path: Path):
            p = path.parent
            while p != dest and p.is_relative_to(dest):
                try:
                    p.rmdir()
                    p = p.parent
                except OSError:
                    break

        try:
            resp = self._session.get(
                episode.enclosure_url,
                stream=True,
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Request failed for episode %r: %s", episode.title, exc)
            _prune_empty_parents(filepath)
            return None

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        try:
            with open(filepath, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if is_cancelled and is_cancelled():
                        logger.info("Download cancelled for episode %r", episode.title)
                        raise InterruptedError("Download cancelled")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        try:
                            progress_callback(downloaded, total)
                        except Exception as cb_exc:
                            logger.debug("Progress callback error: %s", cb_exc)
        except InterruptedError:
            if filepath.exists():
                try:
                    filepath.unlink()
                except OSError:
                    pass
            _prune_empty_parents(filepath)
            return None
        except Exception as exc:
            logger.error("Write failed for episode %r: %s", episode.title, exc)
            # Remove the partial file — a half-written file is worse than none.
            if filepath.exists():
                try:
                    filepath.unlink()
                except OSError:
                    pass
            _prune_empty_parents(filepath)
            return None

        final_size = filepath.stat().st_size
        if final_size < _MIN_FILE_SIZE:
            logger.warning(
                "Downloaded file %s is suspiciously small (%d bytes) — removing",
                filepath,
                final_size,
            )
            filepath.unlink(missing_ok=True)
            _prune_empty_parents(filepath)
            return None

        logger.info("Episode saved: %s (%d bytes)", filepath, downloaded)
        return str(filepath)
