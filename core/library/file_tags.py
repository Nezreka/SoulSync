"""Read embedded tags from an audio file for the Audit Trail UI.

The Audit Trail modal on the Library History view needs to show
exactly what tags are currently embedded in a downloaded file —
title/artist/album metadata, MusicBrainz/Spotify/Tidal IDs,
ReplayGain values, ISRC, cover-art presence, lyrics, and anything
else SoulSync or its background enrichment workers wrote.

The file is the single source of truth. A persisted snapshot at
post-process time would drift the moment a background worker
(audiodb, lastfm, genius, deezer enrichment, lyrics fetch) writes
more tags, or if the user manually re-tags. So the audit endpoint
reads the file live on demand.

This module is the pure mutagen wrapper. Returns a canonical
JSON-serializable dict; never raises (failure modes degrade to an
``{'available': False, 'reason': '...'}`` shape so the caller can
surface a useful error to the user).

Frontend renders the canonical shape directly — no per-source
mapping at the API layer.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from core.metadata.common import get_mutagen_symbols
from utils.logging_config import get_logger


logger = get_logger("library.file_tags")


# ID3 frame names that carry textual values we want to surface
# under the "core" tag group. mutagen exposes ID3 frames keyed by
# their 4-letter codes, so map those codes to friendly labels.
_ID3_TEXT_FRAMES = {
    "TIT2": "title",
    "TPE1": "artist",
    "TPE2": "album_artist",
    "TALB": "album",
    "TDRC": "date",
    "TCON": "genre",
    "TRCK": "tracknumber",
    "TPOS": "discnumber",
    "TBPM": "bpm",
    "TMOO": "mood",
    "TCOP": "copyright",
    "TPUB": "publisher",
    "TLAN": "language",
}


# TXXX-style ID3 frames carry user-defined keys via their `desc`
# attribute. We pick known descriptions out of those.
_KNOWN_TXXX_DESCS = {
    "MusicBrainz Album Id": "musicbrainz_albumid",
    "MusicBrainz Artist Id": "musicbrainz_artistid",
    "MusicBrainz Album Artist Id": "musicbrainz_albumartistid",
    "MusicBrainz Release Group Id": "musicbrainz_releasegroupid",
    "MusicBrainz Release Track Id": "musicbrainz_releasetrackid",
    "MusicBrainz Track Id": "musicbrainz_trackid",
    "Spotify Track Id": "spotify_track_id",
    "Spotify Artist Id": "spotify_artist_id",
    "Spotify Album Id": "spotify_album_id",
    "Tidal Track Id": "tidal_track_id",
    "Tidal Artist Id": "tidal_artist_id",
    "Tidal Album Id": "tidal_album_id",
    "Deezer Track Id": "deezer_track_id",
    "Deezer Artist Id": "deezer_artist_id",
    "Deezer Album Id": "deezer_album_id",
    "AudioDB Track Id": "audiodb_track_id",
    "AudioDB Artist Id": "audiodb_artist_id",
    "AudioDB Album Id": "audiodb_album_id",
    "iTunes Track Id": "itunes_track_id",
    "iTunes Artist Id": "itunes_artist_id",
    "iTunes Album Id": "itunes_album_id",
    "Genius Track Id": "genius_track_id",
    "Genius Url": "genius_url",
    "LastFm Url": "lastfm_url",
    "ASIN": "asin",
    "BARCODE": "barcode",
    "CATALOGNUMBER": "catalognumber",
    "ISRC": "isrc",
    "ORIGINALDATE": "originaldate",
    "RELEASECOUNTRY": "releasecountry",
    "RELEASESTATUS": "releasestatus",
    "RELEASETYPE": "releasetype",
    "SCRIPT": "script",
    "MEDIA": "media",
    "TOTALDISCS": "totaldiscs",
    "TOTALTRACKS": "tracktotal",
    "STYLE": "style",
    "QUALITY": "quality",
    "Artists": "artists",
    "replaygain_track_gain": "replaygain_track_gain",
    "replaygain_track_peak": "replaygain_track_peak",
    "replaygain_album_gain": "replaygain_album_gain",
    "replaygain_album_peak": "replaygain_album_peak",
}


# Vorbis (FLAC/OGG/OPUS) tag keys map 1:1 with our friendly names —
# Vorbis is the most permissive container, every key is just a
# string. mutagen surfaces them as lowercase by convention.
# This passlist filters out the noise (encoder, comment, ...) and
# whitelists everything we want to show.
_VORBIS_ALLOWED_KEYS = frozenset({
    "title", "artist", "albumartist", "album_artist", "album",
    "date", "year", "genre", "tracknumber", "discnumber",
    "tracktotal", "totaltracks", "totaldiscs", "bpm", "mood",
    "copyright", "publisher", "language", "style", "quality",
    "isrc", "barcode", "catalognumber", "asin", "script",
    "media", "originaldate", "releasecountry", "releasestatus",
    "releasetype", "artists", "composer", "performer",
    "musicbrainz_albumid", "musicbrainz_artistid",
    "musicbrainz_albumartistid", "musicbrainz_releasegroupid",
    "musicbrainz_releasetrackid", "musicbrainz_trackid",
    "spotify_track_id", "spotify_artist_id", "spotify_album_id",
    "tidal_track_id", "tidal_artist_id", "tidal_album_id",
    "deezer_track_id", "deezer_artist_id", "deezer_album_id",
    "audiodb_track_id", "audiodb_artist_id", "audiodb_album_id",
    "itunes_track_id", "itunes_artist_id", "itunes_album_id",
    "genius_track_id", "genius_url", "lastfm_url",
    "replaygain_track_gain", "replaygain_track_peak",
    "replaygain_album_gain", "replaygain_album_peak",
    "lyrics", "unsyncedlyrics",
})


def read_embedded_tags(file_path: str) -> Dict[str, Any]:
    """Read embedded tags from an audio file via mutagen.

    Returns a dict with one of two shapes:

    - ``{"available": True, "format": "...", "bitrate": ..., "tags": {...}, "has_picture": bool}``
      on success. ``tags`` is a flat dict of lowercase friendly key →
      string value (lists joined with ', '). Long fields like
      ``lyrics`` are returned in full — caller decides how to display.

    - ``{"available": False, "reason": "..."}`` when the file doesn't
      exist, isn't readable, or mutagen can't recognise the format.

    Never raises. Caller surfaces ``reason`` to the user verbatim.
    """
    if not file_path or not isinstance(file_path, str):
        return {"available": False, "reason": "No file path on this row."}

    if not os.path.exists(file_path):
        return {
            "available": False,
            "reason": f"File no longer exists at: {file_path}",
        }

    symbols = get_mutagen_symbols()
    if symbols is None:
        return {"available": False, "reason": "Mutagen is unavailable."}

    try:
        audio = symbols.File(file_path)
    except Exception as exc:
        logger.debug("Mutagen open failed for %s: %s", file_path, exc)
        return {
            "available": False,
            "reason": f"Could not open file: {exc}",
        }

    if audio is None:
        return {
            "available": False,
            "reason": "File format not recognised by mutagen.",
        }

    fmt = type(audio).__name__
    bitrate = 0
    duration = 0.0
    try:
        if getattr(audio, "info", None) is not None:
            bitrate = int(getattr(audio.info, "bitrate", 0) or 0)
            duration = float(getattr(audio.info, "length", 0) or 0)
    except Exception as exc:  # noqa: S110 — optional info, missing is fine
        logger.debug("audio info read failed: %s", exc)

    has_picture = _detect_picture(audio, symbols)
    tags = _extract_tags(audio, symbols)

    return {
        "available": True,
        "format": fmt,
        "bitrate": bitrate,
        "duration": duration,
        "has_picture": has_picture,
        "tags": tags,
    }


def _detect_picture(audio: Any, symbols: Any) -> bool:
    """True when the file has at least one embedded cover-art picture."""
    # FLAC / OGG-Vorbis expose pictures via `audio.pictures` list.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        return True
    # ID3 stores pictures as APIC frames.
    tags = getattr(audio, "tags", None)
    if tags is None:
        return False
    try:
        if hasattr(tags, "getall"):
            apics = tags.getall("APIC")
            if apics:
                return True
        # MP4 covers under 'covr' key.
        if "covr" in tags and tags["covr"]:
            return True
        # Vorbis embedded base64 picture frame.
        if "metadata_block_picture" in tags:
            return True
    except Exception as exc:  # noqa: S110 — optional probe, missing is fine
        logger.debug("picture detect failed: %s", exc)
    return False


def _extract_tags(audio: Any, symbols: Any) -> Dict[str, str]:
    """Flatten the audio file's tag store to a {key: string} dict.

    Handles the three container families we ship: ID3 (MP3),
    Vorbis-like (FLAC/OGG/OPUS), and MP4. Everything else falls
    through to a generic key/value dump.
    """
    out: Dict[str, str] = {}
    tags = getattr(audio, "tags", None)
    if tags is None:
        return out

    # ID3 path.
    if isinstance(tags, symbols.ID3):
        for code, label in _ID3_TEXT_FRAMES.items():
            frame = tags.get(code)
            if frame is not None:
                val = _stringify(frame)
                if val:
                    out[label] = val
        # TXXX user-defined frames (most of our extra IDs / replay
        # gain / source IDs live here).
        try:
            for frame in tags.getall("TXXX"):
                desc = getattr(frame, "desc", "")
                if not desc:
                    continue
                # mutagen's TXXX comparison is case-sensitive; the
                # dict lookup matches the exact desc string.
                key = _KNOWN_TXXX_DESCS.get(desc) or desc.lower().replace(" ", "_")
                val = _stringify(frame)
                if val:
                    out[key] = val
        except Exception as exc:  # noqa: S110 — optional TXXX walk
            logger.debug("ID3 TXXX walk failed: %s", exc)
        # USLT (unsynchronised lyrics).
        try:
            for frame in tags.getall("USLT"):
                val = _stringify(frame)
                if val:
                    out.setdefault("lyrics", val)
        except Exception as exc:  # noqa: S110 — optional USLT walk
            logger.debug("ID3 USLT walk failed: %s", exc)
        return out

    # MP4 path.
    if isinstance(audio, symbols.MP4):
        _MP4_MAP = {
            "\xa9nam": "title",
            "\xa9ART": "artist",
            "aART": "album_artist",
            "\xa9alb": "album",
            "\xa9day": "date",
            "\xa9gen": "genre",
            "trkn": "tracknumber",
            "disk": "discnumber",
            "\xa9lyr": "lyrics",
            "tmpo": "bpm",
            "cprt": "copyright",
        }
        for key, label in _MP4_MAP.items():
            if key in tags:
                val = _stringify(tags[key])
                if val:
                    out[label] = val
        # Freeform MP4 atoms — prefix ----:com.apple.iTunes:
        for k in tags.keys():
            if not isinstance(k, str) or not k.startswith("----"):
                continue
            label = k.split(":")[-1].lower()
            val = _stringify(tags[k])
            if val:
                out[label] = val
        return out

    # Vorbis-like (FLAC, OGG, OPUS): tags acts dict-like, values are
    # lists of strings.
    try:
        for raw_key in tags.keys():
            if not isinstance(raw_key, str):
                continue
            lower = raw_key.lower()
            if lower not in _VORBIS_ALLOWED_KEYS:
                # Pass through anything that looks like a known
                # source/ID-style key even if not in the allowed
                # set — covers `*_id`, `*_url` shapes we didn't
                # explicitly list.
                if not (lower.endswith("_id") or lower.endswith("_url") or lower.startswith("musicbrainz_")):
                    continue
            val = _stringify(tags[raw_key])
            if val:
                out[lower] = val
    except Exception as exc:  # noqa: S110 — optional vorbis walk
        logger.debug("Vorbis tag walk failed: %s", exc)
    return out


def _stringify(value: Any) -> str:
    """Coerce a mutagen tag value into a human-readable string.

    mutagen returns various shapes depending on the container —
    bare strings, lists of strings, frame objects with `.text` or
    `.data` attributes, MP4Cover objects, integer tuples (trkn,
    disk), etc. Best-effort flatten.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, tuple):
                # (track_num, total) shape from MP4 trkn / disk.
                if len(item) >= 1 and item[0]:
                    if len(item) >= 2 and item[1]:
                        parts.append(f"{item[0]}/{item[1]}")
                    else:
                        parts.append(str(item[0]))
                continue
            s = _stringify(item)
            if s:
                parts.append(s)
        return ", ".join(parts)
    # mutagen frame objects: prefer .text, then .data, then str().
    text = getattr(value, "text", None)
    if text is not None and text is not value:
        return _stringify(text)
    data = getattr(value, "data", None)
    if isinstance(data, (str, bytes)):
        try:
            return data.decode("utf-8", errors="replace").strip() if isinstance(data, bytes) else data.strip()
        except Exception:
            return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


__all__ = ["read_embedded_tags"]
