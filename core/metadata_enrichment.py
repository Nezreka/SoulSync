"""Source-aware metadata enrichment helpers for imported audio files."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

from core.import_context import (
    get_import_clean_album,
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_album,
    get_import_context_artist,
    get_import_original_search,
    get_import_source,
    get_import_source_ids,
    get_import_track_info,
    get_source_tag_names,
    normalize_import_context,
)
from config.settings import config_manager
from core.metadata_service import get_itunes_client
from database.music_database import get_database
from utils.logging_config import get_logger


logger = get_logger("metadata_enrichment")

_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_LOCK = threading.Lock()

_MB_RELEASE_CACHE: Dict[tuple, str] = {}
_MB_RELEASE_CACHE_LOCK = threading.RLock()
_MB_RELEASE_DETAIL_CACHE: Dict[str, Dict[str, Any]] = {}
_MB_RELEASE_DETAIL_CACHE_LOCK = threading.RLock()

_EDITION_PAREN_RE = re.compile(
    r'\s*[\(\[]\s*(?:deluxe|expanded|remaster(?:ed)?|anniversary|special|collector|'
    r'limited|bonus|platinum|gold|super\s*deluxe|standard)'
    r'(?:\s+(?:edition|version))?[^)\]]*[\)\]]',
    re.IGNORECASE,
)
_EDITION_BARE_RE = re.compile(
    r'\s+(?:-\s+)?(?:deluxe|expanded|remaster(?:ed)?|anniversary|special|collector|'
    r'limited|bonus|platinum|gold|super\s*deluxe|standard)'
    r'(?:\s+(?:edition|version))?\s*$',
    re.IGNORECASE,
)


class _NullConfigManager:
    def get(self, _key: str, default: Any = None) -> Any:
        return default


def _get_logger(runtime=None):
    return logger


def _get_config_manager(runtime=None):
    return config_manager


def _get_database(runtime=None):
    try:
        return get_database()
    except Exception:
        return None


def _get_itunes_client(runtime=None):
    try:
        return get_itunes_client()
    except Exception:
        worker = getattr(runtime, "itunes_enrichment_worker", None)
        if worker and getattr(worker, "client", None):
            return worker.client
        return getattr(runtime, "itunes_client", None)


def _extract_artist_name(artist: Any) -> str:
    if isinstance(artist, dict):
        return str(artist.get("name", "") or "")
    if hasattr(artist, "name"):
        return str(getattr(artist, "name") or "")
    return str(artist) if artist else ""


def _get_mutagen_symbols(runtime=None):
    """Lazy mutagen import so tests can monkeypatch this without the package installed."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.apev2 import APEv2, APENoHeaderError
        from mutagen.flac import FLAC, Picture
        from mutagen.id3 import (
            APIC,
            ID3,
            TBPM,
            TCOP,
            TDOR,
            TDRC,
            TCON,
            TIT2,
            TALB,
            TPE1,
            TPE2,
            TPOS,
            TPUB,
            TRCK,
            TSRC,
            TXXX,
            UFID,
            TMED,
        )
        from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
        from mutagen.oggvorbis import OggVorbis
        try:
            from mutagen.oggopus import OggOpus
        except Exception:
            OggOpus = None
    except Exception as exc:
        _get_logger(runtime).debug("Mutagen unavailable for metadata enrichment: %s", exc)
        return None

    return SimpleNamespace(
        File=MutagenFile,
        APEv2=APEv2,
        APENoHeaderError=APENoHeaderError,
        FLAC=FLAC,
        Picture=Picture,
        ID3=ID3,
        APIC=APIC,
        TBPM=TBPM,
        TCOP=TCOP,
        TDOR=TDOR,
        TDRC=TDRC,
        TCON=TCON,
        TIT2=TIT2,
        TALB=TALB,
        TPE1=TPE1,
        TPE2=TPE2,
        TPOS=TPOS,
        TPUB=TPUB,
        TRCK=TRCK,
        TSRC=TSRC,
        TXXX=TXXX,
        UFID=UFID,
        TMED=TMED,
        MP4=MP4,
        MP4Cover=MP4Cover,
        MP4FreeForm=MP4FreeForm,
        OggVorbis=OggVorbis,
        OggOpus=OggOpus,
    )


def _get_file_lock(file_path: str) -> threading.Lock:
    with _FILE_LOCKS_LOCK:
        lock = _FILE_LOCKS.get(file_path)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[file_path] = lock
        return lock


def _is_ogg_opus(audio_file: Any) -> bool:
    return type(audio_file).__name__ == "OggOpus"


def _is_vorbis_like(audio_file: Any, symbols: Any) -> bool:
    vorbis_classes = tuple(
        cls for cls in (
            getattr(symbols, "FLAC", None),
            getattr(symbols, "OggVorbis", None),
        ) if cls is not None
    )
    return bool(vorbis_classes) and isinstance(audio_file, vorbis_classes) or _is_ogg_opus(audio_file)


def _save_audio_file(audio_file: Any, symbols: Any) -> None:
    if isinstance(audio_file.tags, symbols.ID3):
        audio_file.save(v1=0, v2_version=4)
    elif isinstance(audio_file, symbols.FLAC):
        audio_file.save(deleteid3=True)
    else:
        audio_file.save()


def _strip_all_non_audio_tags(file_path: str, runtime=None) -> dict:
    summary = {"apev2_stripped": False, "apev2_tag_count": 0}
    if os.path.splitext(file_path)[1].lower() != ".mp3":
        return summary

    symbols = _get_mutagen_symbols(runtime)
    if not symbols:
        return summary

    try:
        apev2_tags = symbols.APEv2(file_path)
        tag_count = len(apev2_tags)
        tag_keys = list(apev2_tags.keys())
        apev2_tags.delete(file_path)
        summary["apev2_stripped"] = True
        summary["apev2_tag_count"] = tag_count
        _get_logger(runtime).info("Stripped %s APEv2 tags: %s", tag_count, ", ".join(tag_keys[:10]))
    except symbols.APENoHeaderError:
        pass
    except Exception as exc:
        _get_logger(runtime).error("Could not strip APEv2 tags (non-fatal): %s", exc)
    return summary


def _verify_metadata_written(file_path: str, runtime=None) -> bool:
    symbols = _get_mutagen_symbols(runtime)
    if not symbols:
        return False

    try:
        check = symbols.File(file_path)
        if check is None or check.tags is None:
            _get_logger(runtime).info("[VERIFY] Tags are None after save: %s", file_path)
            return False

        title_found = False
        artist_found = False
        if isinstance(check.tags, symbols.ID3):
            title_found = bool(check.tags.getall("TIT2"))
            artist_found = bool(check.tags.getall("TPE1"))
            try:
                symbols.APEv2(file_path)
                _get_logger(runtime).info("[VERIFY] APEv2 tags still present after processing!")
                return False
            except symbols.APENoHeaderError:
                pass
        elif _is_vorbis_like(check, symbols):
            title_found = bool(check.get("title"))
            artist_found = bool(check.get("artist"))
        elif isinstance(check, symbols.MP4):
            title_found = bool(check.get("\xa9nam"))
            artist_found = bool(check.get("\xa9ART"))

        if not title_found or not artist_found:
            _get_logger(runtime).warning("[VERIFY] Missing metadata - title:%s artist:%s", title_found, artist_found)
            return False

        _get_logger(runtime).info("[VERIFY] Metadata verified OK")
        return True
    except Exception as exc:
        _get_logger(runtime).error("[VERIFY] Verification error (non-fatal): %s", exc)
        return False


def _get_image_dimensions(data: bytes):
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            import struct

            w, h = struct.unpack(">II", data[16:24])
            return w, h
        if data[:2] == b"\xff\xd8":
            import struct

            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return w, h
                length = struct.unpack(">H", data[i + 2 : i + 4])[0]
                i += 2 + length
    except Exception:
        pass
    return None, None


def extract_source_metadata(context: dict, artist: dict, album_info: dict, runtime=None) -> dict:
    if album_info is None:
        album_info = {}

    cfg = _get_config_manager(runtime)
    context = normalize_import_context(context)
    original_search = get_import_original_search(context)
    album_ctx = get_import_context_album(context)
    track_info = get_import_track_info(context)
    source = get_import_source(context)
    source_ids = get_import_source_ids(context)

    artist_dict = artist if isinstance(artist, dict) else {
        "name": _extract_artist_name(artist),
        "id": getattr(artist, "id", ""),
        "genres": list(getattr(artist, "genres", []) or []),
    }

    metadata: Dict[str, Any] = {
        "source": source,
        "source_track_id": source_ids["track_id"],
        "source_artist_id": source_ids["artist_id"],
        "source_album_id": source_ids["album_id"],
    }

    metadata["title"] = get_import_clean_title(context, album_info=album_info, default=original_search.get("title", ""))
    if original_search.get("clean_title"):
        _get_logger(runtime).info("Metadata: Using clean title: '%s'", metadata["title"])
    elif album_info.get("clean_track_name"):
        _get_logger(runtime).info("Metadata: Using album info clean name: '%s'", metadata["title"])
    else:
        _get_logger(runtime).warning("Metadata: Using original title as fallback: '%s'", metadata["title"])

    artists = original_search.get("artists")
    if isinstance(artists, list) and artists:
        all_artists = []
        for artist_item in artists:
            if isinstance(artist_item, dict) and artist_item.get("name"):
                all_artists.append(artist_item["name"])
            elif isinstance(artist_item, str):
                all_artists.append(artist_item)
            else:
                all_artists.append(str(artist_item))
        metadata["artist"] = ", ".join(all_artists)
        _get_logger(runtime).info("Metadata: Using all artists: '%s'", metadata["artist"])
    else:
        metadata["artist"] = artist_dict.get("name", "") or get_import_clean_artist(context)
        _get_logger(runtime).info("Metadata: Using primary artist: '%s'", metadata["artist"])

    raw_album_artist = artist_dict.get("name", "") or metadata["artist"]
    track_info_ctx = track_info or {}
    explicit_artist = track_info_ctx.get("_explicit_artist_context") if isinstance(track_info_ctx, dict) else None
    album_artists_for_collab = None

    if isinstance(explicit_artist, dict) and explicit_artist.get("name"):
        raw_album_artist = explicit_artist["name"]
        album_artists_for_collab = [explicit_artist]
    elif isinstance(explicit_artist, str) and explicit_artist:
        raw_album_artist = explicit_artist
        album_artists_for_collab = [{"name": explicit_artist}]
    elif album_ctx and isinstance(album_ctx, dict):
        album_artists = album_ctx.get("artists", [])
        if album_artists:
            first_album_artist = album_artists[0]
            if isinstance(first_album_artist, dict) and first_album_artist.get("name"):
                raw_album_artist = first_album_artist["name"]
            elif isinstance(first_album_artist, str) and first_album_artist:
                raw_album_artist = first_album_artist
            album_artists_for_collab = album_artists

    collab_mode = cfg.get("file_organization.collab_artist_mode", "first")
    if collab_mode == "first" and raw_album_artist:
        context_artists = album_artists_for_collab or original_search.get("artists") or track_info_ctx.get("artists") or []
        if len(context_artists) > 1:
            first = context_artists[0]
            raw_album_artist = first.get("name", first) if isinstance(first, dict) else str(first)
        elif len(context_artists) == 1 and ("," in raw_album_artist or " & " in raw_album_artist):
            artist_id = str(artist_dict.get("id", ""))
            if source == "itunes" and artist_id.isdigit():
                try:
                    itunes_client = _get_itunes_client(runtime)
                    if itunes_client and hasattr(itunes_client, "resolve_primary_artist"):
                        resolved = itunes_client.resolve_primary_artist(artist_id)
                        if resolved and resolved != raw_album_artist:
                            raw_album_artist = resolved
                except Exception:
                    pass
    metadata["album_artist"] = raw_album_artist

    if album_info.get("is_album"):
        metadata["album"] = album_info.get("album_name", "Unknown Album")
        metadata["track_number"] = album_info.get("track_number", 1)
        metadata["total_tracks"] = album_ctx.get("total_tracks", 1) if album_ctx else 1
        _get_logger(runtime).info("[METADATA] Album track - track_number: %s, album: %s", metadata["track_number"], metadata["album"])
    else:
        if album_ctx and album_ctx.get("name"):
            _get_logger(runtime).info("[SAFEGUARD] Using album context name instead of track title for album metadata")
            metadata["album"] = album_ctx["name"]
            metadata["track_number"] = album_info.get("track_number", 1) if album_info else 1
            metadata["total_tracks"] = album_ctx.get("total_tracks", 1)
        else:
            metadata["album"] = metadata["title"]
            metadata["track_number"] = 1
            metadata["total_tracks"] = 1

    disc_num = original_search.get("disc_number")
    if disc_num is None and album_info:
        disc_num = album_info.get("disc_number")
    metadata["disc_number"] = disc_num if disc_num is not None else 1

    if album_ctx and album_ctx.get("release_date"):
        metadata["date"] = album_ctx["release_date"][:4]

    genres = artist_dict.get("genres") or []
    if genres:
        from core.genre_filter import filter_genres

        filtered = filter_genres(list(genres[:2]), cfg)
        if filtered:
            metadata["genre"] = ", ".join(filtered)

    metadata["album_art_url"] = album_info.get("album_image_url") if album_info else None
    if not metadata["album_art_url"] and album_ctx:
        album_image = album_ctx.get("image_url")
        if not album_image and album_ctx.get("images"):
            first_image = album_ctx["images"][0]
            album_image = first_image.get("url") if isinstance(first_image, dict) else None
        metadata["album_art_url"] = album_image

    _get_logger(runtime).info(
        "[Metadata Summary] title='%s' | artist='%s' | album_artist='%s' | album='%s' | track=%s/%s | disc=%s",
        metadata.get("title"),
        metadata.get("artist"),
        metadata.get("album_artist"),
        metadata.get("album"),
        metadata.get("track_number"),
        metadata.get("total_tracks"),
        metadata.get("disc_number"),
    )

    return metadata


def embed_album_art_metadata(audio_file, metadata: dict, runtime=None):
    cfg = _get_config_manager(runtime)
    logger_ = _get_logger(runtime)
    symbols = _get_mutagen_symbols(runtime)
    if not symbols:
        return

    try:
        image_data = None
        mime_type = None

        release_mbid = metadata.get("musicbrainz_release_id")
        if release_mbid and cfg.get("metadata_enhancement.prefer_caa_art", False):
            try:
                caa_url = f"https://coverartarchive.org/release/{release_mbid}/front"
                req = urllib.request.Request(caa_url, headers={"Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    image_data = response.read()
                    mime_type = response.info().get_content_type() or "image/jpeg"
                if not image_data or len(image_data) <= 1000:
                    image_data = None
            except Exception:
                image_data = None

        if not image_data:
            art_url = metadata.get("album_art_url")
            if not art_url:
                logger_.warning("No album art URL available for embedding.")
                return
            with urllib.request.urlopen(art_url, timeout=10) as response:
                image_data = response.read()
                mime_type = response.info().get_content_type() or "image/jpeg"

        if not image_data:
            logger_.error("Failed to download album art data.")
            return

        if isinstance(audio_file.tags, symbols.ID3):
            audio_file.tags.add(symbols.APIC(encoding=3, mime=mime_type, type=3, desc="Cover", data=image_data))
        elif isinstance(audio_file, symbols.FLAC):
            picture = symbols.Picture()
            picture.data = image_data
            picture.type = 3
            picture.mime = mime_type
            width, height = _get_image_dimensions(image_data)
            picture.width = width or 640
            picture.height = height or 640
            picture.depth = 24
            audio_file.add_picture(picture)
        elif isinstance(audio_file, symbols.MP4):
            fmt = symbols.MP4Cover.FORMAT_JPEG if "jpeg" in mime_type else symbols.MP4Cover.FORMAT_PNG
            audio_file["covr"] = [symbols.MP4Cover(image_data, imageformat=fmt)]

        logger_.info("Album art successfully embedded.")
    except Exception as exc:
        logger_.error("Error embedding album art: %s", exc)


def embed_source_ids(audio_file, metadata: dict, context: dict = None, runtime=None):
    cfg = _get_config_manager(runtime)
    logger_ = _get_logger(runtime)
    symbols = _get_mutagen_symbols(runtime)
    if not symbols:
        return

    try:
        tag_config = {
            "SPOTIFY_TRACK_ID": "spotify.tags.track_id",
            "SPOTIFY_ARTIST_ID": "spotify.tags.artist_id",
            "SPOTIFY_ALBUM_ID": "spotify.tags.album_id",
            "ITUNES_TRACK_ID": "itunes.tags.track_id",
            "ITUNES_ARTIST_ID": "itunes.tags.artist_id",
            "ITUNES_ALBUM_ID": "itunes.tags.album_id",
            "MUSICBRAINZ_RECORDING_ID": "musicbrainz.tags.recording_id",
            "MUSICBRAINZ_ARTIST_ID": "musicbrainz.tags.artist_id",
            "MUSICBRAINZ_RELEASE_ID": "musicbrainz.tags.release_id",
            "MUSICBRAINZ_RELEASEGROUPID": "musicbrainz.tags.release_group_id",
            "MUSICBRAINZ_ALBUMARTISTID": "musicbrainz.tags.album_artist_id",
            "MUSICBRAINZ_RELEASETRACKID": "musicbrainz.tags.release_track_id",
            "RELEASETYPE": "musicbrainz.tags.release_type",
            "ORIGINALDATE": "musicbrainz.tags.original_date",
            "RELEASESTATUS": "musicbrainz.tags.release_status",
            "RELEASECOUNTRY": "musicbrainz.tags.release_country",
            "BARCODE": "musicbrainz.tags.barcode",
            "MEDIA": "musicbrainz.tags.media",
            "TOTALDISCS": "musicbrainz.tags.total_discs",
            "CATALOGNUMBER": "musicbrainz.tags.catalog_number",
            "SCRIPT": "musicbrainz.tags.script",
            "ASIN": "musicbrainz.tags.asin",
            "DEEZER_TRACK_ID": "deezer.tags.track_id",
            "DEEZER_ARTIST_ID": "deezer.tags.artist_id",
            "AUDIODB_TRACK_ID": "audiodb.tags.track_id",
            "TIDAL_TRACK_ID": "tidal.tags.track_id",
            "TIDAL_ARTIST_ID": "tidal.tags.artist_id",
            "QOBUZ_TRACK_ID": "qobuz.tags.track_id",
            "QOBUZ_ARTIST_ID": "qobuz.tags.artist_id",
            "GENIUS_TRACK_ID": "genius.tags.track_id",
        }

        def _tag_enabled(path: str) -> bool:
            return cfg.get(path, True) is not False

        def _names_match(a: str, b: str, threshold: float = 0.75) -> bool:
            if not a or not b:
                return False
            from difflib import SequenceMatcher

            norm = lambda s: re.sub(r"[^a-z0-9 ]", "", re.sub(r"\(.*?\)", "", s).lower()).strip()
            return SequenceMatcher(None, norm(a), norm(b)).ratio() >= threshold

        context = normalize_import_context(context)
        source = (metadata.get("source") or "").strip().lower()
        source_ids = {}
        if source:
            source_tag_names = get_source_tag_names(source)
            source_track_id = metadata.get("source_track_id")
            source_artist_id = metadata.get("source_artist_id")
            source_album_id = metadata.get("source_album_id")
            if cfg.get(f"{source}.embed_tags", True) is not False:
                if source_tag_names.get("track") and source_track_id:
                    source_ids[source_tag_names["track"]] = source_track_id
                if source_tag_names.get("artist") and source_artist_id:
                    source_ids[source_tag_names["artist"]] = source_artist_id
                if source_tag_names.get("album") and source_album_id:
                    source_ids[source_tag_names["album"]] = source_album_id

        if not source_ids:
            if cfg.get("spotify.embed_tags", True) is not False:
                if metadata.get("spotify_track_id"):
                    source_ids["SPOTIFY_TRACK_ID"] = metadata["spotify_track_id"]
                if metadata.get("spotify_artist_id"):
                    source_ids["SPOTIFY_ARTIST_ID"] = metadata["spotify_artist_id"]
                if metadata.get("spotify_album_id"):
                    source_ids["SPOTIFY_ALBUM_ID"] = metadata["spotify_album_id"]
            if cfg.get("itunes.embed_tags", True) is not False:
                if metadata.get("itunes_track_id"):
                    source_ids["ITUNES_TRACK_ID"] = metadata["itunes_track_id"]
                if metadata.get("itunes_artist_id"):
                    source_ids["ITUNES_ARTIST_ID"] = metadata["itunes_artist_id"]
                if metadata.get("itunes_album_id"):
                    source_ids["ITUNES_ALBUM_ID"] = metadata["itunes_album_id"]

        track_title = metadata.get("title", "")
        artist_name = metadata.get("album_artist", "") or metadata.get("artist", "")
        track_info = get_import_track_info(context)
        explicit_artist = (track_info or {}).get("_explicit_artist_context") if isinstance(track_info, dict) else None
        batch_artist_name = None
        if isinstance(explicit_artist, dict) and explicit_artist.get("name"):
            batch_artist_name = explicit_artist["name"]
        elif isinstance(explicit_artist, str) and explicit_artist:
            batch_artist_name = explicit_artist

        pp = {
            "id_tags": source_ids,
            "track_title": track_title,
            "artist_name": artist_name,
            "batch_artist_name": batch_artist_name,
            "metadata": metadata,
            "recording_mbid": None,
            "artist_mbid": None,
            "release_mbid": "",
            "mb_genres": [],
            "isrc": None,
            "deezer_bpm": None,
            "deezer_isrc": None,
            "audiodb_mood": None,
            "audiodb_style": None,
            "audiodb_genre": None,
            "tidal_isrc": None,
            "tidal_copyright": None,
            "qobuz_isrc": None,
            "qobuz_copyright": None,
            "qobuz_label": None,
            "lastfm_tags": [],
            "lastfm_url": None,
            "genius_url": None,
            "release_year": None,
        }

        source_order = cfg.get("metadata_enhancement.post_process_order", None)
        if not isinstance(source_order, list) or not source_order:
            source_order = ["musicbrainz", "deezer", "audiodb", "tidal", "qobuz", "lastfm", "genius"]

        db = _get_database(runtime)

        for source_name in source_order:
            if source_name == "musicbrainz":
                if cfg.get("musicbrainz.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                mb_worker = getattr(runtime, "mb_worker", None)
                mb_service = mb_worker.mb_service if mb_worker else None
                if not mb_service:
                    continue
                try:
                    result = mb_service.match_recording(track_title, artist_name)
                    if result and result.get("mbid"):
                        pp["recording_mbid"] = result["mbid"]
                        pp["id_tags"]["MUSICBRAINZ_RECORDING_ID"] = pp["recording_mbid"]
                        details = mb_service.mb_client.get_recording(pp["recording_mbid"], includes=["isrcs", "genres"])
                        if details:
                            isrcs = details.get("isrcs", [])
                            if isrcs:
                                pp["isrc"] = isrcs[0]
                            pp["mb_genres"] = [g["name"] for g in sorted(details.get("genres", []), key=lambda x: x.get("count", 0), reverse=True)]

                    track_artist_name = metadata.get("artist", "") or artist_name
                    if ", " in track_artist_name:
                        track_artist_name = track_artist_name.split(", ")[0]
                    artist_result = mb_service.match_artist(track_artist_name)
                    if artist_result and artist_result.get("mbid"):
                        pp["artist_mbid"] = artist_result["mbid"]
                        pp["id_tags"]["MUSICBRAINZ_ARTIST_ID"] = pp["artist_mbid"]

                    album_name_for_mb = metadata.get("album", "")
                    if album_name_for_mb:
                        artist_key = (pp.get("batch_artist_name") or artist_name).lower().strip()
                        rc_key_norm = (_normalize_album_cache_key(album_name_for_mb), artist_key)
                        rc_key_exact = (album_name_for_mb.lower().strip(), artist_key)
                        with _MB_RELEASE_CACHE_LOCK:
                            cached = _MB_RELEASE_CACHE.get(rc_key_norm)
                            if cached is None:
                                cached = _MB_RELEASE_CACHE.get(rc_key_exact)
                            if cached is not None:
                                pp["release_mbid"] = cached
                            else:
                                try:
                                    rc_result = mb_service.match_release(album_name_for_mb, artist_name)
                                    pp["release_mbid"] = rc_result.get("mbid", "") if rc_result else ""
                                except Exception:
                                    pp["release_mbid"] = ""
                                _MB_RELEASE_CACHE[rc_key_norm] = pp["release_mbid"]
                                _MB_RELEASE_CACHE[rc_key_exact] = pp["release_mbid"]
                        if pp["release_mbid"]:
                            pp["id_tags"]["MUSICBRAINZ_RELEASE_ID"] = pp["release_mbid"]

                    if pp["release_mbid"]:
                        with _MB_RELEASE_DETAIL_CACHE_LOCK:
                            release_detail = _MB_RELEASE_DETAIL_CACHE.get(pp["release_mbid"])
                        if release_detail is None:
                            release_detail = mb_service.mb_client.get_release(
                                pp["release_mbid"],
                                includes=["release-groups", "labels", "media", "artist-credits", "recordings"],
                            ) or {}
                            with _MB_RELEASE_DETAIL_CACHE_LOCK:
                                _MB_RELEASE_DETAIL_CACHE[pp["release_mbid"]] = release_detail
                        if release_detail:
                            rg = release_detail.get("release-group", {})
                            if rg.get("id"):
                                pp["id_tags"]["MUSICBRAINZ_RELEASEGROUPID"] = rg["id"]
                            ac = release_detail.get("artist-credit", [])
                            if ac and isinstance(ac[0], dict):
                                aa = ac[0].get("artist", {})
                                if aa.get("id"):
                                    pp["id_tags"]["MUSICBRAINZ_ALBUMARTISTID"] = aa["id"]
                            if rg.get("primary-type"):
                                pp["id_tags"]["RELEASETYPE"] = rg["primary-type"]
                            if rg.get("first-release-date"):
                                pp["id_tags"]["ORIGINALDATE"] = rg["first-release-date"]
                                if not pp["release_year"] and len(rg["first-release-date"]) >= 4:
                                    year = rg["first-release-date"][:4]
                                    if year.isdigit():
                                        pp["release_year"] = year
                            if release_detail.get("status"):
                                pp["id_tags"]["RELEASESTATUS"] = release_detail["status"]
                            if release_detail.get("country"):
                                pp["id_tags"]["RELEASECOUNTRY"] = release_detail["country"]
                            if release_detail.get("barcode"):
                                pp["id_tags"]["BARCODE"] = release_detail["barcode"]
                            media_list = release_detail.get("media", [])
                            if media_list:
                                fmt = media_list[0].get("format", "")
                                if fmt:
                                    pp["id_tags"]["MEDIA"] = fmt
                                pp["id_tags"]["TOTALDISCS"] = str(len(media_list))
                            label_info = release_detail.get("label-info", [])
                            if label_info and isinstance(label_info[0], dict):
                                cat = label_info[0].get("catalog-number", "")
                                if cat:
                                    pp["id_tags"]["CATALOGNUMBER"] = cat
                            text_rep = release_detail.get("text-representation", {})
                            if isinstance(text_rep, dict) and text_rep.get("script"):
                                pp["id_tags"]["SCRIPT"] = text_rep["script"]
                            if release_detail.get("asin"):
                                pp["id_tags"]["ASIN"] = release_detail["asin"]
                            track_num = metadata.get("track_number")
                            disc_num = metadata.get("disc_number") or 1
                            if track_num and media_list:
                                try:
                                    track_num_int = int(track_num)
                                    disc_num_int = int(disc_num)
                                    for medium in media_list:
                                        if medium.get("position", 1) == disc_num_int:
                                            for mtrack in (medium.get("tracks") or medium.get("track-list", [])):
                                                if mtrack.get("position") == track_num_int:
                                                    if mtrack.get("id"):
                                                        pp["id_tags"]["MUSICBRAINZ_RELEASETRACKID"] = mtrack["id"]
                                                    release_recording = mtrack.get("recording", {})
                                                    if release_recording.get("id"):
                                                        pp["recording_mbid"] = release_recording["id"]
                                                        pp["id_tags"]["MUSICBRAINZ_RECORDING_ID"] = release_recording["id"]
                                                    break
                                            break
                                except (ValueError, TypeError):
                                    pass
                except Exception as exc:
                    logger_.error("MusicBrainz lookup failed (non-fatal): %s", exc)
                continue

            if source_name == "deezer":
                if cfg.get("deezer.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                try:
                    deezer_worker = getattr(runtime, "deezer_worker", None)
                    dz_client = deezer_worker.client if deezer_worker else None
                    if not dz_client:
                        continue
                    dz_result = dz_client.search_track(artist_name, track_title)
                    if dz_result and _names_match(dz_result.get("title", ""), track_title) and _names_match(dz_result.get("artist", {}).get("name", ""), artist_name):
                        dz_track_id = dz_result["id"]
                        pp["id_tags"]["DEEZER_TRACK_ID"] = str(dz_track_id)
                        dz_artist_id = dz_result.get("artist", {}).get("id")
                        if dz_artist_id:
                            pp["id_tags"]["DEEZER_ARTIST_ID"] = str(dz_artist_id)
                        dz_details = dz_client.get_track_details(dz_track_id)
                        if dz_details:
                            bpm_val = dz_details.get("bpm")
                            if bpm_val and bpm_val > 0:
                                pp["deezer_bpm"] = bpm_val
                            dz_isrc = dz_details.get("isrc")
                            if dz_isrc:
                                pp["deezer_isrc"] = dz_isrc
                        if not pp["release_year"]:
                            dz_album = dz_result.get("album", {})
                            dz_release = (dz_album.get("release_date", "") if isinstance(dz_album, dict) else "") or ""
                            if len(dz_release) >= 4 and dz_release[:4].isdigit():
                                pp["release_year"] = dz_release[:4]
                except Exception as exc:
                    logger_.error("Deezer lookup failed (non-fatal): %s", exc)
                continue

            if source_name == "audiodb":
                if cfg.get("audiodb.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                try:
                    audiodb_worker = getattr(runtime, "audiodb_worker", None)
                    adb_client = audiodb_worker.client if audiodb_worker else None
                    if not adb_client:
                        continue
                    adb_result = adb_client.search_track(artist_name, track_title)
                    if adb_result and _names_match(adb_result.get("strTrack", ""), track_title) and _names_match(adb_result.get("strArtist", ""), artist_name):
                        adb_track_id = adb_result.get("idTrack")
                        if adb_track_id:
                            pp["id_tags"]["AUDIODB_TRACK_ID"] = str(adb_track_id)
                        adb_mb_track = adb_result.get("strMusicBrainzID")
                        if adb_mb_track and "MUSICBRAINZ_RECORDING_ID" not in pp["id_tags"]:
                            pp["id_tags"]["MUSICBRAINZ_RECORDING_ID"] = adb_mb_track
                            pp["recording_mbid"] = adb_mb_track
                        adb_mb_artist = adb_result.get("strMusicBrainzArtistID")
                        if adb_mb_artist and "MUSICBRAINZ_ARTIST_ID" not in pp["id_tags"]:
                            pp["id_tags"]["MUSICBRAINZ_ARTIST_ID"] = adb_mb_artist
                            pp["artist_mbid"] = adb_mb_artist
                        pp["audiodb_mood"] = adb_result.get("strMood") or None
                        pp["audiodb_style"] = adb_result.get("strStyle") or None
                        pp["audiodb_genre"] = adb_result.get("strGenre") or None
                except Exception as exc:
                    logger_.error("AudioDB lookup failed (non-fatal): %s", exc)
                continue

            if source_name == "tidal":
                if cfg.get("tidal.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                try:
                    tidal_client = getattr(runtime, "tidal_client", None)
                    if not (tidal_client and tidal_client.is_authenticated()):
                        continue
                    td_result = tidal_client.search_track(artist_name, track_title)
                    if td_result and _names_match(td_result.get("title", ""), track_title):
                        td_track_id = td_result.get("id")
                        if td_track_id:
                            pp["id_tags"]["TIDAL_TRACK_ID"] = str(td_track_id)
                        td_artist = td_result.get("artist", {})
                        if isinstance(td_artist, dict) and td_artist.get("id"):
                            pp["id_tags"]["TIDAL_ARTIST_ID"] = str(td_artist["id"])
                        if td_track_id:
                            td_details = tidal_client.get_track(str(td_track_id))
                            if td_details:
                                pp["tidal_isrc"] = td_details.get("isrc")
                                td_copyright = td_details.get("copyright")
                                if isinstance(td_copyright, dict):
                                    td_copyright = td_copyright.get("text", td_copyright.get("name", ""))
                                pp["tidal_copyright"] = td_copyright or None
                        if not pp["release_year"]:
                            td_album = td_result.get("album", {})
                            td_release = ""
                            if isinstance(td_album, dict):
                                td_release = str(td_album.get("release_date", "") or td_album.get("releaseDate", "") or "")
                            if len(td_release) >= 4 and td_release[:4].isdigit():
                                pp["release_year"] = td_release[:4]
                except Exception as exc:
                    logger_.error("Tidal lookup failed (non-fatal): %s", exc)
                continue

            if source_name == "qobuz":
                if cfg.get("qobuz.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                try:
                    qobuz_worker = getattr(runtime, "qobuz_enrichment_worker", None)
                    qz_client = qobuz_worker.client if qobuz_worker else None
                    if not (qz_client and qz_client.is_authenticated()):
                        continue
                    qz_result = qz_client.search_track(artist_name, track_title)
                    if qz_result:
                        qz_performer = qz_result.get("performer") or {}
                        if not isinstance(qz_performer, dict):
                            qz_performer = {}
                        qz_artist_name = qz_performer.get("name", "")
                        if _names_match(qz_result.get("title", ""), track_title) and _names_match(qz_artist_name, artist_name):
                            qz_track_id = qz_result.get("id")
                            if qz_track_id:
                                pp["id_tags"]["QOBUZ_TRACK_ID"] = str(qz_track_id)
                            if qz_performer.get("id"):
                                pp["id_tags"]["QOBUZ_ARTIST_ID"] = str(qz_performer["id"])
                            qz_isrc = qz_result.get("isrc")
                            if isinstance(qz_isrc, dict):
                                qz_isrc = qz_isrc.get("value", qz_isrc.get("id", ""))
                            if qz_isrc:
                                pp["qobuz_isrc"] = qz_isrc
                            qz_copyright = qz_result.get("copyright")
                            if isinstance(qz_copyright, dict):
                                qz_copyright = qz_copyright.get("text", qz_copyright.get("name", ""))
                            if isinstance(qz_copyright, str):
                                pp["qobuz_copyright"] = qz_copyright
                            qz_album = qz_result.get("album", {})
                            if isinstance(qz_album, dict):
                                qz_label_info = qz_album.get("label", {})
                                if isinstance(qz_label_info, dict) and qz_label_info.get("name"):
                                    pp["qobuz_label"] = qz_label_info["name"]
                                if not pp["release_year"]:
                                    qz_release = str(qz_album.get("release_date_original", "") or "")
                                    if not qz_release:
                                        qz_ts = qz_album.get("released_at")
                                        if qz_ts and isinstance(qz_ts, (int, float)) and qz_ts > 0:
                                            import datetime as _dt
                                            qz_release = str(_dt.datetime.utcfromtimestamp(qz_ts).year)
                                    if len(qz_release) >= 4 and qz_release[:4].isdigit():
                                        pp["release_year"] = qz_release[:4]
                except Exception as exc:
                    logger_.error("Qobuz lookup failed (non-fatal): %s", exc)
                continue

            if source_name == "lastfm":
                if cfg.get("lastfm.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                try:
                    lastfm_worker = getattr(runtime, "lastfm_worker", None)
                    lf_client = lastfm_worker.client if lastfm_worker else None
                    if not lf_client:
                        continue
                    lf_result = lf_client.get_track_info(artist_name, track_title)
                    if lf_result:
                        lf_url = lf_result.get("url")
                        if lf_url:
                            pp["lastfm_url"] = lf_url
                        lf_toptags = lf_result.get("toptags", {})
                        if isinstance(lf_toptags, dict):
                            tag_list = lf_toptags.get("tag", [])
                            if isinstance(tag_list, list):
                                pp["lastfm_tags"] = [tag.get("name", "") for tag in tag_list if isinstance(tag, dict) and tag.get("name")]
                            elif isinstance(tag_list, dict) and tag_list.get("name"):
                                pp["lastfm_tags"] = [tag_list["name"]]
                except Exception as exc:
                    logger_.error("Last.fm lookup failed (non-fatal): %s", exc)
                continue

            if source_name == "genius":
                if cfg.get("genius.embed_tags", True) is False:
                    continue
                if not track_title or not artist_name:
                    continue
                try:
                    import core.genius_client as _genius_module

                    if time.time() < _genius_module._rate_limit_until:
                        logger_.info("Genius rate-limited, skipping (non-blocking)")
                        continue
                    genius_worker = getattr(runtime, "genius_worker", None)
                    g_client = genius_worker.client if genius_worker else None
                    if not g_client:
                        continue
                    g_result = g_client.search_song(artist_name, track_title)
                    if g_result:
                        g_id = g_result.get("id")
                        if g_id:
                            pp["id_tags"]["GENIUS_TRACK_ID"] = str(g_id)
                        g_url = g_result.get("url")
                        if g_url:
                            pp["genius_url"] = g_url
                except Exception as exc:
                    logger_.error("Genius lookup failed (non-fatal): %s", exc)
                continue

        if not pp["id_tags"] and not pp["deezer_bpm"] and not pp["deezer_isrc"] and not pp["audiodb_mood"] and not pp["audiodb_style"]:
            return

        filtered_tags: Dict[str, str] = {}
        for tag_name, value in pp["id_tags"].items():
            config_path = tag_config.get(tag_name)
            if config_path and not _tag_enabled(config_path):
                continue
            filtered_tags[tag_name] = value

        written = []
        id3_tag_map = {
            "MUSICBRAINZ_RECORDING_ID": ("UFID", "http://musicbrainz.org"),
            "MUSICBRAINZ_ARTIST_ID": ("TXXX", "MusicBrainz Artist Id"),
            "MUSICBRAINZ_RELEASE_ID": ("TXXX", "MusicBrainz Album Id"),
            "MUSICBRAINZ_RELEASEGROUPID": ("TXXX", "MusicBrainz Release Group Id"),
            "MUSICBRAINZ_ALBUMARTISTID": ("TXXX", "MusicBrainz Album Artist Id"),
            "MUSICBRAINZ_RELEASETRACKID": ("TXXX", "MusicBrainz Release Track Id"),
            "RELEASETYPE": ("TXXX", "MusicBrainz Album Type"),
            "RELEASESTATUS": ("TXXX", "MusicBrainz Album Status"),
            "RELEASECOUNTRY": ("TXXX", "MusicBrainz Album Release Country"),
            "ORIGINALDATE": ("TDOR", None),
            "MEDIA": ("TMED", None),
        }
        vorbis_tag_map = {
            "MUSICBRAINZ_RECORDING_ID": "MUSICBRAINZ_TRACKID",
            "MUSICBRAINZ_ARTIST_ID": "MUSICBRAINZ_ARTISTID",
            "MUSICBRAINZ_RELEASE_ID": "MUSICBRAINZ_ALBUMID",
            "MUSICBRAINZ_RELEASEGROUPID": "MUSICBRAINZ_RELEASEGROUPID",
            "MUSICBRAINZ_ALBUMARTISTID": "MUSICBRAINZ_ALBUMARTISTID",
            "MUSICBRAINZ_RELEASETRACKID": "MUSICBRAINZ_RELEASETRACKID",
        }
        mp4_tag_map = {
            "MUSICBRAINZ_RECORDING_ID": "MusicBrainz Track Id",
            "MUSICBRAINZ_ARTIST_ID": "MusicBrainz Artist Id",
            "MUSICBRAINZ_RELEASE_ID": "MusicBrainz Album Id",
            "MUSICBRAINZ_RELEASEGROUPID": "MusicBrainz Release Group Id",
            "MUSICBRAINZ_ALBUMARTISTID": "MusicBrainz Album Artist Id",
            "MUSICBRAINZ_RELEASETRACKID": "MusicBrainz Release Track Id",
            "RELEASETYPE": "MusicBrainz Album Type",
            "RELEASESTATUS": "MusicBrainz Album Status",
            "RELEASECOUNTRY": "MusicBrainz Album Release Country",
        }

        if isinstance(audio_file.tags, symbols.ID3):
            for tag_name, value in filtered_tags.items():
                spec = id3_tag_map.get(tag_name)
                if spec:
                    frame_type, desc = spec
                    if frame_type == "UFID":
                        audio_file.tags.add(symbols.UFID(owner=desc, data=str(value).encode("ascii")))
                        written.append(f"UFID:{desc}")
                    elif frame_type == "TDOR":
                        audio_file.tags.add(symbols.TDOR(encoding=3, text=[value]))
                        written.append("TDOR")
                    elif frame_type == "TMED":
                        audio_file.tags.add(symbols.TMED(encoding=3, text=[value]))
                        written.append("TMED")
                    else:
                        audio_file.tags.add(symbols.TXXX(encoding=3, desc=desc, text=[value]))
                        written.append(f"TXXX:{desc}")
                else:
                    audio_file.tags.add(symbols.TXXX(encoding=3, desc=tag_name, text=[str(value)]))
                    written.append(f"TXXX:{tag_name}")
        elif _is_vorbis_like(audio_file, symbols):
            for tag_name, value in filtered_tags.items():
                audio_file[vorbis_tag_map.get(tag_name, tag_name)] = [str(value)]
                written.append(vorbis_tag_map.get(tag_name, tag_name))
        elif isinstance(audio_file, symbols.MP4):
            for tag_name, value in filtered_tags.items():
                key = f"----:com.apple.iTunes:{mp4_tag_map.get(tag_name, tag_name)}"
                audio_file[key] = [symbols.MP4FreeForm(str(value).encode("utf-8"))]
                written.append(key)

        if written:
            logger_.info("Embedded IDs: %s", ", ".join(written))

        release_year = pp["release_year"]
        needs_date_tag = bool(release_year and not metadata.get("date"))
        if needs_date_tag:
            metadata["date"] = release_year
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TDRC(encoding=3, text=[release_year]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["date"] = [release_year]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["\xa9day"] = [release_year]
            logger_.info("Date tag: %s", release_year)

        if _tag_enabled("deezer.tags.bpm") and pp["deezer_bpm"] and pp["deezer_bpm"] > 0:
            bpm_int = int(pp["deezer_bpm"])
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TBPM(encoding=3, text=[str(bpm_int)]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["BPM"] = [str(bpm_int)]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["tmpo"] = [bpm_int]
            logger_.info("BPM: %s", bpm_int)

        if _tag_enabled("audiodb.tags.mood") and pp["audiodb_mood"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="MOOD", text=[pp["audiodb_mood"]]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["MOOD"] = [pp["audiodb_mood"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:MOOD"] = [symbols.MP4FreeForm(pp["audiodb_mood"].encode("utf-8"))]

        if _tag_enabled("audiodb.tags.style") and pp["audiodb_style"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="STYLE", text=[pp["audiodb_style"]]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["STYLE"] = [pp["audiodb_style"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:STYLE"] = [symbols.MP4FreeForm(pp["audiodb_style"].encode("utf-8"))]

        if _tag_enabled("metadata_enhancement.tags.genre_merge"):
            enrichment_genres = []
            if _tag_enabled("musicbrainz.tags.genres"):
                enrichment_genres += pp["mb_genres"]
            if pp["audiodb_genre"] and _tag_enabled("audiodb.tags.genre"):
                enrichment_genres.append(pp["audiodb_genre"])
            if _tag_enabled("lastfm.tags.genres"):
                enrichment_genres += pp["lastfm_tags"]
            if enrichment_genres:
                from core.genre_filter import filter_genres as _filter_genres

                enrichment_genres = _filter_genres(enrichment_genres, cfg)
                source_genres = [g.strip() for g in str(metadata.get("genre", "")).split(",") if g.strip()]
                seen = set()
                merged = []
                for genre in source_genres + enrichment_genres:
                    key = genre.strip().lower()
                    if key and key not in seen:
                        seen.add(key)
                        merged.append(genre.strip().title())
                    if len(merged) >= 5:
                        break
                if merged:
                    genre_string = ", ".join(merged)
                    if isinstance(audio_file.tags, symbols.ID3):
                        audio_file.tags.add(symbols.TCON(encoding=3, text=[genre_string]))
                    elif _is_vorbis_like(audio_file, symbols):
                        audio_file["GENRE"] = [genre_string]
                    elif isinstance(audio_file, symbols.MP4):
                        audio_file["\xa9gen"] = [genre_string]
                    logger_.info("Genres merged: %s", genre_string)

        isrc_candidates = []
        if pp["isrc"] and _tag_enabled("musicbrainz.tags.isrc"):
            isrc_candidates.append(("MusicBrainz", pp["isrc"]))
        if pp["deezer_isrc"] and _tag_enabled("deezer.tags.isrc"):
            isrc_candidates.append(("Deezer", pp["deezer_isrc"]))
        if pp["tidal_isrc"] and _tag_enabled("tidal.tags.isrc"):
            isrc_candidates.append(("Tidal", pp["tidal_isrc"]))
        if pp["qobuz_isrc"] and _tag_enabled("qobuz.tags.isrc"):
            isrc_candidates.append(("Qobuz", pp["qobuz_isrc"]))
        if isrc_candidates:
            isrc_source, final_isrc = isrc_candidates[0]
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TSRC(encoding=3, text=[final_isrc]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["ISRC"] = [final_isrc]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:ISRC"] = [symbols.MP4FreeForm(final_isrc.encode("utf-8"))]
            logger_.info("ISRC (%s): %s", isrc_source, final_isrc)

        copyright_candidates = []
        if pp["tidal_copyright"] and _tag_enabled("tidal.tags.copyright"):
            copyright_candidates.append(("Tidal", pp["tidal_copyright"]))
        if pp["qobuz_copyright"] and _tag_enabled("qobuz.tags.copyright"):
            copyright_candidates.append(("Qobuz", pp["qobuz_copyright"]))
        if copyright_candidates:
            copyright_source, final_copyright = copyright_candidates[0]
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TCOP(encoding=3, text=[final_copyright]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["COPYRIGHT"] = [final_copyright]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["cprt"] = [final_copyright]
            logger_.info("Copyright (%s): %s", copyright_source, final_copyright[:60])

        if _tag_enabled("qobuz.tags.label") and pp["qobuz_label"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TPUB(encoding=3, text=[pp["qobuz_label"]]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["LABEL"] = [pp["qobuz_label"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:LABEL"] = [symbols.MP4FreeForm(pp["qobuz_label"].encode("utf-8"))]

        if _tag_enabled("lastfm.tags.url") and pp["lastfm_url"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="LASTFM_URL", text=[pp["lastfm_url"]]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["LASTFM_URL"] = [pp["lastfm_url"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:LASTFM_URL"] = [symbols.MP4FreeForm(pp["lastfm_url"].encode("utf-8"))]

        if _tag_enabled("genius.tags.url") and pp["genius_url"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="GENIUS_URL", text=[pp["genius_url"]]))
            elif _is_vorbis_like(audio_file, symbols):
                audio_file["GENIUS_URL"] = [pp["genius_url"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:GENIUS_URL"] = [symbols.MP4FreeForm(pp["genius_url"].encode("utf-8"))]

        release_id = pp["release_mbid"]
        if release_id:
            metadata["musicbrainz_release_id"] = release_id
            if db is not None:
                try:
                    album_name_for_db = metadata.get("album", "")
                    album_artist_for_db = metadata.get("album_artist", "") or metadata.get("artist", "")
                    if album_name_for_db and album_artist_for_db:
                        conn = db._get_connection()
                        try:
                            cursor = conn.cursor()
                            cursor.execute(
                                """
                                UPDATE albums SET year = ?
                                WHERE (year IS NULL OR year = 0)
                                  AND id IN (
                                    SELECT al.id FROM albums al
                                    JOIN artists ar ON ar.id = al.artist_id
                                    WHERE LOWER(al.title) = LOWER(?) AND LOWER(ar.name) = LOWER(?)
                                  )
                                """,
                                (int(release_year), album_name_for_db, album_artist_for_db),
                            )
                            if cursor.rowcount > 0:
                                conn.commit()
                                logger_.info("Updated album year to %s in database", release_year)
                            else:
                                conn.rollback()
                        finally:
                            conn.close()
                except Exception as exc:
                    logger_.error("Could not update album year in DB: %s", exc)

    except Exception as exc:
        logger_.error("Error embedding source IDs (non-fatal): %s", exc)


def download_cover_art(album_info: dict, target_dir: str, context: dict = None, runtime=None):
    cfg = _get_config_manager(runtime)
    logger_ = _get_logger(runtime)
    if cfg.get("metadata_enhancement.cover_art_download", True) is False:
        return

    try:
        cover_path = os.path.join(target_dir, "cover.jpg")
        album_info = album_info or {}
        release_mbid = album_info.get("musicbrainz_release_id")
        prefer_caa = cfg.get("metadata_enhancement.prefer_caa_art", False)

        if os.path.exists(cover_path):
            if release_mbid and prefer_caa:
                try:
                    existing_size = os.path.getsize(cover_path)
                    if existing_size > 200_000:
                        return
                    is_upgrade = True
                except Exception:
                    return
            else:
                return
        else:
            is_upgrade = False

        image_data = None
        if release_mbid and prefer_caa:
            try:
                caa_url = f"https://coverartarchive.org/release/{release_mbid}/front"
                req = urllib.request.Request(caa_url, headers={"Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    image_data = response.read()
                if not image_data or len(image_data) <= 1000:
                    image_data = None
            except Exception:
                image_data = None

        if is_upgrade and not image_data:
            logger_.error("CAA upgrade failed - keeping existing cover.jpg")
            return

        if not image_data:
            art_url = album_info.get("album_image_url")
            if not art_url and context:
                album_ctx = get_import_context_album(context)
                art_url = album_ctx.get("image_url")
                if not art_url and album_ctx.get("images"):
                    images = album_ctx.get("images", [])
                    if images and isinstance(images[0], dict):
                        art_url = images[0].get("url", "")
                if art_url:
                    logger_.info("Using cover art URL from album context")
            if art_url and "i.scdn.co" in art_url:
                try:
                    from core.spotify_client import _upgrade_spotify_image_url

                    art_url = _upgrade_spotify_image_url(art_url)
                except Exception:
                    pass
            elif art_url and "mzstatic.com" in art_url:
                import re as _re

                art_url = _re.sub(r"\d+x\d+bb", "3000x3000bb", art_url)
            if not art_url:
                logger_.warning("No cover art URL available for download.")
                return
            with urllib.request.urlopen(art_url, timeout=10) as response:
                image_data = response.read()

        if not image_data:
            return

        with open(cover_path, "wb") as handle:
            handle.write(image_data)
        logger_.info("Cover art downloaded to: %s", cover_path)
    except Exception as exc:
        logger_.error("Error downloading cover.jpg: %s", exc)


def generate_lrc_file(file_path: str, context: dict, artist: dict, album_info: dict, runtime=None) -> bool:
    cfg = _get_config_manager(runtime)
    logger_ = _get_logger(runtime)
    if cfg.get("metadata_enhancement.lrclib_enabled", True) is False:
        return False

    try:
        from core.lyrics_client import lyrics_client

        context = normalize_import_context(context)
        original_search = get_import_original_search(context)
        album_context = get_import_context_album(context)
        track_name = get_import_clean_title(context, default=original_search.get("title", "Unknown Track"))

        if isinstance(artist, dict):
            artist_name = artist.get("name", "Unknown Artist")
        elif hasattr(artist, "name"):
            artist_name = artist.name
        else:
            artist_name = str(artist) if artist else "Unknown Artist"

        album_name = None
        duration_seconds = None
        if album_info and album_info.get("is_album"):
            album_name = (
                get_import_clean_album(context, album_info=album_info, default="")
                or album_info.get("album_name")
                or album_context.get("name")
            )

        if original_search.get("duration_ms"):
            duration_seconds = int(original_search["duration_ms"] / 1000)

        success = lyrics_client.create_lrc_file(
            audio_file_path=file_path,
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            duration_seconds=duration_seconds,
        )

        if success:
            logger_.info("LRC file generated for: %s", track_name)
        else:
            logger_.warning("No lyrics found for: %s", track_name)
        return success
    except Exception as exc:
        logger_.error("Error generating LRC file for %s: %s", file_path, exc)
        return False


def wipe_source_tags(file_path: str, runtime=None) -> bool:
    cfg = _get_config_manager(runtime)
    logger_ = _get_logger(runtime)
    _ = cfg  # keep signature parallel with other helpers

    try:
        _strip_all_non_audio_tags(file_path, runtime=runtime)
        symbols = _get_mutagen_symbols(runtime)
        if not symbols:
            return False

        audio = symbols.File(file_path)
        if audio is None:
            return False
        if hasattr(audio, "clear_pictures"):
            audio.clear_pictures()
        if audio.tags is not None:
            tag_count = len(audio.tags)
            audio.tags.clear()
        else:
            audio.add_tags()
            tag_count = 0
        _save_audio_file(audio, symbols)
        if tag_count > 0:
            logger_.info("[Tag Wipe] Stripped %s source tags from: %s", tag_count, os.path.basename(file_path))
        return True
    except Exception as exc:
        logger_.error("[Tag Wipe] Failed (non-fatal): %s", exc)
        return False


def enhance_file_metadata(file_path: str, context: dict, artist: dict, album_info: dict, runtime=None) -> bool:
    cfg = _get_config_manager(runtime)
    logger_ = _get_logger(runtime)
    if cfg.get("metadata_enhancement.enabled", True) is False:
        logger_.warning("Metadata enhancement disabled in config.")
        return True

    if album_info is None:
        album_info = {}

    symbols = _get_mutagen_symbols(runtime)
    if not symbols:
        logger_.error("Mutagen is unavailable, cannot enhance metadata.")
        return False

    file_lock = _get_file_lock(file_path)
    with file_lock:
        logger_.info("Enhancing metadata for: %s", os.path.basename(file_path))
        try:
            _strip_all_non_audio_tags(file_path, runtime=runtime)
            audio_file = symbols.File(file_path)
            if audio_file is None:
                logger_.error("Could not load audio file with Mutagen: %s", file_path)
                return False

            if hasattr(audio_file, "clear_pictures"):
                audio_file.clear_pictures()

            if audio_file.tags is not None:
                if len(audio_file.tags) > 0:
                    tag_keys = list(audio_file.tags.keys())[:15]
                    logger_.info("Clearing %s existing tags: %s", len(audio_file.tags), ", ".join(str(k) for k in tag_keys))
                audio_file.tags.clear()
            else:
                audio_file.add_tags()

            _save_audio_file(audio_file, symbols)

            metadata = extract_source_metadata(context, artist, album_info, runtime=runtime)
            if not metadata:
                logger_.error("Could not extract source metadata, saving with cleared tags.")
                _save_audio_file(audio_file, symbols)
                return True

            track_num_str = f"{metadata.get('track_number', 1)}/{metadata.get('total_tracks', 1)}"
            write_multi = cfg.get("metadata_enhancement.tags.write_multi_artist", False)
            artists_list = metadata.get("_artists_list", [])

            if isinstance(audio_file.tags, symbols.ID3):
                if metadata.get("title"):
                    audio_file.tags.add(symbols.TIT2(encoding=3, text=[metadata["title"]]))
                if metadata.get("artist"):
                    audio_file.tags.add(symbols.TPE1(encoding=3, text=[metadata["artist"]]))
                    if write_multi and len(artists_list) > 1:
                        audio_file.tags.add(symbols.TPE1(encoding=3, text=artists_list))
                if metadata.get("album_artist"):
                    audio_file.tags.add(symbols.TPE2(encoding=3, text=[metadata["album_artist"]]))
                if metadata.get("album"):
                    audio_file.tags.add(symbols.TALB(encoding=3, text=[metadata["album"]]))
                if metadata.get("date"):
                    audio_file.tags.add(symbols.TDRC(encoding=3, text=[metadata["date"]]))
                if metadata.get("genre"):
                    audio_file.tags.add(symbols.TCON(encoding=3, text=[metadata["genre"]]))
                audio_file.tags.add(symbols.TRCK(encoding=3, text=[track_num_str]))
                if metadata.get("disc_number"):
                    audio_file.tags.add(symbols.TPOS(encoding=3, text=[str(metadata["disc_number"])]))
            elif _is_vorbis_like(audio_file, symbols):
                if metadata.get("title"):
                    audio_file["title"] = [metadata["title"]]
                if metadata.get("artist"):
                    audio_file["artist"] = [metadata["artist"]]
                    if write_multi and len(artists_list) > 1:
                        audio_file["artists"] = artists_list
                if metadata.get("album_artist"):
                    audio_file["albumartist"] = [metadata["album_artist"]]
                if metadata.get("album"):
                    audio_file["album"] = [metadata["album"]]
                if metadata.get("date"):
                    audio_file["date"] = [metadata["date"]]
                if metadata.get("genre"):
                    audio_file["genre"] = [metadata["genre"]]
                audio_file["tracknumber"] = [track_num_str]
                if metadata.get("disc_number"):
                    audio_file["discnumber"] = [str(metadata["disc_number"])]
            elif isinstance(audio_file, symbols.MP4):
                if metadata.get("title"):
                    audio_file["\xa9nam"] = [metadata["title"]]
                if metadata.get("artist"):
                    audio_file["\xa9ART"] = artists_list if (write_multi and len(artists_list) > 1) else [metadata["artist"]]
                if metadata.get("album_artist"):
                    audio_file["aART"] = [metadata["album_artist"]]
                if metadata.get("album"):
                    audio_file["\xa9alb"] = [metadata["album"]]
                if metadata.get("date"):
                    audio_file["\xa9day"] = [metadata["date"]]
                if metadata.get("genre"):
                    audio_file["\xa9gen"] = [metadata["genre"]]
                audio_file["trkn"] = [(metadata.get("track_number", 1), metadata.get("total_tracks", 1))]
                if metadata.get("disc_number"):
                    audio_file["disk"] = [(metadata["disc_number"], 0)]

            embed_source_ids(audio_file, metadata, context, runtime=runtime)

            if album_info is not None and metadata.get("musicbrainz_release_id"):
                album_info["musicbrainz_release_id"] = metadata["musicbrainz_release_id"]

            if cfg.get("metadata_enhancement.embed_album_art", True):
                embed_album_art_metadata(audio_file, metadata, runtime=runtime)

            quality = context.get("_audio_quality", "")
            if quality and cfg.get("metadata_enhancement.tags.quality_tag", True) is not False:
                if isinstance(audio_file.tags, symbols.ID3):
                    audio_file.tags.add(symbols.TXXX(encoding=3, desc="QUALITY", text=[quality]))
                elif _is_vorbis_like(audio_file, symbols):
                    audio_file["quality"] = [quality]
                elif isinstance(audio_file, symbols.MP4):
                    audio_file["----:com.apple.iTunes:QUALITY"] = [symbols.MP4FreeForm(quality.encode("utf-8"))]

            _save_audio_file(audio_file, symbols)

            verified = _verify_metadata_written(file_path, runtime=runtime)
            if verified:
                logger_.info("Metadata enhanced successfully.")
            else:
                logger_.info("Metadata saved but verification found issues (see above).")
            return True
        except Exception as exc:
            import traceback

            logger_.error("Error enhancing metadata for %s: %s", file_path, exc)
            logger_.error("[Metadata Debug] Exception type: %s", type(exc).__name__)
            logger_.info("[Metadata Debug] File exists: %s", os.path.exists(file_path))
            logger_.warning("[Metadata Debug] Artist: %s", artist.get("name", "MISSING") if artist else "None")
            logger_.warning("[Metadata Debug] Album info: %s", album_info.get("album_name", "MISSING") if album_info else "None")
            logger_.error("[Metadata Debug] Traceback:\n%s", traceback.format_exc())
            return False


def _normalize_album_cache_key(album_name: str) -> str:
    result = _EDITION_PAREN_RE.sub("", album_name or "")
    result = _EDITION_BARE_RE.sub("", result)
    return result.lower().strip()
