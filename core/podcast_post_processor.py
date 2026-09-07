"""Podcast Post-Processor — embeds rich audio metadata & generates media server sidecars.

Handles:
  - In-file audio tag embedding via Mutagen (ID3v2.4 for MP3/WAV, MP4 tags for M4A/AAC,
    Vorbis comments for FLAC/OGG/Opus).
  - High-resolution cover artwork embedding into audio files.
  - Show-level sidecar generation in the show root folder:
      * `cover.jpg` / `poster.jpg` (Plex, Jellyfin, Audiobookshelf, Navidrome)
      * `tvshow.nfo` (Kodi / Jellyfin / Emby / Plex show metadata)
      * `show.info.json` (universal JSON metadata)
  - Episode-level sidecar generation next to the imported audio file:
      * `<name>.nfo` (`<episodedetails>` XML for Plex / Jellyfin / Kodi)
      * `<name>.info.json` (structured JSON metadata matching yt-dlp/SoulSync convention)
      * `<name>.jpg` (Plex Local Media Assets episodic thumbnail)
      * `<name>-thumb.jpg` (Jellyfin / Kodi episodic thumbnail)

Best-effort design: post-processing operations are independently guarded. Tagging or
sidecar generation failures log warnings and NEVER fail or corrupt the audio download.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import requests

from core.podcast_client import PodcastEpisode, PodcastShow
from utils.logging_config import get_logger

logger = get_logger("podcast_post_processor")

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_DEFAULT_SETTINGS = {
    "embed_metadata": True,
    "embed_artwork": True,
    "save_artwork": True,
    "write_nfo": True,
    "write_json": True,
}


# ---------------------------------------------------------------------------
# Pure Helpers
# ---------------------------------------------------------------------------

def strip_html(text: Optional[str]) -> str:
    """Unescape HTML entities, replace paragraph/break tags with newlines, and strip markup."""
    if not text:
        return ""
    unescaped = html.unescape(text)
    # Convert <br>, <p>, and list item tags to newlines
    converted = re.sub(r"<(?:br|p|/p|li)[\s/]*>", "\n", unescaped, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    cleaned = re.sub(r"<[^>]+>", "", converted)
    # Collapse multiple blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def fetch_artwork_bytes(url: Optional[str], timeout: int = 15) -> Optional[Tuple[bytes, str]]:
    """Fetch image bytes from an HTTP(S) URL and determine MIME type.

    Returns:
        (image_bytes, mime_type) tuple on success, or None on failure.
    """
    if not url:
        return None
    url_str = str(url).strip()
    if not (url_str.startswith("http://") or url_str.startswith("https://")):
        return None

    try:
        resp = requests.get(url_str, headers=_REQUEST_HEADERS, timeout=timeout)
        if resp.status_code != 200 or len(resp.content) < 100:
            return None

        content_type = resp.headers.get("content-type", "").lower().split(";")[0].strip()
        if not content_type or "image" not in content_type:
            # Fall back to extension detection
            lowered = url_str.split("?")[0].lower()
            if lowered.endswith(".png"):
                content_type = "image/png"
            elif lowered.endswith(".webp"):
                content_type = "image/webp"
            else:
                content_type = "image/jpeg"

        return resp.content, content_type
    except Exception as exc:
        logger.debug("Failed to fetch artwork from %s: %s", url_str, exc)
        return None


# ---------------------------------------------------------------------------
# XML / NFO Generators
# ---------------------------------------------------------------------------

def build_podcast_episode_nfo(
    episode: PodcastEpisode,
    show_title: Optional[str] = None,
    author: Optional[str] = None,
) -> str:
    """Build a Kodi/Jellyfin/Plex-compatible <episodedetails> XML sidecar string."""
    show_name = show_title or episode.show_title or "Podcast"
    artist = author or episode.author or show_name
    title = episode.title or "Episode"

    aired_str = ""
    year_str = ""
    if episode.pub_date:
        aired_str = episode.pub_date.strftime("%Y-%m-%d")
        year_str = episode.pub_date.strftime("%Y")

    plot_text = strip_html(episode.description or episode.show_notes)

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<episodedetails>",
        f"  <title>{escape(title)}</title>",
        f"  <showtitle>{escape(show_name)}</showtitle>",
    ]

    if episode.season is not None:
        out.append(f"  <season>{episode.season}</season>")
    elif year_str.isdigit():
        out.append(f"  <season>{year_str}</season>")

    if episode.episode_number is not None:
        out.append(f"  <episode>{episode.episode_number}</episode>")
    elif aired_str and len(aired_str) == 10:
        mmdd = aired_str[5:7] + aired_str[8:10]
        if mmdd.isdigit():
            out.append(f"  <episode>{int(mmdd)}</episode>")

    if plot_text:
        out.append(f"  <plot>{escape(plot_text)}</plot>")

    if aired_str:
        out.append(f"  <aired>{escape(aired_str)}</aired>")

    if artist:
        out.append(f"  <studio>{escape(artist)}</studio>")

    if episode.duration_seconds and episode.duration_seconds > 0:
        runtime_mins = max(1, round(episode.duration_seconds / 60))
        out.append(f"  <runtime>{runtime_mins}</runtime>")

    if episode.guid:
        out.append(f'  <uniqueid type="guid" default="true">{escape(episode.guid)}</uniqueid>')

    if episode.enclosure_url:
        out.append(f'  <uniqueid type="enclosure">{escape(episode.enclosure_url)}</uniqueid>')

    out.append("</episodedetails>")
    return "\n".join(out) + "\n"


def build_podcast_show_nfo(show_meta: Dict[str, Any]) -> str:
    """Build a Kodi/Jellyfin/Plex-compatible <tvshow> XML sidecar string for the podcast show."""
    title = show_meta.get("title") or "Podcast"
    plot = strip_html(show_meta.get("description") or "")
    author = show_meta.get("author") or ""
    categories = show_meta.get("categories") or []
    premiered = str(show_meta.get("premiered") or "")[:10]
    feed_url = show_meta.get("feed_url") or ""
    itunes_id = show_meta.get("itunes_id")

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<tvshow>",
        f"  <title>{escape(title)}</title>",
    ]

    if plot:
        out.append(f"  <plot>{escape(plot)}</plot>")
        out.append(f"  <outline>{escape(plot[:250])}</outline>")

    if author:
        out.append(f"  <studio>{escape(author)}</studio>")

    if premiered:
        out.append(f"  <premiered>{escape(premiered)}</premiered>")

    for cat in categories:
        if cat and cat.lower() != "podcasts":
            out.append(f"  <genre>{escape(cat)}</genre>")

    if feed_url:
        out.append(f'  <uniqueid type="feed" default="true">{escape(feed_url)}</uniqueid>')

    if itunes_id:
        out.append(f'  <uniqueid type="itunes">{escape(str(itunes_id))}</uniqueid>')

    out.append("</tvshow>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# JSON Metadata Generators
# ---------------------------------------------------------------------------

def build_podcast_episode_json(
    episode: PodcastEpisode,
    show_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a comprehensive JSON metadata dict for an episode (yt-dlp / Audiobookshelf style)."""
    show_meta = show_meta or {}
    show_name = show_meta.get("title") or episode.show_title or ""
    artist = show_meta.get("author") or episode.author or show_name

    pub_iso = episode.pub_date.isoformat() if episode.pub_date else None
    pub_year = episode.pub_date.strftime("%Y") if episode.pub_date else None

    return {
        "id": episode.guid or episode.enclosure_url,
        "title": episode.title,
        "show": show_name,
        "series": show_name,
        "author": artist,
        "uploader": artist,
        "description": strip_html(episode.description),
        "show_notes": episode.show_notes or episode.description,
        "duration": episode.duration_seconds,
        "season": episode.season,
        "episode_number": episode.episode_number,
        "episode_type": episode.episode_type,
        "pub_date": pub_iso,
        "release_year": pub_year,
        "enclosure_url": episode.enclosure_url,
        "enclosure_type": episode.enclosure_type,
        "enclosure_length": episode.enclosure_length,
        "guid": episode.guid,
        "artwork_url": episode.artwork_url or show_meta.get("artwork_url"),
        "feed_url": show_meta.get("feed_url"),
        "categories": show_meta.get("categories", []),
        "chapter_url": episode.chapter_url,
        "transcript_url": episode.transcript_url,
    }


def build_podcast_show_json(show_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Build a comprehensive JSON metadata dict for a show."""
    return {
        "title": show_meta.get("title", ""),
        "author": show_meta.get("author", ""),
        "description": strip_html(show_meta.get("description", "")),
        "feed_url": show_meta.get("feed_url", ""),
        "itunes_id": show_meta.get("itunes_id"),
        "website": show_meta.get("website", ""),
        "language": show_meta.get("language", "en"),
        "explicit": bool(show_meta.get("explicit", False)),
        "categories": show_meta.get("categories", []),
        "artwork_url": show_meta.get("artwork_url"),
        "episode_count": show_meta.get("episode_count"),
    }


# ---------------------------------------------------------------------------
# In-File Tag Embedding (Mutagen)
# ---------------------------------------------------------------------------

def embed_podcast_tags(
    audio_path: Path,
    episode: PodcastEpisode,
    show_meta: Optional[Dict[str, Any]] = None,
    embed_artwork: bool = True,
    art_bytes: Optional[bytes] = None,
    art_mime: Optional[str] = None,
) -> bool:
    """Embed rich podcast metadata and cover art into the audio file.

    Supports:
      - ID3v2.4 for .mp3 / .wav
      - MP4 atoms for .m4a / .mp4 / .aac
      - Vorbis comments for .flac / .ogg / .opus

    Returns:
        True if tags were written successfully, False otherwise.
    """
    if not audio_path.is_file():
        logger.debug("Audio file not found for tagging: %s", audio_path)
        return False

    show_meta = show_meta or {}
    show_title = show_meta.get("title") or episode.show_title or "Podcast"
    author = show_meta.get("author") or episode.author or show_title
    title = episode.title or "Episode"
    desc = strip_html(episode.description or episode.show_notes)
    categories = show_meta.get("categories") or []
    genre = ", ".join([c for c in categories if c.lower() != "podcasts"]) if categories else "Podcast"

    date_str = ""
    year_int: Optional[int] = None
    if episode.pub_date:
        date_str = episode.pub_date.strftime("%Y-%m-%d")
        try:
            year_int = int(episode.pub_date.strftime("%Y"))
        except ValueError:
            pass

    feed_url = show_meta.get("feed_url") or ""
    guid = episode.guid or episode.enclosure_url or ""

    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(str(audio_path))
        if audio is None:
            logger.debug("Mutagen could not parse audio file: %s", audio_path)
            return False

        if audio.tags is None:
            audio.add_tags()

        from mutagen.id3 import (
            APIC,
            COMM,
            ID3,
            PCST,
            TALB,
            TCON,
            TDRC,
            TGID,
            TIT2,
            TPE1,
            TPE2,
            TPOS,
            TRCK,
            TXXX,
            WFED,
        )
        from mutagen.mp4 import MP4, MP4Cover
        from mutagen.flac import FLAC, Picture

        if isinstance(audio.tags, ID3):
            # --- ID3 Tagging (MP3 / WAV) ---
            tags: ID3 = audio.tags
            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[title]))

            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[show_title]))

            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=[author]))

            # TPE2 = Album Artist (Crucial for Plex/Jellyfin/Navidrome grouping)
            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[show_title]))

            if episode.episode_number is not None:
                tags.delall("TRCK")
                tags.add(TRCK(encoding=3, text=[str(episode.episode_number)]))

            if episode.season is not None:
                tags.delall("TPOS")
                tags.add(TPOS(encoding=3, text=[str(episode.season)]))

            if date_str:
                tags.delall("TDRC")
                tags.add(TDRC(encoding=3, text=[date_str]))

            if genre:
                tags.delall("TCON")
                tags.add(TCON(encoding=3, text=[genre]))

            if desc:
                tags.delall("COMM")
                tags.add(COMM(encoding=3, lang="eng", desc="Description", text=[desc]))

            # Podcast flag
            tags.delall("PCST")
            try:
                tags.add(PCST())
            except Exception:
                pass

            if feed_url:
                tags.delall("WFED")
                tags.add(WFED(url=feed_url))

            if guid:
                tags.delall("TGID")
                tags.add(TGID(encoding=3, text=[guid]))
                tags.delall("TXXX:PODCAST_GUID")
                tags.add(TXXX(encoding=3, desc="PODCAST_GUID", text=[guid]))

            if embed_artwork and art_bytes:
                tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime=art_mime or "image/jpeg",
                        type=3,  # Cover (front)
                        desc="Cover",
                        data=art_bytes,
                    )
                )

        elif isinstance(audio, MP4):
            # --- MP4 / M4A / AAC Tagging ---
            audio["\xa9nam"] = [title]
            audio["\xa9alb"] = [show_title]
            audio["\xa9ART"] = [author]
            audio["aART"] = [show_title]

            if episode.episode_number is not None:
                audio["trkn"] = [(episode.episode_number, 0)]
            if episode.season is not None:
                audio["disk"] = [(episode.season, 0)]
            if date_str:
                audio["\xa9day"] = [date_str]
            if genre:
                audio["\xa9gen"] = [genre]
            if desc:
                audio["\xa9des"] = [desc[:255]]
                audio["ldes"] = [desc]

            # Podcast flag atom
            audio["pcst"] = [1]

            if feed_url:
                audio["purl"] = [feed_url.encode("utf-8") if isinstance(feed_url, str) else feed_url]
            if guid:
                audio["egid"] = [guid.encode("utf-8") if isinstance(guid, str) else guid]

            if embed_artwork and art_bytes:
                img_fmt = MP4Cover.FORMAT_PNG if (art_mime and "png" in art_mime) else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(art_bytes, imageformat=img_fmt)]

        elif hasattr(audio, "tags") and hasattr(audio.tags, "__setitem__"):
            # --- Vorbis Tagging (FLAC / OGG / OPUS) ---
            audio["TITLE"] = [title]
            audio["ALBUM"] = [show_title]
            audio["ARTIST"] = [author]
            audio["ALBUMARTIST"] = [show_title]

            if episode.episode_number is not None:
                audio["TRACKNUMBER"] = [str(episode.episode_number)]
            if episode.season is not None:
                audio["DISCNUMBER"] = [str(episode.season)]
            if date_str:
                audio["DATE"] = [date_str]
            if genre:
                audio["GENRE"] = [genre]
            if desc:
                audio["DESCRIPTION"] = [desc]
                audio["COMMENT"] = [desc]

            audio["PODCAST"] = ["1"]
            if feed_url:
                audio["PODCASTURL"] = [feed_url]
            if guid:
                audio["PODCASTID"] = [guid]

            if embed_artwork and art_bytes:
                try:
                    if isinstance(audio, FLAC):
                        audio.clear_pictures()
                        pic = Picture()
                        pic.data = art_bytes
                        pic.type = 3
                        pic.mime = art_mime or "image/jpeg"
                        pic.desc = "Cover"
                        audio.add_picture(pic)
                except Exception as pic_err:
                    logger.debug("Failed to embed vorbis picture: %s", pic_err)

        # Persist tags atomically via save_audio_file
        from types import SimpleNamespace
        from core.metadata.common import save_audio_file
        save_audio_file(audio, SimpleNamespace(ID3=ID3, FLAC=FLAC, File=MutagenFile))
        logger.debug("Successfully embedded podcast tags into %s", audio_path.name)
        return True

    except Exception as exc:
        logger.warning("Error embedding podcast tags for %s: %s", audio_path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Sidecar Files Management
# ---------------------------------------------------------------------------

def find_show_dir(audio_path: Path, show_title: str, dest_root: Optional[Path] = None) -> Path:
    """Locate the ancestor directory corresponding to the podcast show root.

    Traces parents up towards dest_root to find a directory whose name matches
    the sanitized show_title. If not found or if the template was flat, returns
    the parent folder of the audio file.
    """
    from core.podcast_download_client import sanitize_filename
    want_name = sanitize_filename(show_title).lower()

    curr = audio_path.parent
    root = dest_root.resolve() if dest_root else None

    # Walk up parent hierarchy (up to 5 levels)
    for _ in range(5):
        if curr.name.lower() == want_name:
            return curr
        if root and curr == root:
            break
        if curr.parent == curr:
            break
        curr = curr.parent

    # Fall back to audio file's direct parent or grandparent if in a 'Season XX' folder
    parent = audio_path.parent
    if parent.name.lower().startswith("season") or parent.name.lower().startswith("series"):
        return parent.parent

    return parent


def ensure_podcast_show_assets(
    show_dir: Path,
    show_meta: Dict[str, Any],
    settings: Dict[str, Any],
    art_bytes: Optional[bytes] = None,
) -> List[str]:
    """Seed show-level assets (cover.jpg, tvshow.nfo, show.info.json) in the show folder.

    Idempotent: does not overwrite existing files unless missing.
    Returns list of created filenames.
    """
    created: List[str] = []
    if not show_dir.is_dir():
        try:
            show_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return created

    want_artwork = bool(settings.get("save_artwork", True))
    want_nfo = bool(settings.get("write_nfo", True))
    want_json = bool(settings.get("write_json", True))

    # 1. Show Artwork: cover.jpg and poster.jpg
    if want_artwork:
        cover_path = show_dir / "cover.jpg"
        poster_path = show_dir / "poster.jpg"
        if not cover_path.is_file() or not poster_path.is_file():
            data = art_bytes
            if not data and show_meta.get("artwork_url"):
                fetched = fetch_artwork_bytes(show_meta["artwork_url"])
                if fetched:
                    data = fetched[0]

            if data:
                try:
                    if not cover_path.is_file():
                        cover_path.write_bytes(data)
                        created.append("cover.jpg")
                    if not poster_path.is_file():
                        poster_path.write_bytes(data)
                        created.append("poster.jpg")
                except OSError as exc:
                    logger.debug("Failed writing show artwork in %s: %s", show_dir, exc)

    # 2. tvshow.nfo
    if want_nfo:
        nfo_path = show_dir / "tvshow.nfo"
        if not nfo_path.is_file():
            try:
                xml_content = build_podcast_show_nfo(show_meta)
                nfo_path.write_text(xml_content, encoding="utf-8")
                created.append("tvshow.nfo")
            except OSError as exc:
                logger.debug("Failed writing tvshow.nfo in %s: %s", show_dir, exc)

    # 3. show.info.json
    if want_json:
        json_path = show_dir / "show.info.json"
        if not json_path.is_file():
            try:
                data_dict = build_podcast_show_json(show_meta)
                json_path.write_text(json.dumps(data_dict, indent=2, ensure_ascii=False), encoding="utf-8")
                created.append("show.info.json")
            except OSError as exc:
                logger.debug("Failed writing show.info.json in %s: %s", show_dir, exc)

    return created


def write_podcast_episode_sidecars(
    audio_path: Path,
    episode: PodcastEpisode,
    show_meta: Dict[str, Any],
    settings: Dict[str, Any],
    art_bytes: Optional[bytes] = None,
    art_mime: Optional[str] = None,
) -> List[str]:
    """Write episode sidecar files (<name>.nfo, <name>.info.json, <name>.jpg, <name>-thumb.jpg).

    Returns list of created filenames.
    """
    created: List[str] = []
    parent_dir = audio_path.parent
    stem = audio_path.stem

    want_artwork = bool(settings.get("save_artwork", True))
    want_nfo = bool(settings.get("write_nfo", True))
    want_json = bool(settings.get("write_json", True))

    # 1. Episode NFO (<name>.nfo)
    if want_nfo:
        nfo_path = parent_dir / f"{stem}.nfo"
        try:
            show_title = show_meta.get("title") or episode.show_title
            author = show_meta.get("author") or episode.author
            xml_content = build_podcast_episode_nfo(episode, show_title=show_title, author=author)
            nfo_path.write_text(xml_content, encoding="utf-8")
            created.append(f"{stem}.nfo")
        except OSError as exc:
            logger.debug("Failed writing episode nfo for %s: %s", stem, exc)

    # 2. Episode JSON (<name>.info.json)
    if want_json:
        json_path = parent_dir / f"{stem}.info.json"
        try:
            data_dict = build_podcast_episode_json(episode, show_meta=show_meta)
            json_path.write_text(json.dumps(data_dict, indent=2, ensure_ascii=False), encoding="utf-8")
            created.append(f"{stem}.info.json")
        except OSError as exc:
            logger.debug("Failed writing episode info.json for %s: %s", stem, exc)

    # 3. Dual Episodic Thumbnails (<name>.jpg for Plex, <name>-thumb.jpg for Jellyfin/Kodi)
    if want_artwork:
        data = art_bytes
        ext = ".png" if (art_mime and "png" in art_mime) else ".jpg"
        plex_thumb = parent_dir / f"{stem}{ext}"
        jf_thumb = parent_dir / f"{stem}-thumb{ext}"

        if not plex_thumb.is_file() or not jf_thumb.is_file():
            if not data:
                art_url = episode.artwork_url or show_meta.get("artwork_url")
                if art_url:
                    fetched = fetch_artwork_bytes(art_url)
                    if fetched:
                        data = fetched[0]
                        ext = ".png" if "png" in fetched[1] else ".jpg"
                        plex_thumb = parent_dir / f"{stem}{ext}"
                        jf_thumb = parent_dir / f"{stem}-thumb{ext}"

            if data:
                try:
                    if not plex_thumb.is_file():
                        plex_thumb.write_bytes(data)
                        created.append(plex_thumb.name)
                    if not jf_thumb.is_file():
                        shutil.copy2(plex_thumb, jf_thumb)
                        created.append(jf_thumb.name)
                except OSError as exc:
                    logger.debug("Failed writing episode thumbnails for %s: %s", stem, exc)

    return created


# ---------------------------------------------------------------------------
# Top-Level Coordinator
# ---------------------------------------------------------------------------

def post_process_podcast_episode(
    audio_path: str | Path,
    episode: PodcastEpisode,
    show_meta: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
    dest_root: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Top-level post-processor entry point called immediately after audio download.

    Coordinates:
      1. Configuration resolution
      2. Artwork fetching (cached in memory for tags + sidecars)
      3. Audio tag and artwork embedding via Mutagen
      4. Show-level asset seeding (cover.jpg, tvshow.nfo, show.info.json)
      5. Episode-level sidecar generation (<name>.nfo, <name>.info.json, thumbnails)

    Returns:
      Summary dict with success status, embedded tags, and created sidecars.
    """
    path = Path(audio_path).resolve()
    dest = Path(dest_root).resolve() if dest_root else None

    # Merge configuration with defaults
    cfg = dict(_DEFAULT_SETTINGS)
    if isinstance(settings, dict):
        cfg.update(settings)
    else:
        try:
            from core.settings import config_manager
            if config_manager:
                cfg["embed_metadata"] = config_manager.get("podcasts.embed_metadata", True)
                cfg["embed_artwork"] = config_manager.get("podcasts.embed_artwork", True)
                cfg["save_artwork"] = config_manager.get("podcasts.save_artwork", True)
                cfg["write_nfo"] = config_manager.get("podcasts.write_nfo", True)
                cfg["write_json"] = config_manager.get("podcasts.write_json", True)
        except Exception:
            pass

    meta = dict(show_meta or {})
    if not meta.get("title") and episode.show_title:
        meta["title"] = episode.show_title
    if not meta.get("author") and episode.author:
        meta["author"] = episode.author
    if not meta.get("artwork_url") and episode.artwork_url:
        meta["artwork_url"] = episode.artwork_url

    result = {
        "success": True,
        "audio_path": str(path),
        "tags_embedded": False,
        "show_assets": [],
        "episode_sidecars": [],
    }

    # Fetch artwork once in memory if any feature requests art
    art_bytes: Optional[bytes] = None
    art_mime: Optional[str] = None
    if cfg.get("embed_artwork") or cfg.get("save_artwork"):
        art_url = episode.artwork_url or meta.get("artwork_url")
        if art_url:
            fetched = fetch_artwork_bytes(art_url)
            if fetched:
                art_bytes, art_mime = fetched

    # 1. Embed in-file tags
    if cfg.get("embed_metadata"):
        try:
            result["tags_embedded"] = embed_podcast_tags(
                audio_path=path,
                episode=episode,
                show_meta=meta,
                embed_artwork=bool(cfg.get("embed_artwork")),
                art_bytes=art_bytes,
                art_mime=art_mime,
            )
        except Exception as tag_exc:
            logger.warning("Tag embedding failed for %s: %s", path.name, tag_exc)

    # 2. Show-level assets
    show_dir = find_show_dir(path, meta.get("title") or episode.show_title or "Podcast", dest_root=dest)
    try:
        result["show_assets"] = ensure_podcast_show_assets(
            show_dir=show_dir,
            show_meta=meta,
            settings=cfg,
            art_bytes=art_bytes,
        )
    except Exception as show_exc:
        logger.warning("Show assets generation failed for %s: %s", show_dir, show_exc)

    # 3. Episode-level sidecars
    try:
        result["episode_sidecars"] = write_podcast_episode_sidecars(
            audio_path=path,
            episode=episode,
            show_meta=meta,
            settings=cfg,
            art_bytes=art_bytes,
            art_mime=art_mime,
        )
    except Exception as ep_exc:
        logger.warning("Episode sidecars generation failed for %s: %s", path.name, ep_exc)

    logger.info(
        "Post-processed podcast %s: tags=%s, show_assets=%s, sidecars=%s",
        path.name,
        result["tags_embedded"],
        len(result["show_assets"]),
        len(result["episode_sidecars"]),
    )
    return result
