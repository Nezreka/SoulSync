"""Match downloaded release files before metadata writes or library publication.

Release/indexer hints never replace the ordinary per-file import checks. A bonus
album needs a complete catalogue track list and an unambiguous one-to-one map.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from core.imports.filename import parse_filename_metadata
from core.tag_writer import read_file_tags
from core.text.title_match import recording_version_markers
from mutagen import File as MutagenFile
from utils.logging_config import get_logger

logger = get_logger("downloads.release_import")

AUDIO_EXTENSIONS = {".flac", ".ape", ".wav", ".alac", ".dsf", ".dff", ".aiff", ".aif", ".opus", ".ogg", ".m4a", ".aac", ".mp3", ".wma"}


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    track_number: int = 0
    disc_number: int = 0
    duration_ms: float = 0
    tagged: bool = False


def _text(value: Any) -> str:
    return "".join(c.casefold() for c in str(value or "") if c.isalnum())


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return default


def read_release_file(path: str) -> ReleaseFile:
    """Read existing tags before the importer replaces them with matched data."""
    tags = read_file_tags(path)
    duration = 0
    try:
        audio = MutagenFile(path)
        duration = float(getattr(getattr(audio, "info", None), "length", 0) or 0) * 1000
    except Exception as exc:
        logger.debug("Release duration is unavailable for %s: %s", path, exc)
    parsed = parse_filename_metadata(path)
    return ReleaseFile(
        str(path),
        str(tags.get("title") or parsed.get("title") or Path(path).stem).replace("_", " "),
        str(tags.get("artist") or tags.get("album_artist") or parsed.get("artist") or ""),
        str(tags.get("album") or ""),
        _number(tags.get("track_number")),
        _number(tags.get("disc_number")),
        duration,
        not tags.get("error") and bool(tags.get("title")),
    )


def _artist(track: dict) -> str:
    artists = track.get("artists") or []
    if isinstance(artists, str):
        return artists
    if isinstance(artists, dict):
        return str(artists.get("name") or "")
    if artists:
        first = artists[0]
        return str(first.get("name") or "") if isinstance(first, dict) else str(first)
    return str(track.get("artist") or track.get("artist_name") or "")


def _title(value: str) -> str:
    edition = r"(?:\d{4}[ -]+)?(?:remaster(?:ed)?|mono|stereo)(?:[ -]+\d{4})?"
    suffix = rf"\s*(?:[\[(]{edition}[\])]|[-–]\s*{edition})\s*$"
    return _text(re.sub(suffix, "", value.replace("_", " "), flags=re.IGNORECASE))


def release_match_score(item: ReleaseFile, track: dict) -> float:
    expected = str(track.get("name") or track.get("title") or "")
    if recording_version_markers(expected) != recording_version_markers(item.title):
        return 0.0
    wanted, actual = _title(expected), _title(item.title)
    if not wanted or not actual:
        return 0.0
    title_score = SequenceMatcher(None, wanted, actual).ratio()
    artist = _artist(track)
    if artist and item.artist:
        from core.matching.artist_aliases import artist_names_match

        matched, _ = artist_names_match(
            artist,
            item.artist,
            threshold=0.80,
            aliases=track.get("artist_aliases"),
            similarity=lambda a, b: SequenceMatcher(None, _text(a), _text(b)).ratio(),
        )
        if not matched:
            return 0.0
    duration = float(track.get("duration_ms") or 0)
    if duration and item.duration_ms and abs(duration - item.duration_ms) > max(4000, duration * 0.05):
        return 0.0
    return title_score


def select_requested_file(files: list[ReleaseFile], track: dict) -> ReleaseFile | None:
    """Refuse close alternatives rather than apply one song's tags to another."""
    unique = {str(Path(item.path).resolve()): item for item in files}
    scored = sorted(((release_match_score(item, track), item) for item in unique.values()), key=lambda entry: entry[0], reverse=True)
    if not scored or scored[0][0] < 0.80:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.03:
        return None
    return scored[0][1]


def match_complete_album(files: list[ReleaseFile], tracks: list[dict], album_name: str):
    """Return catalogue/file pairs only for a complete, precisely identified edition."""
    files = list({str(Path(item.path).resolve()): item for item in files}.values())
    if len(tracks) < 2 or len(files) != len(tracks) or not _text(album_name):
        return None
    if any(not item.tagged or _text(item.album) != _text(album_name) for item in files):
        return None
    result, used = [], set()
    for index, track in enumerate(tracks, 1):
        if _text(_artist(track)) in {"", "unknown", "unknownartist", "variousartists", "none", "null"}:
            return None
        disc = _number(track.get("disc_number"), 1)
        number = _number(track.get("track_number"), index)
        candidates = [item for item in files if item.path not in used and item.track_number == number and (item.disc_number or 1) == disc]
        item = select_requested_file(candidates, track)
        if item is None or release_match_score(item, track) < 0.95:
            return None
        used.add(item.path)
        result.append((track, item))
    return result


# Striped locks bound memory and serialize concurrent expansions of the same
# library/album. The ordinary requested-track pipeline still runs independently.
_album_locks = [threading.Lock() for _ in range(32)]


def move_without_replace(source: str, destination: str, *, on_created=None) -> None:
    """Publish exclusively; a concurrent download must never lose its file."""
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        # SMB may not support hard links. Exclusive creation still protects it.
        created = False
        try:
            with open(source, "rb") as src, open(destination, "xb") as dst:
                created = True
                if on_created:
                    on_created(destination)
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            shutil.copystat(source, destination)
        except Exception:
            if created:
                Path(destination).unlink(missing_ok=True)
            raise
    else:
        try:
            if on_created:
                on_created(destination)
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise
    try:
        os.unlink(source)
    except Exception:
        # The publisher cannot record a move that raised. Remove only the
        # destination this call created exclusively, leaving its source intact.
        try:
            Path(destination).unlink()
        except OSError as cleanup_error:
            logger.error("Owned release destination could not be rolled back: %s", cleanup_error)
        raise


def _album_context(parent: dict, track: dict, album: dict, profile: dict, source: str) -> dict:
    """Build clean per-track metadata; never inherit another song's IDs/flags."""
    from core.imports.context import get_import_context_artist
    from core.imports.paths import import_profile_id

    artists = track.get("artists") or [get_import_context_artist(parent)]
    artist = deepcopy(artists[0] if isinstance(artists, list) else artists)
    if not isinstance(artist, dict):
        artist = {"name": str(artist)}
    info = deepcopy(track)
    info["quality_profile_id"] = profile.get("id") or parent.get("track_info", {}).get("quality_profile_id")
    info["album"] = album.get("name") or ""
    ctx = {
        "artist": artist,
        "album": deepcopy(album),
        "track_info": info,
        "source": source,
        "profile_id": import_profile_id(parent),
        "_quality_profile": deepcopy(profile),
        "is_album_download": True,
        "has_clean_metadata": True,
        "has_full_metadata": True,
        "original_search_result": {
            "id": info.get("id") or "",
            "source": source,
            "clean_title": info.get("name") or info.get("title") or "",
            "clean_artist": artist.get("name") or "",
            "clean_album": info["album"],
            "album": info["album"],
            "track_number": info.get("track_number"),
            "disc_number": info.get("disc_number"),
            "duration_ms": info.get("duration_ms"),
        },
    }
    # Source labels are keyed by download username, independently of metadata
    # provider. Carry safe origin breadcrumbs, never the requested song's IDs.
    original = parent.get("original_search_result") or parent.get("search_result") or {}
    username = parent.get("_download_username") or original.get("username")
    if username:
        ctx["_download_username"] = username
        ctx["original_search_result"]["username"] = username
    for key in ("filename", "indexer_id", "indexer_name"):
        if original.get(key) is not None:
            ctx["original_search_result"][key] = deepcopy(original[key])
    from core.downloads.origin import derive_download_origin

    origin, origin_context = derive_download_origin(parent)
    if origin:
        info["_dl_origin"] = origin
        info["_dl_origin_context"] = origin_context
    ctx["_release_defer_events"] = True
    return ctx


def try_complete_album_import(
    context_key: str,
    context: dict,
    files: list[ReleaseFile],
    requested: ReleaseFile,
    transfer_dir: str,
    process_file,
    *,
    lookup_album=None,
    publish=None,
    is_cancelled=None,
    automation_engine=None,
):
    """Opt-in expansion. None means the caller should import its requested file.

    Preflight runs the existing import checks on private copies. All tracks then
    run the complete normal pipeline into staging. Only a fully finished tree is
    marked recoverable and published; failed checks leave the requested import
    free to proceed and never mutate the client's original release.
    """
    from core.imports.pipeline import _resolve_context_quality_profile, import_rejection_reason
    from core.imports.context import get_import_context_album, get_import_source
    from core.downloads.atomic_album_publish import staging_root_for_batch, iter_staged_files
    from core.downloads.atomic_manifest import begin_batch, set_release_state, record_staged_track, record_release_request
    from core.downloads.atomic_recovery import make_db_path_updater
    from database.music_database import MusicDatabase

    profile = deepcopy(_resolve_context_quality_profile(context))
    if profile.get("release_import_mode") != "complete_album" or process_file is None:
        return None
    cancelled = is_cancelled or (lambda: False)
    if cancelled():
        return {"status": "cancelled"}
    album = deepcopy(get_import_context_album(context))
    name = album.get("name") or context.get("track_info", {}).get("album")
    if isinstance(name, dict):
        album = deepcopy(name)
        name = album.get("name")
    if not name or not album.get("id"):
        context["_release_import_note"] = "Full album metadata is unavailable; importing requested track only"
        return None
    if lookup_album is None:
        from core.metadata.album_tracks import get_artist_album_tracks

        lookup_album = get_artist_album_tracks
    try:
        payload = lookup_album(
            album["id"], artist_name=_artist(context.get("track_info", {})), album_name=name, source_override=get_import_source(context) or None
        )
        if not isinstance(payload, dict) or not payload.get("success"):
            raise ValueError("Album catalogue lookup did not succeed")
        catalogue_album = payload.get("album") or {}
        if _text(catalogue_album.get("name")) != _text(name):
            raise ValueError("Album edition could not be confirmed")
        pairs = match_complete_album(files, payload.get("tracks") or [], name)
        if not pairs or requested.path not in {item.path for _, item in pairs}:
            raise ValueError("Release does not contain an unambiguous complete album")
    except Exception as exc:
        context["_release_import_note"] = str(exc)
        logger.info("[Release Import] Requested-track fallback: %s", exc)
        return None

    album.update(catalogue_album)
    album["total_tracks"] = len(pairs)
    source = payload.get("source") or get_import_source(context)
    identity = f"{os.path.realpath(transfer_dir)}::{_text(_artist(context.get('track_info', {})))}::{_text(name)}"
    lock = _album_locks[int(hashlib.sha256(identity.encode()).hexdigest(), 16) % len(_album_locks)]
    with lock:
        stage_id = "release-" + uuid.uuid4().hex
        staging_root = staging_root_for_batch(transfer_dir, stage_id)
        staged_contexts = []
        ready = False
        db = None
        try:
            Path(transfer_dir).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".soulsync-release-input-", dir=transfer_dir) as scratch:
                inputs = []
                for index, (track, item) in enumerate(pairs):
                    if cancelled():
                        return {"status": "cancelled"}
                    path = str(Path(scratch) / f"{index}{Path(item.path).suffix}")
                    shutil.copy2(item.path, path)
                    ctx = _album_context(context, track, album, profile, source)
                    ctx["_release_preflight_only"] = True
                    process_file(f"{context_key}:release:{stage_id}:check:{index}", ctx, path)
                    if not ctx.get("_release_checks_passed"):
                        reason = import_rejection_reason(ctx) or "An album track did not pass the configured checks"
                        context["_release_import_note"] = f"{track.get('name')}: {reason}"
                        return None
                    # Fresh contexts prevent stale success/check flags from leaking.
                    inputs.append((item, path, _album_context(context, track, album, profile, source)))
                if not begin_batch(staging_root, batch_id=stage_id, transfer_dir=transfer_dir, profile_id=context.get("profile_id"), album_name=name):
                    raise OSError("Cannot persist release staging manifest")
                if not set_release_state(staging_root, state="checking", expected_count=len(pairs)):
                    raise OSError("Cannot persist release import gate")
                for index, (item, path, ctx) in enumerate(inputs):
                    if cancelled():
                        return {"status": "cancelled"}
                    ctx.update(_release_staging_root=staging_root, _release_transfer_dir=transfer_dir, _release_abort_on_existing=True)
                    process_file(f"{context_key}:release:{stage_id}:import:{index}", ctx, path)
                    final = ctx.get("_final_processed_path")
                    if (
                        not ctx.get("_pipeline_import_succeeded")
                        or not final
                        or not Path(final).is_file()
                        or not Path(final).resolve().is_relative_to(Path(staging_root).resolve())
                    ):
                        context["_release_import_note"] = "An album track could not be staged; importing requested track only"
                        return None
                    staged_contexts.append((item, ctx))
                    # The normal pipeline already records this. Persist an explicit
                    # final path too, so restart recovery can settle each wishlist.
                    from core.imports.context import get_import_source_ids

                    live = Path(transfer_dir) / Path(final).relative_to(staging_root)
                    if not record_staged_track(
                        staging_root,
                        staged_path=final,
                        final_path=str(live),
                        source=source,
                        source_ids=get_import_source_ids(ctx),
                        track_name=ctx["track_info"]["name"],
                        artist_name=_artist(ctx["track_info"]),
                        profile_id=ctx.get("profile_id"),
                    ):
                        raise OSError("Cannot persist staged track metadata")
                outputs = {ctx["_final_processed_path"] for _, ctx in staged_contexts}
                if len(outputs) != len(pairs):
                    raise ValueError("Album tracks did not produce unique primary outputs")
                requested_stage = next(ctx["_final_processed_path"] for item, ctx in staged_contexts if item.path == requested.path)
                from core.imports.context import get_import_source_ids
                from core.imports.paths import import_profile_id

                requested_entry = {
                    "staged_path": requested_stage,
                    "final_path": str(Path(transfer_dir) / Path(requested_stage).relative_to(staging_root)),
                    "source": get_import_source(context),
                    "source_ids": get_import_source_ids(context),
                    "track_name": context.get("track_info", {}).get("name") or "",
                    "artist_name": _artist(context.get("track_info", {})),
                    "profile_id": import_profile_id(context),
                }
                if not record_release_request(staging_root, requested_entry):
                    raise OSError("Cannot persist initiating request identity")
                if cancelled():
                    return {"status": "cancelled"}
                if not set_release_state(staging_root, state="ready", expected_count=len(pairs)):
                    raise OSError("Cannot persist completed release gate")
                ready = True
                db = MusicDatabase()
                result = publish_verified_release(
                    staging_root,
                    transfer_dir,
                    move_without_replace,
                    make_db_path_updater(db, include_provenance=True),
                    publisher=publish,
                    is_cancelled=cancelled,
                )
                if not result.get("success"):
                    if cancelled():
                        if not set_release_state(staging_root, state="cancelled", expected_count=len(pairs)):
                            # A release prefix with no readable manifest is also
                            # fail-closed. Do not let an old ready gate revive it.
                            from core.downloads.atomic_manifest import manifest_path

                            Path(manifest_path(staging_root)).unlink(missing_ok=True)
                        context["_release_import_note"] = "Album publication cancelled; private files will not be recovered automatically"
                        return {"status": "cancelled", "staging_root": staging_root}
                    context["_release_import_note"] = "Album publication failed; verified files remain in recoverable staging"
                    return {"status": "pending", "staging_root": staging_root}
                mapping = dict(result.get("published") or [])
                requested_path = None
                for item, ctx in staged_contexts:
                    final = mapping.get(ctx["_final_processed_path"])
                    if item.path == requested.path:
                        requested_path = final
                    if final:
                        ctx["_final_processed_path"] = final
                        from core.imports.side_effects import emit_track_downloaded

                        emit_track_downloaded(ctx, automation_engine)
                        from core.imports.pipeline import _settle_wishlist_for_completed_track

                        try:
                            _settle_wishlist_for_completed_track(ctx, final)
                        except Exception as exc:
                            logger.warning("[Release Import] Published track wishlist reconciliation deferred: %s", exc)
                if not requested_path or not Path(requested_path).is_file():
                    raise ValueError("Requested track has no published output")
                from core.imports.pipeline import _settle_wishlist_for_completed_track

                try:
                    _settle_wishlist_for_completed_track(context, requested_path)
                except Exception as exc:
                    logger.warning("[Release Import] Published request wishlist reconciliation deferred: %s", exc)
                context["_final_processed_path"] = requested_path
                context["_pipeline_import_succeeded"] = True
                context["_release_import_note"] = f"Imported complete album ({len(pairs)} tracks)"
                return {"status": "imported", "path": requested_path, "count": len(pairs)}
        except Exception as exc:
            logger.warning("[Release Import] Album expansion failed: %s", exc, exc_info=True)
            context["_release_import_note"] = str(exc)
            if ready:
                if cancelled():
                    if not set_release_state(staging_root, state="cancelled", expected_count=len(pairs)):
                        from core.downloads.atomic_manifest import manifest_path

                        Path(manifest_path(staging_root)).unlink(missing_ok=True)
                    return {"status": "cancelled", "staging_root": staging_root}
                return {"status": "pending", "staging_root": staging_root}
            return None
        finally:
            if not ready and Path(staging_root).exists():
                # Only our private COPIES are removed. Client originals survive.
                try:
                    db = db or MusicDatabase()
                    conn = db._get_connection()
                    try:
                        for file in iter_staged_files(staging_root):
                            for table in ("tracks", "library_history", "track_downloads"):
                                conn.execute(f"DELETE FROM {table} WHERE file_path = ?", (file,))
                        conn.commit()
                    finally:
                        conn.close()
                    shutil.rmtree(staging_root)
                except Exception as exc:
                    logger.warning("[Release Import] Incomplete copies remain private at %s: %s", staging_root, exc)


def _digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_verified_release(staging_root: str, transfer_dir: str, move, update=None, *, publisher=None, is_cancelled=None):
    """Resume an interrupted checked release using its durable output inventory.

    A file may be in staging or already live. Its exact checked bytes must still
    exist before anything moves. Database paths are reconciled for moves that
    completed immediately before the process exited. An ordinary failure rolls
    publication back, while the ready manifest remains retryable.
    """
    from core.downloads.atomic_manifest import read_manifest, manifest_tracks, record_release_publication, skip_release_sidecar
    from core.downloads.atomic_album_publish import iter_staged_files, publish_album_batch, to_final_path

    manifest = read_manifest(staging_root) or {}
    inventory = manifest.get("release_inventory")
    expected = manifest.get("release_expected_count")
    tracks = manifest_tracks(manifest)
    if (
        manifest.get("release_state") != "ready"
        or not isinstance(inventory, dict)
        or not inventory
        or not isinstance(expected, int)
        or expected < 2
        or len(tracks) != expected
        or not all(path in inventory for path in tracks)
    ):
        return {"success": False, "published": [], "failed": [(staging_root, "Release manifest is incomplete")]}
    primary_paths = set(tracks) | {inventory[path]["final_path"] for path in tracks}
    raw_update = update
    if raw_update:

        def update(old_path, new_path):
            rows = raw_update(old_path, new_path)
            # Retained lossy companions and sidecars have no native track row;
            # the initiating per-track outputs still require normal registration.
            if isinstance(rows, int) and rows == 0 and old_path not in primary_paths:
                return None
            return rows

    already = []
    ignored = []
    error = None
    for staged, entry in inventory.items():
        final = to_final_path(staged, staging_root, transfer_dir)
        if not isinstance(entry, dict) or not final or final != entry.get("final_path"):
            error = error or (staged, "Invalid checked output destination")
            continue
        if entry.get("skipped"):
            ignored.append(staged)
            continue
        source_exists, final_exists = Path(staged).is_file(), Path(final).is_file()
        try:
            source_valid = source_exists and _digest(staged) == entry.get("sha256")
            if source_exists and not source_valid:
                error = error or (staged, "Checked staging file was changed")
            if final_exists:
                stat = os.stat(final)
                identity = (stat.st_dev, stat.st_ino)
                owns = entry.get("publish_intent") and identity in (
                    (entry.get("staged_device"), entry.get("staged_inode")),
                    (entry.get("published_device"), entry.get("published_inode")),
                )
                if not owns and entry.get("optional"):
                    ignored.append(staged)
                    continue
                if owns and source_valid and _digest(final) != entry.get("sha256"):
                    # A process may die while SMB copies into its exclusively
                    # created destination. Rescue that known-owned partial copy
                    # privately and retry from the intact checked staging file.
                    rescue = Path(transfer_dir) / ".soulsync_release_recovery" / Path(staging_root).name / (Path(final).name + "." + uuid.uuid4().hex)
                    move_without_replace(final, str(rescue))
                    continue
                if not owns or _digest(final) != entry.get("sha256"):
                    error = error or (staged, "An independently existing library file must be preserved")
                    continue
                already.append((staged, final))
            elif not source_exists:
                error = error or (staged, "A checked release output is missing")
        except OSError as exc:
            error = error or (staged, str(exc))
    extras = set(iter_staged_files(staging_root)) - set(inventory)
    if extras:
        error = (next(iter(extras)), "Unverified file appeared in release staging")

    def rollback_already():
        failed = []
        for staged, final in reversed(already):
            try:
                if Path(staged).exists():
                    # A hard-link publish can die before removing its source.
                    if _digest(staged) != _digest(final):
                        raise ValueError("Cannot roll back changed release output")
                    Path(final).unlink()
                else:
                    move(final, staged)
                if update:
                    update(final, staged)
            except Exception as exc:
                failed.append((final, str(exc)))
        return failed

    if error or (is_cancelled and is_cancelled()):
        return {"success": False, "published": [], "failed": [error or (staging_root, "Cancelled")], "rollback_failed": rollback_already()}
    try:
        for staged in ignored:
            if not skip_release_sidecar(staging_root, staged):
                raise OSError("Cannot persist optional sidecar exclusion")
            Path(staged).unlink(missing_ok=True)
        for staged, final in already:
            if update:
                update(staged, final)
            if Path(staged).exists():
                Path(staged).unlink()

        def checked_move(src, dst):
            # A cancelled job must not publish further outputs. Rollback moves
            # target staging and remain allowed so nothing is left partly live.
            if is_cancelled and is_cancelled() and not Path(dst).resolve().is_relative_to(Path(staging_root).resolve()):
                raise RuntimeError("Release publication cancelled")
            publishing = not Path(dst).resolve().is_relative_to(Path(staging_root).resolve())
            if publishing:
                if not record_release_publication(staging_root, src):
                    raise OSError("Cannot persist publication intent")

                def created(path):
                    if not record_release_publication(staging_root, src, created_path=path):
                        raise OSError("Cannot persist publication ownership")

                try:
                    if move is move_without_replace:
                        move(src, dst, on_created=created)
                    else:
                        move(src, dst)
                        created(dst)
                    if is_cancelled and is_cancelled():
                        raise RuntimeError("Release publication cancelled")
                except Exception:
                    if not Path(src).exists() and Path(dst).exists():
                        move(dst, src)
                    raise
            else:
                move(src, dst)

        result = (publisher or publish_album_batch)(staging_root, transfer_dir, checked_move, update)
    except Exception as exc:
        return {"success": False, "published": [], "failed": [(staging_root, str(exc))], "rollback_failed": rollback_already()}
    if not result.get("success"):
        result["rollback_failed"] = (result.get("rollback_failed") or []) + rollback_already()
        return result
    result["published"] = already + list(result.get("published") or [])
    return result
