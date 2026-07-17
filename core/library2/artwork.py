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
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.artwork")


# The import precache and the HTTP slow path may discover the same uncached
# entity at the same time.  Keep one lock boundary in this module (rather than
# an API-only lock) so every caller shares the same single-flight guarantee.
_build_locks: Dict[tuple[str, str, int], tuple[threading.Lock, int]] = {}
_build_locks_guard = threading.Lock()


@contextmanager
def _build_lock(database, kind: str, entity_id: int) -> Iterator[None]:
    """Reference-counted per-entity lock; entries disappear when idle.

    A full first import may touch hundreds of thousands of entities, so a
    permanent lock registry would trade the provider stampede for a memory
    leak.  Counting owners + waiters lets us safely prune without allowing a
    third caller to create a second lock while another waiter still exists.
    """
    key = (str(database.database_path), kind, int(entity_id))
    with _build_locks_guard:
        current = _build_locks.get(key)
        lock, references = current if current is not None else (threading.Lock(), 0)
        _build_locks[key] = (lock, references + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _build_locks_guard:
            current = _build_locks.get(key)
            if current is not None and current[0] is lock:
                references = current[1] - 1
                if references <= 0:
                    _build_locks.pop(key, None)
                else:
                    _build_locks[key] = (lock, references)


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


def _normalize_jpeg_variants(data: bytes, thumb_height: int = 256) -> Optional[tuple[bytes, bytes]]:
    """Validate once and encode the full JPEG plus its list thumbnail.

    Keeping both encodes on the same decoded/transposed RGB image avoids
    reopening and decoding the just-written full JPEG for every entity.  The
    P2-04 format boundary remains unchanged: arbitrary source bytes are fully
    decoded, EXIF-transposed and normalized to RGB JPEG.
    """
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

            thumbnail = image.copy()
            width, height = thumbnail.size
            if height > thumb_height:
                thumbnail = thumbnail.resize(
                    (max(1, int(width * thumb_height / height)), thumb_height),
                    Image.LANCZOS,
                )

            output = BytesIO()
            image.save(output, "JPEG", quality=90, optimize=True)
            thumb_output = BytesIO()
            thumbnail.save(thumb_output, "JPEG", quality=82, optimize=True)
            return output.getvalue(), thumb_output.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.debug("artwork image validation failed: %s", exc)
        return None


def _normalize_jpeg(data: bytes) -> Optional[bytes]:
    """Validate arbitrary image bytes and encode the one cache format: JPEG."""
    variants = _normalize_jpeg_variants(data)
    return variants[0] if variants else None


def _write_thumbnail_bytes(dst: Path, data: bytes) -> None:
    try:
        tmp = dst.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    except OSError as exc:
        logger.debug("thumbnail generation failed for %s: %s", dst, exc)


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
          AND COALESCE(tf.file_state,'active')
              NOT IN ('missing_confirmed','deleted')
        ORDER BY t.track_number, t.id, {primary_order('tf')} LIMIT 5
        """,
        (album_id,),
    ).fetchall()
    for row in rows:
        abs_path = _resolve_abs(row["path"], config_manager)
        # resolve_lib2_path already guarantees an existing path.  Avoid a
        # duplicate stat here; it is especially costly on NAS mounts.  The
        # canonical extractor still performs its own file-type guard.
        if abs_path:
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


_OVERRIDE_ENTITY_TYPE = {"album": "release_group", "artist": "artist"}


def _manual_art_override_url(conn, kind: str, entity_id: int) -> Optional[str]:
    """A user-picked cover (docs §49 art picker) always wins over the
    auto-resolved embedded/provider image — mirrors the legacy picker's "a
    manual pick pins the choice" guarantee, but via the existing
    ``lib2_metadata_overrides`` store (``image_url`` field) instead of a
    parallel pin flag."""
    entity_type = _OVERRIDE_ENTITY_TYPE.get(kind)
    if entity_type is None:
        return None
    try:
        from core.library2.metadata_overrides import get_field_overrides
        overrides = get_field_overrides(conn, entity_type=entity_type, entity_id=entity_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("manual art override lookup failed (%s %s): %s", kind, entity_id, e)
        return None
    override = overrides.get("image_url")
    return str(override.value) if override and override.value else None


def build_artwork(database, conn, config_manager, kind: str, entity_id: int,
                  *, force: bool = False) -> Optional[str]:
    """Resolve + cache artwork for an artist/album; return the on-disk jpg path.

    ``kind`` is 'artist' or 'album'. Returns None when no art could be found.
    Idempotent: a cached file is reused unless ``force`` is set.  Concurrent
    callers for the same database/entity are single-flighted so an immediate
    UI browse cannot duplicate a running background provider/NAS lookup.
    """
    with _build_lock(database, kind, entity_id):
        return _build_artwork_unlocked(
            database, conn, config_manager, kind, entity_id, force=force
        )


def _build_artwork_unlocked(database, conn, config_manager, kind: str, entity_id: int,
                            *, force: bool = False) -> Optional[str]:
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
    override_url = _manual_art_override_url(conn, kind, entity_id)
    if override_url:
        try:
            from core.library.artist_image import download_image_bytes
            data = download_image_bytes(override_url)
        except Exception as e:  # noqa: BLE001
            logger.debug("manual art override download failed (%s %s): %s", kind, entity_id, e)

    provider_attempted = False
    if not data and kind == "album":
        data = _embedded_art_for_album(conn, config_manager, entity_id)
    elif not data and kind == "artist":
        # §52.5: an artist photo is semantically different from an album
        # cover. Prefer the provider's artist image; embedded album art is the
        # resilient fallback for providers/artists without one.
        provider_attempted = True
        url = _provider_art_url(conn, kind, entity_id)
        if url:
            try:
                from core.library.artist_image import download_image_bytes
                data = download_image_bytes(url)
            except Exception as e:  # noqa: BLE001
                logger.debug("provider image download failed: %s", e)

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
            if not data:
                data = _embedded_art_for_album(conn, config_manager, album["id"])

    if not data and not provider_attempted:
        url = _provider_art_url(conn, kind, entity_id)
        if url:
            try:
                from core.library.artist_image import download_image_bytes
                data = download_image_bytes(url)
            except Exception as e:  # noqa: BLE001
                logger.debug("provider image download failed: %s", e)

    if not data:
        return None
    variants = _normalize_jpeg_variants(data)
    if not variants:
        return None
    data, thumbnail = variants
    try:
        tmp = out.with_suffix(".writing")
        tmp.write_bytes(data)
        os.replace(tmp, out)
        _write_thumbnail_bytes(thumb_file(database, kind, entity_id), thumbnail)
        return str(out)
    except OSError as e:
        logger.debug("artwork write failed (%s %s): %s", kind, entity_id, e)
        return None


def _precache_max_workers(config_manager, default: int = 6) -> int:
    """Return bounded artwork concurrency.

    Artwork is predominantly independent NAS/network I/O, unlike the heavier
    auto-import pipeline.  It therefore gets a dedicated optional knob and a
    higher default, while retaining ``auto_import.max_workers`` as a backwards-
    compatible fallback for installations that already tuned it.  The hard cap
    prevents an accidental setting from stampeding providers or a NAS.
    """
    if config_manager is None:
        return default
    try:
        configured = config_manager.get("library_v2.artwork_cache_workers", None)
        if configured is None:
            configured = config_manager.get("auto_import.max_workers", default)
        return min(16, max(1, int(configured)))
    except Exception:  # noqa: BLE001
        return default


def precache_all_artwork(database, config_manager, *, progress=None) -> Dict[str, int]:
    """Resolve + cache artwork for every artist and album to local disk.

    Runs in the background after an import so the UI serves covers from disk
    (fast) instead of resolving on first view (Lidarr-style). Embedded covers are
    cheap; provider lookups are the slow part (one network call per uncached
    entity), so this dispatches to a bounded ``ThreadPoolExecutor`` — same
    pattern/config key as ``core.auto_import_worker`` — instead of resolving
    one artist/album at a time. Artists/albums already cached are skipped.
    Returns counts. Never raises.
    """
    counts = {"artists": 0, "albums": 0}
    try:
        conn = database._get_connection()
    except Exception:  # noqa: BLE001
        return counts
    try:
        artist_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_artists")]
        album_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_albums")]
    except Exception as e:  # noqa: BLE001
        logger.debug("artwork precache error: %s", e)
        return counts
    finally:
        conn.close()

    total = len(artist_ids) + len(album_ids)
    # Compute cache paths from one directory snapshot.  artwork_file() ensures
    # the directory exists on every call; doing that thousands of times here is
    # unnecessary filesystem work even when every image is already cached.
    cache_dir = artwork_dir(database)
    pending = [
        (kind, eid)
        for kind, ids in (("album", album_ids), ("artist", artist_ids))
        for eid in ids
        if not (cache_dir / f"{kind}_{int(eid)}.jpg").exists()
    ]
    progress_lock = threading.Lock()
    done = [total - len(pending)]
    if progress:
        progress("artwork", done[0], total)

    def _build_one(kind: str, eid: int) -> bool:
        try:
            thread_conn = database._get_connection()
        except Exception:  # noqa: BLE001
            return False
        try:
            return bool(build_artwork(database, thread_conn, config_manager, kind, eid))
        except Exception as e:  # noqa: BLE001
            logger.debug("artwork precache build failed (%s %s): %s", kind, eid, e)
            return False
        finally:
            thread_conn.close()

    try:
        if pending:
            max_workers = min(len(pending), _precache_max_workers(config_manager))
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="Lib2Artwork"
            ) as executor:
                futures = {
                    executor.submit(_build_one, kind, eid): kind for kind, eid in pending
                }
                for future in as_completed(futures):
                    if future.result():
                        counts[futures[future] + "s"] += 1
                    with progress_lock:
                        done[0] += 1
                        if progress and (done[0] % 25 == 0 or done[0] == total):
                            progress("artwork", done[0], total)
    except Exception as e:  # noqa: BLE001
        logger.debug("artwork precache error: %s", e)
    if progress:
        progress("artwork", total, total)
    logger.info("Library v2 artwork precache: %s", counts)
    return counts


def apply_manual_artwork(
    database, conn, kind: str, entity_id: int, url: str, *, profile_id: int = 1,
) -> bool:
    """Apply a user-picked cover (docs §49 art picker).

    Downloads + validates ``url`` FIRST, then pins it as the entity's
    ``image_url`` metadata override (so a later "Refresh & Scan"/precache
    pass — which calls :func:`build_artwork` again — sees it via
    :func:`_manual_art_override_url` and won't clobber the pick with the
    auto-resolved embedded/provider image), then writes it straight into the
    managed artwork cache file so it's visible immediately, without waiting
    for a background rebuild.

    Returns False (no override set, no write) when the URL doesn't resolve
    to a valid image — the caller surfaces that as a user-facing error.
    Raises :class:`~core.library2.metadata_overrides.MetadataOverrideError`
    when the entity itself doesn't exist (propagated from ``set_field_override``).
    """
    entity_type = _OVERRIDE_ENTITY_TYPE.get(kind)
    if entity_type is None:
        return False

    from core.library.artist_image import download_image_bytes
    data = download_image_bytes(url)
    if not data:
        return False
    variants = _normalize_jpeg_variants(data)
    if not variants:
        return False
    data, thumbnail = variants

    from core.library2.metadata_overrides import set_field_override
    set_field_override(
        conn, entity_type=entity_type, entity_id=entity_id,
        field_name="image_url", value=url, profile_id=profile_id,
        reason="manual cover pick",
    )

    # Serialize the final cache replacement with background/on-demand builds.
    # The override is already persisted on this connection, so whichever build
    # follows will also resolve to the user's pinned URL.
    with _build_lock(database, kind, entity_id):
        out = artwork_file(database, kind, entity_id)
        try:
            tmp = out.with_suffix(".writing")
            tmp.write_bytes(data)
            os.replace(tmp, out)
            _write_thumbnail_bytes(thumb_file(database, kind, entity_id), thumbnail)
        except OSError as e:
            logger.debug("manual artwork write failed (%s %s): %s", kind, entity_id, e)
            return False
    return True


__all__ = [
    "build_artwork",
    "apply_manual_artwork",
    "artwork_file",
    "thumb_file",
    "artwork_dir",
    "is_cached_jpeg",
    "precache_all_artwork",
]
