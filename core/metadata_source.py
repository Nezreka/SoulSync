"""Source metadata extraction and source-ID embedding helpers."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict

from core.import_context import (
    extract_artist_name,
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_album,
    get_import_original_search,
    get_import_source,
    get_import_source_ids,
    get_import_track_info,
    get_source_tag_names,
    normalize_import_context,
)
from core.metadata_service import get_itunes_client
from database.music_database import get_database
from core.metadata_common import (
    get_config_manager,
    get_logger,
    get_mutagen_symbols,
    is_vorbis_like,
)

__all__ = [
    "extract_source_metadata",
    "embed_source_ids",
]


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


def normalize_album_cache_key(album_name: str) -> str:
    result = _EDITION_PAREN_RE.sub("", album_name or "")
    result = _EDITION_BARE_RE.sub("", result)
    return result.lower().strip()


def extract_source_metadata(context: dict, artist: dict, album_info: dict) -> dict:
    if album_info is None:
        album_info = {}

    cfg = get_config_manager()
    logger_ = get_logger()
    context = normalize_import_context(context)
    original_search = get_import_original_search(context)
    album_ctx = get_import_context_album(context)
    track_info = get_import_track_info(context)
    source = get_import_source(context)
    source_ids = get_import_source_ids(context)

    artist_dict = artist if isinstance(artist, dict) else {
        "name": extract_artist_name(artist),
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
        logger_.info("Metadata: Using clean title: '%s'", metadata["title"])
    elif album_info.get("clean_track_name"):
        logger_.info("Metadata: Using album info clean name: '%s'", metadata["title"])
    else:
        logger_.warning("Metadata: Using original title as fallback: '%s'", metadata["title"])

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
        logger_.info("Metadata: Using all artists: '%s'", metadata["artist"])
    else:
        metadata["artist"] = artist_dict.get("name", "") or get_import_clean_artist(context)
        logger_.info("Metadata: Using primary artist: '%s'", metadata["artist"])

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
                    itunes_client = get_itunes_client()
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
        logger_.info("[METADATA] Album track - track_number: %s, album: %s", metadata["track_number"], metadata["album"])
    else:
        if album_ctx and album_ctx.get("name"):
            logger_.info("[SAFEGUARD] Using album context name instead of track title for album metadata")
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

    logger_.info(
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


def embed_source_ids(audio_file, metadata: dict, context: dict = None, runtime=None):
    cfg = get_config_manager()
    logger_ = get_logger()
    symbols = get_mutagen_symbols()
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

        db = get_database()

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
        elif isinstance(audio_file, symbols.MP4):
            # Keep the dedicated MP4 path last so the same tag maps can be reused.
            for tag_name, value in filtered_tags.items():
                key = f"----:com.apple.iTunes:{mp4_tag_map.get(tag_name, tag_name)}"
                audio_file[key] = [symbols.MP4FreeForm(str(value).encode("utf-8"))]
                written.append(key)
        elif is_vorbis_like(audio_file, symbols):
            for tag_name, value in filtered_tags.items():
                audio_file[vorbis_tag_map.get(tag_name, tag_name)] = [str(value)]
                written.append(vorbis_tag_map.get(tag_name, tag_name))

        if written:
            logger_.info("Embedded IDs: %s", ", ".join(written))

        release_year = pp["release_year"]
        needs_date_tag = bool(release_year and not metadata.get("date"))
        if needs_date_tag:
            metadata["date"] = release_year
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TDRC(encoding=3, text=[release_year]))
            elif is_vorbis_like(audio_file, symbols):
                audio_file["date"] = [release_year]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["\xa9day"] = [release_year]
            logger_.info("Date tag: %s", release_year)

        if _tag_enabled("deezer.tags.bpm") and pp["deezer_bpm"] and pp["deezer_bpm"] > 0:
            bpm_int = int(pp["deezer_bpm"])
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TBPM(encoding=3, text=[str(bpm_int)]))
            elif is_vorbis_like(audio_file, symbols):
                audio_file["BPM"] = [str(bpm_int)]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["tmpo"] = [bpm_int]
            logger_.info("BPM: %s", bpm_int)

        if _tag_enabled("audiodb.tags.mood") and pp["audiodb_mood"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="MOOD", text=[pp["audiodb_mood"]]))
            elif is_vorbis_like(audio_file, symbols):
                audio_file["MOOD"] = [pp["audiodb_mood"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:MOOD"] = [symbols.MP4FreeForm(pp["audiodb_mood"].encode("utf-8"))]

        if _tag_enabled("audiodb.tags.style") and pp["audiodb_style"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="STYLE", text=[pp["audiodb_style"]]))
            elif is_vorbis_like(audio_file, symbols):
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
                    elif is_vorbis_like(audio_file, symbols):
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
            elif is_vorbis_like(audio_file, symbols):
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
            elif is_vorbis_like(audio_file, symbols):
                audio_file["COPYRIGHT"] = [final_copyright]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["cprt"] = [final_copyright]
            logger_.info("Copyright (%s): %s", copyright_source, final_copyright[:60])

        if _tag_enabled("qobuz.tags.label") and pp["qobuz_label"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TPUB(encoding=3, text=[pp["qobuz_label"]]))
            elif is_vorbis_like(audio_file, symbols):
                audio_file["LABEL"] = [pp["qobuz_label"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:LABEL"] = [symbols.MP4FreeForm(pp["qobuz_label"].encode("utf-8"))]

        if _tag_enabled("lastfm.tags.url") and pp["lastfm_url"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="LASTFM_URL", text=[pp["lastfm_url"]]))
            elif is_vorbis_like(audio_file, symbols):
                audio_file["LASTFM_URL"] = [pp["lastfm_url"]]
            elif isinstance(audio_file, symbols.MP4):
                audio_file["----:com.apple.iTunes:LASTFM_URL"] = [symbols.MP4FreeForm(pp["lastfm_url"].encode("utf-8"))]

        if _tag_enabled("genius.tags.url") and pp["genius_url"]:
            if isinstance(audio_file.tags, symbols.ID3):
                audio_file.tags.add(symbols.TXXX(encoding=3, desc="GENIUS_URL", text=[pp["genius_url"]]))
            elif is_vorbis_like(audio_file, symbols):
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
