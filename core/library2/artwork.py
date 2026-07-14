"""Media-server-independent artwork for Library v2 (Lidarr-MediaCover style).

Artwork is resolved WITHOUT any media server and cached on local disk:

1. **Embedded cover (primary)** — read the cover embedded in one of the entity's
   own audio files (``core/metadata/art_apply.extract_embedded_art``). Every track
   SoulSync tags carries embedded art, so this works for a pure-SoulSync install
   with no Plex/Jellyfin/Navidrome.
2. **Provider fallback** — artist images (which files rarely embed) come from the
   metadata providers via the stored external IDs
   (``core/metadata/artist_image.get_artist_image_url``).

Resolved bytes are written once into a managed cache dir next to the database
(``<db_dir>/lib2_artwork/<kind>_<id>.jpg``) and served by the
``/api/library/v2/artwork/<kind>/<id>`` endpoint. Nothing here ever touches a media
server. Never raises — callers get ``None``/placeholder behaviour on failure.
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.artwork")


def artwork_dir(database) -> Path:
    d = Path(database.database_path).parent / "lib2_artwork"
    d.mkdir(parents=True, exist_ok=True)
    return d


def artwork_file(database, kind: str, entity_id: int) -> Path:
    return artwork_dir(database) / f"{kind}_{int(entity_id)}.jpg"


def thumb_file(database, kind: str, entity_id: int) -> Path:
    return artwork_dir(database) / f"{kind}_{int(entity_id)}_t.jpg"


def is_cached_jpeg(path: Path) -> bool:
    """Cheap fast-path guard for old caches that stored PNG/WEBP as .jpg."""
    try:
        with path.open("rb") as handle:
            return handle.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _normalize_jpeg(data: bytes) -> Optional[bytes]:
    """Validate arbitrary image bytes and encode the one cache format: JPEG."""
    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(data)) as image:
            image.load()
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, "JPEG", quality=90, optimize=True)
            return output.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.debug("artwork image validation failed: %s", exc)
        return None


def _write_thumbnail(src: Path, dst: Path, height: int = 256) -> None:
    """Write a small JPEG thumbnail (Lidarr-style) for fast list rendering."""
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = im.size
            if h > height:
                im = im.resize((max(1, int(w * height / h)), height), Image.LANCZOS)
            tmp = dst.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=82, optimize=True)
            os.replace(tmp, dst)
    except Exception as e:  # noqa: BLE001
        logger.debug("thumbnail generation failed for %s: %s", src, e)


def _resolve_abs(file_path: str, config_manager) -> Optional[str]:
    try:
        from core.library2.paths import resolve_lib2_path
        return resolve_lib2_path(file_path, config_manager=config_manager)
    except Exception as e:  # noqa: BLE001
        logger.debug("path resolve failed for %s: %s", file_path, e)
        return None


def _embedded_art_for_album(conn, config_manager, album_id: int) -> Optional[bytes]:
    """Extract embedded cover from any track file belonging to the album."""
    from core.library2.track_files import primary_order
    from core.metadata.art_apply import extract_embedded_art
    rows = conn.execute(
        f"""
        SELECT tf.path FROM lib2_track_files tf
        JOIN lib2_tracks t ON t.id = tf.track_id
        WHERE t.album_id = ? AND tf.path IS NOT NULL
        ORDER BY t.track_number, t.id, {primary_order('tf')} LIMIT 5
        """,
        (album_id,),
    ).fetchall()
    for row in rows:
        abs_path = _resolve_abs(row["path"], config_manager)
        if abs_path and os.path.exists(abs_path):
            data = extract_embedded_art(abs_path)
            if data:
                return data
    return None


def _provider_art_url(conn, kind: str, entity_id: int) -> Optional[str]:
    """Best-effort provider image URL from stored external IDs / names (no media
    server). Artists use the artist-image resolver; albums use the cover-art
    lookup across whatever providers are available (CAA/Deezer/iTunes/Spotify…)."""
    try:
        if kind == "artist":
            row = conn.execute(
                "SELECT id, spotify_id, musicbrainz_id, external_ids, name "
                "FROM lib2_artists WHERE id=?",
                (entity_id,),
            ).fetchone()
            if not row:
                return None
            source_ids = _source_ids(row["external_ids"])
            if row["spotify_id"]:
                source_ids["spotify"] = row["spotify_id"]
            if row["musicbrainz_id"]:
                source_ids["musicbrainz"] = row["musicbrainz_id"]
            from core.library2.metadata_overrides import project_metadata
            effective, _overrides = project_metadata(
                conn,
                entity_type="artist",
                entity_id=row["id"],
                provider_fields=dict(row),
            )
            from core.library2.provider_adapters import fetch_artwork_url
            result = fetch_artwork_url(
                "artist",
                artist_name=effective["name"],
                source_ids=source_ids,
            )
            return result.url if result else None
        elif kind == "album":
            row = conn.execute(
                """SELECT al.id, al.title, al.spotify_id, al.musicbrainz_id,
                          al.external_ids, ar.id AS artist_id,
                          ar.name AS artist_name,
                          ed.spotify_id AS edition_spotify_id,
                          ed.musicbrainz_id AS edition_musicbrainz_id,
                          ed.external_ids AS edition_external_ids
                   FROM lib2_albums al JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                   LEFT JOIN lib2_release_editions ed
                          ON ed.release_group_id=al.id AND ed.is_default=1
                   WHERE al.id = ?""",
                (entity_id,),
            ).fetchone()
            if not row:
                return None
            source_ids = _source_ids(row["external_ids"])
            source_ids.update(_source_ids(row["edition_external_ids"]))
            for source, value in (
                ("spotify", row["edition_spotify_id"] or row["spotify_id"]),
                ("musicbrainz", row["edition_musicbrainz_id"] or row["musicbrainz_id"]),
            ):
                if value:
                    source_ids[source] = value
            from core.library2.metadata_overrides import project_metadata
            album_effective, _album_overrides = project_metadata(
                conn,
                entity_type="release_group",
                entity_id=row["id"],
                provider_fields=dict(row),
            )
            artist_effective, _artist_overrides = project_metadata(
                conn,
                entity_type="artist",
                entity_id=row["artist_id"],
                provider_fields={"name": row["artist_name"]},
            )
            from core.library2.provider_adapters import fetch_artwork_url
            result = fetch_artwork_url(
                "album",
                artist_name=artist_effective["name"],
                album_title=album_effective["title"],
                source_ids=source_ids,
            )
            return result.url if result else None
    except Exception as e:  # noqa: BLE001
        logger.debug("provider art lookup failed (%s %s): %s", kind, entity_id, e)
    return None


def _source_ids(raw: Any) -> Dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip().lower(): str(provider_id).strip()
        for source, provider_id in value.items()
        if str(source).strip() and str(provider_id).strip()
    }


def build_artwork(database, conn, config_manager, kind: str, entity_id: int,
                  *, force: bool = False) -> Optional[str]:
    """Resolve + cache artwork for an artist/album; return the on-disk jpg path.

    ``kind`` is 'artist' or 'album'. Returns None when no art could be found.
    Idempotent: a cached file is reused unless ``force`` is set.
    """
    out = artwork_file(database, kind, entity_id)
    if out.exists() and not force:
        if is_cached_jpeg(out):
            return str(out)
        # Self-heal caches created before artwork bytes were normalized.
        try:
            out.unlink()
            old_thumb = thumb_file(database, kind, entity_id)
            if old_thumb.exists():
                old_thumb.unlink()
        except OSError:
            return None
    if force and out.exists():
        try:
            out.unlink()
        except OSError:
            pass

    data: Optional[bytes] = None
    if kind == "album":
        data = _embedded_art_for_album(conn, config_manager, entity_id)
    elif kind == "artist":
        # Artists: prefer the embedded cover of one of their albums (fast, local),
        # then fall back to a provider image via external IDs.
        album = conn.execute(
            """
            SELECT al.id FROM lib2_album_artists aa
            JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id = ?
            ORDER BY (al.album_type <> 'single') DESC, al.year DESC LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if album:
            data = _embedded_art_for_album(conn, config_manager, album["id"])

    if not data:
        url = _provider_art_url(conn, kind, entity_id)
        if url:
            try:
                from core.library.artist_image import download_image_bytes
                data = download_image_bytes(url)
            except Exception as e:  # noqa: BLE001
                logger.debug("provider image download failed: %s", e)

    if not data:
        return None
    data = _normalize_jpeg(data)
    if not data:
        return None
    try:
        tmp = out.with_suffix(".writing")
        tmp.write_bytes(data)
        os.replace(tmp, out)
        _write_thumbnail(out, thumb_file(database, kind, entity_id))
        return str(out)
    except OSError as e:
        logger.debug("artwork write failed (%s %s): %s", kind, entity_id, e)
        return None


def precache_all_artwork(database, config_manager, *, progress=None) -> Dict[str, int]:
    """Resolve + cache artwork for every artist and album to local disk.

    Runs in the background after an import so the UI serves covers from disk
    (fast) instead of resolving on first view (Lidarr-style). Embedded covers are
    cheap; provider lookups are the slow part, so artists/albums already cached are
    skipped. Returns counts. Never raises.
    """
    counts = {"artists": 0, "albums": 0}
    try:
        conn = database._get_connection()
    except Exception:  # noqa: BLE001
        return counts
    try:
        artist_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_artists")]
        album_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_albums")]
        total = len(artist_ids) + len(album_ids)
        done = 0
        for kind, ids in (("album", album_ids), ("artist", artist_ids)):
            for eid in ids:
                if artwork_file(database, kind, eid).exists():
                    done += 1
                    continue
                if build_artwork(database, conn, config_manager, kind, eid):
                    counts[kind + "s"] += 1
                done += 1
                if progress and done % 25 == 0:
                    progress("artwork", done, total)
    except Exception as e:  # noqa: BLE001
        logger.debug("artwork precache error: %s", e)
    finally:
        conn.close()
    logger.info("Library v2 artwork precache: %s", counts)
    return counts


__all__ = [
    "build_artwork",
    "artwork_file",
    "thumb_file",
    "artwork_dir",
    "is_cached_jpeg",
    "precache_all_artwork",
]
