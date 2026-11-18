"""`artist.nfo` writer for Jellyfin / Kodi / Emby (#1449).

Jellyfin identifies artists by name search unless the artist item itself
carries a MusicBrainz ID — the MBID on the *tracks'* tags is not consulted
for the artist item. Writing a minimal Kodi-format ``artist.nfo`` with the
MusicBrainz artist ID into each artist folder fixes misidentification
(short/generic artist names otherwise resolve to the wrong artist).

The ``<name>`` is taken from the file's own ALBUMARTIST tag so it exactly
matches what the media server links tracks to. An existing ``artist.nfo``
carrying a *different* MBID is never overwritten (the user may have fixed
it by hand).

The artist folder comes from the naming template (#2138): the folder at
the level the album/single template names after the artist, counted from
the library root, when it carries the artist's name. A layout with no
artist folder (``Compilations/<Album>/track.mp3``) gets no nfo.
"""

import os
import threading
import xml.etree.ElementTree as ET
from typing import Tuple

from utils.logging_config import get_logger

logger = get_logger("library.artist_nfo")

ARTIST_NFO_FILENAME = "artist.nfo"

# Opt-in setting (default off) gating the automatic import-time write.
# The explicit backfill repair job bypasses it via force=True.
SETTING_PATH = "library.write_artist_nfo"


def build_artist_nfo(artist_name: str, musicbrainz_id: str) -> str:
    """Build the Kodi-format artist.nfo XML document.

    ElementTree handles all escaping, so names with ``&``, ``<`` etc.
    stay well-formed.
    """
    artist = ET.Element("artist")
    name_el = ET.SubElement(artist, "name")
    name_el.text = str(artist_name or "").strip()
    mbid_el = ET.SubElement(artist, "musicbrainzartistid")
    mbid_el.text = str(musicbrainz_id or "").strip()
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(artist, encoding="unicode") + "\n"


def read_artist_nfo_mbid(nfo_path: str) -> str:
    """Return the ``musicbrainzartistid`` in an existing artist.nfo, or ``""``.

    Defensive: unparseable files yield "", never raise.
    """
    try:
        root = ET.parse(nfo_path).getroot()
        el = root.find("musicbrainzartistid")
        if el is not None and el.text:
            return el.text.strip()
    except Exception as exc:  # noqa: BLE001 — best effort parse
        logger.debug("Could not parse %s: %s", nfo_path, exc)
    return ""


def write_artist_nfo(
    folder: str,
    artist_name: str,
    musicbrainz_id: str,
    *,
    overwrite: bool = False,
) -> Tuple[bool, str]:
    """Write ``artist.nfo`` into ``folder``.

    Returns ``(True, written_path)`` on success, ``(False, reason)``
    otherwise. Atomic write via ``<filename>.tmp`` + ``os.replace`` so a
    partial write never leaves a corrupt file on disk.

    An existing ``artist.nfo`` with a *different* MBID is never replaced
    unless ``overwrite=True`` — the user may have corrected it by hand.
    """
    if not folder or not os.path.isdir(folder):
        return False, "artist folder does not exist"
    artist_name = str(artist_name or "").strip()
    if not artist_name:
        return False, "no artist name"
    musicbrainz_id = str(musicbrainz_id or "").strip()
    if not musicbrainz_id:
        return False, "no MusicBrainz artist ID"

    target = os.path.join(folder, ARTIST_NFO_FILENAME)
    if os.path.exists(target) and not overwrite:
        existing_mbid = read_artist_nfo_mbid(target)
        if existing_mbid and existing_mbid != musicbrainz_id:
            return False, "existing artist.nfo has a different MBID; not overwriting"
        return False, "artist.nfo already exists"

    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(build_artist_nfo(artist_name, musicbrainz_id))
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001 — report, don't raise
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:  # noqa: S110 — cleanup, not critical
            pass
        return False, f"write failed: {exc}"

    return True, target


def _library_roots(cfg) -> list:
    """the configured music library folders and the transfer folder."""
    roots = []
    try:
        music_paths = cfg.get("library.music_paths", []) if cfg else []
        if isinstance(music_paths, str):
            music_paths = [music_paths]
        roots.extend(p for p in (music_paths or []) if isinstance(p, str) and p.strip())
        transfer = cfg.get("soulseek.transfer_path", "") if cfg else ""
        if isinstance(transfer, str) and transfer.strip():
            roots.append(transfer)
    except Exception as exc:  # noqa: BLE001 - config read must not break the writer
        logger.debug("artist.nfo: library roots unreadable: %s", exc)
    return roots


def _is_library_root(folder: str, cfg) -> bool:
    """True when ``folder`` is a configured music library root or the
    transfer folder (#1449: never plant artist.nfo at the library root)."""
    try:
        folder_norm = os.path.normcase(os.path.normpath(os.path.abspath(folder)))
    except (OSError, ValueError):
        return False
    for root in _library_roots(cfg):
        try:
            if folder_norm == os.path.normcase(os.path.normpath(os.path.abspath(root))):
                return True
        except (OSError, ValueError):
            continue
    return False


def artist_folder_for_track(track_path: str, artist_name: str, cfg) -> str:
    """the artist's folder for a library track, by the naming template (#2138)."""
    from core.library.artist_image import locate_artist_folder

    try:
        templates = cfg.get("file_organization.templates", {}) if cfg else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("artist.nfo: templates unreadable: %s", exc)
        templates = {}
    return locate_artist_folder(track_path, artist_name, _library_roots(cfg), templates)


_artwork_lock = threading.Lock()
# artist folders this process already looked up artwork for: a folder whose
# artist has none online is asked once per run, not once per imported track
_artwork_tried: set = set()


def schedule_artist_artwork(folder: str, artist_name: str, mbid: str) -> bool:
    """poster.jpg / background.jpg for this folder, off the import thread (#2138).
    once per folder per run. True when a lookup was started."""
    from core.library.artist_image import write_artist_artwork

    key = os.path.normcase(os.path.abspath(folder))
    with _artwork_lock:
        if key in _artwork_tried:
            return False
        _artwork_tried.add(key)

    def run():
        try:
            results = write_artist_artwork(folder, artist_name, mbid)
            for kind, (ok, detail) in results.items():
                if ok:
                    logger.info("artist %s written: %s", kind, detail)
                else:
                    logger.debug("artist %s skipped for %s: %s", kind, folder, detail)
        except Exception as exc:  # noqa: BLE001 - artwork is a bonus
            logger.debug("artist artwork failed for %s: %s", folder, exc)

    threading.Thread(target=run, name="artist-artwork", daemon=True).start()
    return True


def ensure_artist_nfo_for_track(track_path: str, cfg, *, force: bool = False,
                               fallback_mbid: str = "",
                               fallback_name: str = "",
                               artwork=None) -> Tuple[bool, str]:
    """Write ``artist.nfo`` for a track's artist folder when enabled (#1449).

    Reads ALBUMARTIST and the MusicBrainz album-artist ID from the file's
    own tags (post-enhancement), so ``<name>`` exactly matches the tags
    Jellyfin links tracks by. Idempotent: skips when the nfo already
    exists. Never raises — import must not fail because of a sidecar.

    ``force=True`` skips the opt-in setting check — used by the explicit
    backfill repair job, where running the job is itself the consent.

    ``fallback_mbid`` supplies the MusicBrainz artist ID when the file's
    own tags lack it (used by the backfill job, which already knows the
    artist's MBID from the database — e.g. files imported before MB tag
    embedding existed). ``fallback_name`` is the database artist name that
    MBID belongs to: the fallback is only used when it matches the file's
    ALBUMARTIST (case-insensitive), so a re-identified artist's new MBID is
    never paired with a stale file-tag name. ``<name>`` still always comes
    from the file's ALBUMARTIST tag, per the issue's exact-match
    requirement.

    ``artwork(folder, name, mbid)``, when given, is called for the artist
    folder once it is known, nfo written or not: the poster.jpg /
    background.jpg writer (#2138).
    """
    try:
        if not force and (cfg is None or not cfg.get(SETTING_PATH, False)):
            return False, "setting disabled"
        if not track_path or not os.path.isfile(track_path):
            return False, "track file not found"

        from core.library.file_tags import read_embedded_tags

        result = read_embedded_tags(track_path) or {}
        tags = result.get("tags") or {}
        album_artist = str(tags.get("album_artist") or "").strip()
        if not album_artist:
            return False, "no ALBUMARTIST in file tags"

        # #2138: the folder the naming template gives the artist, not "one up
        # from the album folder" (Artist/Album/Artist - Album/track put the nfo
        # in "Album").
        artist_folder = artist_folder_for_track(track_path, album_artist, cfg)
        if not artist_folder or not os.path.isdir(artist_folder):
            return False, "could not derive artist folder"
        # #1449 safety: never write to a configured library root or the
        # transfer folder (one artist's name/MBID at the top of the library).
        if _is_library_root(artist_folder, cfg):
            return False, "derived folder is a library root; not writing artist.nfo"

        # Key shape differs by container (see file_tags.py): ID3/Vorbis use
        # musicbrainz_albumartistid, MP4 freeform atoms surface underscored.
        # The backfill job passes the DB-known MBID as a fallback for files
        # whose tags predate MB embedding — the file tags win when present,
        # and the fallback only applies when the DB artist name matches the
        # file's ALBUMARTIST (never pair a re-identified MBID with a stale
        # name).
        file_mbid = str(
            tags.get("musicbrainz_albumartistid")
            or tags.get("musicbrainz_album_artist_id")
            or ""
        ).strip()
        mbid = file_mbid
        if not mbid and fallback_mbid and fallback_name:
            if album_artist.casefold() == str(fallback_name).strip().casefold():
                mbid = str(fallback_mbid).strip()
            else:
                logger.debug(
                    "artist.nfo: DB artist %r != file ALBUMARTIST %r; "
                    "not using DB MBID fallback", fallback_name, album_artist)

        if artwork is not None:
            try:
                artwork(artist_folder, album_artist, mbid)
            except Exception as exc:  # noqa: BLE001 - artwork is a bonus
                logger.debug("artist artwork skipped for %s: %s", artist_folder, exc)

        if os.path.exists(os.path.join(artist_folder, ARTIST_NFO_FILENAME)):
            return False, "artist.nfo already exists"
        if not mbid:
            return False, "no MusicBrainz album-artist ID in file tags"
        return write_artist_nfo(artist_folder, album_artist, mbid)
    except Exception as exc:  # noqa: BLE001 — never break import
        logger.debug("ensure_artist_nfo_for_track failed for %s: %s", track_path, exc)
        return False, f"error: {exc}"


__all__ = [
    "ARTIST_NFO_FILENAME",
    "SETTING_PATH",
    "build_artist_nfo",
    "read_artist_nfo_mbid",
    "write_artist_nfo",
    "artist_folder_for_track",
    "schedule_artist_artwork",
    "ensure_artist_nfo_for_track",
]
