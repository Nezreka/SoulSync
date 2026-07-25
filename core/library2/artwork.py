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
import socket
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urljoin, urlsplit

from utils.logging_config import get_logger

logger = get_logger("library2.artwork")

MAX_REMOTE_ARTWORK_BYTES = 10 * 1024 * 1024
MAX_ARTWORK_PIXELS = 40_000_000
MAX_ARTWORK_DIMENSION = 12_000
MAX_ARTWORK_REDIRECTS = 4


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


_entity_versions: Dict[tuple[str, str, int], tuple[int, int]] = {}
_entity_generations: Dict[tuple[str, str, int], int] = {}
_entity_versions_guard = threading.Lock()


def forget_artwork_versions(database, kind: Optional[str] = None, entity_id: Optional[int] = None) -> None:
    """Drop cached mtimes so the next lookup re-stats from disk.

    rev25-09: every drop also bumps that entity's own generation marker,
    checked (under the same lock) by any lookup for the *same entity* that's
    mid-stat when it happens.  A lookup only commits its result to the cache
    if its entity's marker is still what it was when the lookup started — so
    a write racing a concurrent read can never have its invalidation silently
    overwritten by the read's now-stale value, the way a pre-scandir
    directory-mtime snapshot could.  The marker is per entity (not a single
    shared counter) so invalidating one entity can't force every other
    already-cached entity on the same page to re-stat too.

    With ``kind``/``entity_id`` given, drops only that one entry (the normal
    case — every managed write already knows exactly what it touched).
    Without them, drops every entry for this database (bulk operations, e.g.
    a full rescan moving the whole cache directory).
    """
    with _entity_versions_guard:
        if kind is not None and entity_id is not None:
            key = (str(database.database_path), kind, int(entity_id))
            _entity_generations[key] = _entity_generations.get(key, 0) + 1
            _entity_versions.pop(key, None)
        else:
            prefix = str(database.database_path)
            for cache_key in {k for k in _entity_versions if k[0] == prefix} | {
                k for k in _entity_generations if k[0] == prefix
            }:
                _entity_generations[cache_key] = _entity_generations.get(cache_key, 0) + 1
                _entity_versions.pop(cache_key, None)


def artwork_version(database, kind: str, entity_id: int) -> int:
    """Cache-bust token for one entity's artwork, or 0 when it isn't cached.

    rev25-03: a whole-directory scan (the previous implementation) costs more
    syscalls than the handful of entities any single caller actually needs —
    two files per artist/album means tens of thousands of directory entries on
    a large library, and every successful build now forgets the snapshot from
    several call sites (list rendering, autolink, discography-expand), so the
    "reused across renders" premise rarely holds anymore.  Statting only the
    one file this call needs, cached per entity until its own write
    invalidates it, keeps the win without the inversion.
    """
    key = (str(database.database_path), kind, int(entity_id))
    with _entity_versions_guard:
        cached = _entity_versions.get(key)
        marker = _entity_generations.get(key, 0)
    if cached is not None and cached[0] == marker:
        return cached[1]
    try:
        mtime = int(artwork_file(database, kind, entity_id).stat().st_mtime)
    except OSError:
        mtime = 0
    with _entity_versions_guard:
        if _entity_generations.get(key, 0) == marker:
            _entity_versions[key] = (marker, mtime)
    return mtime


def _cached_artwork_filenames(directory: Path) -> set[str]:
    """One-shot bulk listing of every cached ``.jpg`` filename.

    rev25-14: kept as the *one* directory-scan implementation, used only by
    :func:`precache_all_artwork` — a full-library pass over tens of thousands
    of entities is the one case where a single scan legitimately beats a stat
    per entity.  Every other caller wants a handful of specific entities and
    uses :func:`artwork_version` instead.
    """
    try:
        with os.scandir(directory) as entries:
            return {entry.name for entry in entries if entry.name.endswith(".jpg")}
    except OSError as exc:
        logger.debug("artwork directory listing failed for %s: %s", directory, exc)
        return set()


def invalidate_artwork(database, kind: str, entity_id: int) -> int:
    """Remove both managed cache variants for one entity.

    Repair jobs may rewrite embedded art outside the Library-v2 API.  Keeping
    this invalidation in the artwork module gives those bridges the same
    full+thumbnail cache-bust boundary as native refreshes.  The next request
    rebuilds from embedded art/provider fallback.  Missing files are a no-op.
    """
    removed = 0
    with _build_lock(database, kind, int(entity_id)):
        for path in (
            artwork_file(database, kind, int(entity_id)),
            thumb_file(database, kind, int(entity_id)),
        ):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.debug("artwork invalidation failed for %s: %s", path, exc)
    forget_artwork_versions(database, kind, int(entity_id))
    return removed


def is_cached_jpeg(path: Path) -> bool:
    """Cheap fast-path guard for old caches that stored PNG/WEBP as .jpg."""
    try:
        with path.open("rb") as handle:
            return handle.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _public_remote_artwork_url(url: str) -> bool:
    """Allow only HTTP(S) targets whose complete DNS answer is public."""
    from ipaddress import ip_address

    try:
        parsed = urlsplit(str(url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = {
            item[4][0].split("%", 1)[0]
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
        return bool(addresses) and all(ip_address(value).is_global for value in addresses)
    except (OSError, TypeError, ValueError):
        return False


def _response_peer_is_public(response: Any) -> bool:
    """Reject a DNS-rebound connection when Requests exposes its peer socket."""
    from ipaddress import ip_address

    raw = getattr(response, "raw", None)
    connection = getattr(raw, "_connection", None) or getattr(raw, "connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        return True  # URL DNS validation remains the portable baseline.
    try:
        peer = str(sock.getpeername()[0]).split("%", 1)[0]
        return ip_address(peer).is_global
    except (OSError, TypeError, ValueError):
        return False


def _download_remote_artwork(
    url: str,
    *,
    max_bytes: int = MAX_REMOTE_ARTWORK_BYTES,
) -> Optional[bytes]:
    """Fetch public artwork with a bounded, revalidated redirect walk."""
    import requests

    current = str(url or "").strip()
    limit = max(1, int(max_bytes))
    for _redirect in range(MAX_ARTWORK_REDIRECTS + 1):
        if not _public_remote_artwork_url(current):
            return None
        response = None
        try:
            response = requests.get(
                current,
                timeout=(3.05, 15),
                stream=True,
                allow_redirects=False,
                headers={"Accept": "image/*"},
            )
            if not _response_peer_is_public(response):
                return None
            if response.status_code in {301, 302, 303, 307, 308}:
                location = str(response.headers.get("Location") or "").strip()
                if not location:
                    return None
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                return None
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("image/"):
                return None
            try:
                declared_size = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size < 0 or declared_size > limit:
                return None
            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > limit:
                    return None
            return bytes(body) if body else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("remote artwork fetch failed: %s", exc)
            return None
        finally:
            if response is not None:
                response.close()
    return None


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
            width, height = image.size
            if (
                width <= 0 or height <= 0
                or width > MAX_ARTWORK_DIMENSION
                or height > MAX_ARTWORK_DIMENSION
                or width * height > MAX_ARTWORK_PIXELS
            ):
                return None
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

            # rev25-13: both variants pay the extra entropy pass.  The full
            # variant is not list-view-only — it's requested on every artist/
            # album detail-page visit and in the cover-pick match dialogs — so
            # the CPU cost (paid once, in a background thread, when the image
            # is first cached) buys back real bytes on every one of those
            # future serves plus a smaller permanent on-disk footprint.
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
            data = _download_remote_artwork(override_url)
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
                data = _download_remote_artwork(url)
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
                data = _download_remote_artwork(url)
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
        forget_artwork_versions(database, kind, entity_id)
        return str(out)
    except OSError as e:
        logger.debug("artwork write failed (%s %s): %s", kind, entity_id, e)
        return None


_background_executor: Optional[ThreadPoolExecutor] = None
_background_executor_workers: int = 0
_background_inflight: set = set()
_background_guard = threading.Lock()
# rev25-08: submit() has no native queue bound — a client abusing the artwork
# endpoint, or repeated renders of a page full of uncached covers, could grow
# the pending queue faster than the pool drains it, each entry eventually
# opening its own DB connection when it runs.  Beyond this many
# scheduled-or-running entities, new requests fall back to the placeholder
# contract instead of queuing (dropped, not queued without bound).
_MAX_BACKGROUND_QUEUE = 500


def shutdown_background_executor() -> None:
    """Best-effort teardown for the background artwork pool.

    rev25-08: ``ThreadPoolExecutor`` registers a non-daemon ``threading``
    atexit hook that joins its worker threads — an in-flight provider
    download stuck on a slow socket used to delay interpreter exit, and with
    it the container's response to SIGTERM.  Called from the app's own
    shutdown handler, alongside every other pool in ``web_server.py``'s
    ``_shutdown_runtime_components``.  A no-op if the pool was never created.
    Also clears the module reference, so a stray scheduling call afterwards
    builds a fresh pool instead of hitting the shut-down one's ``RuntimeError``.
    """
    global _background_executor
    with _background_guard:
        executor = _background_executor
        _background_executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def schedule_artwork_build(database, config_manager, kind: str, entity_id: int):
    """Resolve one entity's artwork off the caller's thread (perf25-02).

    The cold path costs a provider walk, an HTTP download and two JPEG encodes;
    doing that inside the artwork request pins a web worker per uncached image
    on a page.  Callers instead answer with the placeholder contract right away
    and let this bounded pool fill the cache for the next render.

    Returns the scheduled :class:`~concurrent.futures.Future`, or ``None`` when
    a build for the same entity is already queued/running, or the background
    queue is already saturated.  ``build_artwork`` still owns the per-entity
    single-flight lock, so a concurrent HTTP or precache build can never be
    duplicated either.
    """
    global _background_executor, _background_executor_workers
    key = (str(database.database_path), kind, int(entity_id))
    with _background_guard:
        if key in _background_inflight:
            return None
        if len(_background_inflight) >= _MAX_BACKGROUND_QUEUE:
            logger.debug(
                "background artwork queue full (%d), dropping %s %s",
                len(_background_inflight), kind, entity_id,
            )
            return None
        desired_workers = _precache_max_workers(config_manager)
        if _background_executor is None:
            _background_executor = ThreadPoolExecutor(
                max_workers=desired_workers,
                thread_name_prefix="Lib2ArtworkBg",
            )
            _background_executor_workers = desired_workers
        elif desired_workers != _background_executor_workers and not _background_inflight:
            # rev25-08: the pool's worker count was frozen from whichever
            # caller happened to construct it first — a later change to
            # ``library_v2.artwork_cache_workers``/``auto_import.max_workers``
            # never took effect without a process restart, unlike
            # precache_all_artwork, which reads fresh on every run. Nothing is
            # queued/running right now, so it's safe to replace the pool.
            _background_executor.shutdown(wait=False)
            _background_executor = ThreadPoolExecutor(
                max_workers=desired_workers,
                thread_name_prefix="Lib2ArtworkBg",
            )
            _background_executor_workers = desired_workers
        executor = _background_executor
        _background_inflight.add(key)

    def _run() -> bool:
        # rev25-01: the key must be released no matter which step fails —
        # including connection acquisition itself.  A single finally around
        # the whole body (rather than a separate try/except just for
        # _get_connection) is what guarantees that; a transient connection
        # error used to leak the key and pin this entity to its placeholder
        # for the rest of the process's life.
        conn = None
        try:
            try:
                conn = database._get_connection()
            except Exception as exc:  # noqa: BLE001
                logger.debug("background artwork connection failed: %s", exc)
                return False
            try:
                return bool(
                    build_artwork(database, conn, config_manager, kind, int(entity_id))
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "background artwork build failed (%s %s): %s", kind, entity_id, exc
                )
                return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("background artwork connection close failed: %s", exc)
            # Released inside the worker, not in a done-callback: the entity
            # must be schedulable again the moment its build is over, and a
            # callback can still be pending when the future already resolved.
            with _background_guard:
                _background_inflight.discard(key)

    try:
        return executor.submit(_run)
    except RuntimeError as exc:  # interpreter/executor shutdown
        logger.debug("background artwork scheduling failed: %s", exc)
        with _background_guard:
            _background_inflight.discard(key)
        return None


def schedule_missing_artwork(database, config_manager, targets) -> int:
    """Warm the artwork cache for entities that just joined the library.

    perf25-04/rev25-04: the batch precache job only knows the state of its
    last run, so an artist added by a finished download or a freshly expanded
    discography stayed cold until someone browsed it.  Callers pass
    ``(kind, entity_id)`` pairs after their own commit — typically one album
    plus its artist, never the whole library — so each target is checked with
    its own stat via :func:`artwork_version` instead of a full directory scan;
    a per-download import used to re-scandir the entire cache directory here,
    which is exactly the blocking I/O perf25-04 was meant to remove.  The rest
    are queued on the same bounded background pool.  Never raises — artwork is
    presentation data.
    """
    scheduled = 0
    try:
        seen = set()
        for kind, entity_id in targets or ():
            try:
                key = (str(kind), int(entity_id))
            except (TypeError, ValueError):
                continue
            if key in seen or artwork_version(database, key[0], key[1]) > 0:
                continue
            seen.add(key)
            if schedule_artwork_build(database, config_manager, key[0], key[1]) is not None:
                scheduled += 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("eager artwork scheduling failed: %s", exc)
    return scheduled


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
    # rev25-14: one bulk directory listing instead of a per-entity exists()
    # check — a real library's artist+album count can run into the tens of
    # thousands, and this is the one place that's actually cheaper than the
    # per-entity path every other caller uses (see artwork_version).
    cached_names = _cached_artwork_filenames(artwork_dir(database))
    pending = [
        (kind, eid)
        for kind, ids in (("album", album_ids), ("artist", artist_ids))
        for eid in ids
        if f"{kind}_{int(eid)}.jpg" not in cached_names
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

    data = _download_remote_artwork(url)
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
    forget_artwork_versions(database, kind, entity_id)
    return True


__all__ = [
    "build_artwork",
    "apply_manual_artwork",
    "artwork_file",
    "artwork_version",
    "forget_artwork_versions",
    "thumb_file",
    "artwork_dir",
    "invalidate_artwork",
    "is_cached_jpeg",
    "precache_all_artwork",
    "schedule_artwork_build",
    "schedule_missing_artwork",
    "shutdown_background_executor",
]
