"""Import/post-processing pipeline for downloads and imported files."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from core.settings import config_manager
from core.imports.file_ops import (
    cleanup_empty_directories,
    cleanup_slskd_dedup_siblings,
    create_lossy_copy,
    downsample_hires_flac,
    get_audio_quality_string,
    get_quality_tier_from_extension,
    probe_audio_quality,
    safe_move_file,
)
from core.imports.context import (
    build_import_album_info,
    detect_album_info_web,
    extract_artist_name,
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_artist,
    get_import_has_clean_metadata,
    get_import_original_search,
    get_import_source,
    get_import_track_info,
    normalize_import_context,
)
from core.imports.file_integrity import (
    check_audio_integrity,
    duration_reference_for_context,
    expected_duration_for_check,
    probe_decoded_duration,
    resolve_duration_tolerance,
)
from core.imports.filename import extract_track_number_from_filename
from core.imports.guards import check_flac_bit_depth, check_quality_target, move_to_quarantine
from core.imports.silence import detect_broken_audio
from core.imports.quarantine import (
    approve_quarantine_entry,
    delete_quarantine_entry,
    entry_id_from_quarantined_filename,
    list_quarantine_entries,
)
from core.imports.version_mismatch_fallback import try_accept_version_mismatch_fallback
from core.imports.side_effects import (
    emit_track_downloaded,
    record_download_provenance,
    record_library_history_download,
    record_soulsync_library_entry,
    require_library_v2_registration,
)
from core.wishlist.resolution import check_and_remove_from_wishlist
from core.runtime_state import (
    add_activity_item,
    download_batches,
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    mark_task_completed as _mark_task_completed,
    post_process_locks,
    post_process_locks_lock,
    processed_download_ids,
    tasks_lock,
)
from core.metadata.artwork import download_cover_art
from core.metadata.common import wipe_source_tags
from core.imports.tag_policy import should_wipe_tags_on_enhancement_failure
from core.metadata.enrichment import enhance_file_metadata
from core.imports.paths import (
    build_final_path_for_track,
    build_simple_download_destination,
    docker_resolve_path,
)
from core.imports.album_naming import resolve_album_group
from core.metadata.lyrics import generate_lrc_file
from database.music_database import get_database
from core.tag_writer import is_placeholder_meta, read_file_tags, write_tags_to_file
from utils.logging_config import get_logger


logger = get_logger("imports.pipeline")
pp_logger = get_logger("post_processing")

_upgrade_locks_guard = threading.Lock()
_upgrade_locks: dict[int, tuple[threading.Lock, int]] = {}

__all__ = [
    "build_import_pipeline_runtime",
    "post_process_matched_download",
    "post_process_matched_download_with_verification",
]


def _audio_length_seconds(path: str) -> float:
    """Best-effort real length of an audio file: mutagen's header first, then
    an ffmpeg decode when the header reads 0 (HiFi's fragmented FLAC does).
    0.0 when it genuinely can't be determined."""
    try:
        from mutagen import File as MutagenFile
        info = getattr(MutagenFile(path), 'info', None)
        length = float(getattr(info, 'length', 0) or 0)
        if length > 0:
            return length
    except Exception:   # noqa: BLE001,S110 - fall through to the ffmpeg decode
        logger.debug("mutagen length read failed for %s, decoding instead", path, exc_info=True)
    return probe_decoded_duration(path)


def _replacement_length_is_safe(existing_path: str, incoming_path: str,
                                min_ratio: float = 0.8) -> bool:
    """Guard a library-file replacement: True unless the incoming file plays
    materially shorter than the file it would replace (a preview/truncated
    clip). Conservative — if EITHER length can't be determined we allow the
    replace (the other gates own that case), so this never blocks a legit
    upgrade, only a clear shrink."""
    existing_s = _audio_length_seconds(existing_path)
    incoming_s = _audio_length_seconds(incoming_path)
    if existing_s <= 0 or incoming_s <= 0:
        return True
    return incoming_s >= existing_s * min_ratio


def _batch_force_replace(context: dict) -> bool:
    """True when the USER explicitly forced this download (the 'Force
    download' toggle on the download modal). An explicit re-download of a
    track they already own is replace-intent — the metadata-protection skip
    must not silently discard it (#1045).

    Reads the batch's ``force_replace`` key, which web_server sets ONLY for a
    user-checked toggle (wing_it excluded). Deliberately NOT
    ``force_download_all``: the wishlist sets that on every background batch
    (it means "skip ownership checks" there) and Wing It auto-enables it —
    neither may ever overwrite library files. Anything missing or unreadable
    → False, i.e. the protective default."""
    try:
        batch_id = (context or {}).get('batch_id')
        if not batch_id:
            return False
        return bool((download_batches.get(batch_id) or {}).get('force_replace'))
    except Exception:   # noqa: BLE001 - protection is the safe default
        return False


def _should_skip_quarantine_check(context: dict, check_name: str) -> bool:
    bypass = context.get('_skip_quarantine_check')
    if bypass == 'all':
        return True
    if isinstance(bypass, (list, tuple, set)):
        return 'all' in bypass or check_name in bypass
    return bypass == check_name


def _try_force_grab_quarantine_approval(
    context: dict,
    *,
    reason_code: str,
    trigger: str,
    reason: str,
) -> bool:
    """Use a persisted Force-Grab approval only for the exact same reason."""
    try:
        from core.acquisition.pipeline_callback import (
            notify_force_quarantine_auto_approved,
        )
        approved = notify_force_quarantine_auto_approved(
            context,
            reason_code=reason_code,
            trigger=trigger,
            reason=reason,
        )
    except Exception as exc:  # Fail closed into the normal quarantine flow.
        logger.warning("Force-Grab quarantine bridge failed: %s", exc)
        return False
    if approved:
        context['_force_approved_quarantine_reason'] = reason_code
    return approved


def _journal_pipeline_started(context: dict) -> bool:
    """Fail-open bridge into the correlated Acquisition timeline."""
    try:
        from core.acquisition.pipeline_callback import notify_pipeline_import_started

        return notify_pipeline_import_started(context)
    except Exception as exc:  # noqa: BLE001 — bookkeeping cannot block import
        logger.debug("acquisition pipeline-start journal skipped: %s", exc)
        return False


def _journal_pipeline_check(
    context: dict,
    *,
    check: str,
    status: str,
    reason_code: str,
    message: str | None = None,
    actor: str = "system",
    payload: dict | None = None,
) -> bool:
    """Fail-open bridge for one granular pipeline check verdict."""
    try:
        from core.acquisition.pipeline_callback import notify_pipeline_check_result

        return notify_pipeline_check_result(
            context,
            check=check,
            status=status,
            reason_code=reason_code,
            message=message,
            actor=actor,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — bookkeeping cannot block import
        logger.debug("acquisition %s journal skipped: %s", check, exc)
        return False


def _journal_previous_file_replaced(context: dict, *, reason: str, message: str | None = None) -> bool:
    """Fail-open bridge for the F-10 ``previous_file_replaced`` history step."""
    try:
        from core.acquisition.pipeline_callback import notify_previous_file_replaced

        return notify_previous_file_replaced(context, reason=reason, message=message)
    except Exception as exc:  # noqa: BLE001 — bookkeeping cannot block import
        logger.debug("acquisition previous_file_replaced journal skipped: %s", exc)
        return False


def _record_replaced_file_path(context: dict, removed_path: str) -> None:
    """Remember a library file this run physically deleted (dd28-08).

    ``core.library2.autolink`` reads this when it links the replacement, so the
    catalog row for the deleted file is retired in the same transaction that
    records the new one — instead of both rows surviving with the *stale* one
    still flagged primary.
    """
    if not removed_path:
        return
    try:
        paths = context.setdefault("_replaced_file_paths", [])
        if isinstance(paths, list) and removed_path not in paths:
            paths.append(removed_path)
    except Exception as exc:  # noqa: BLE001 — bookkeeping cannot block import
        logger.debug("replaced-path bookkeeping skipped: %s", exc)


def _resolve_enhance_original_path(recorded_path: str) -> str:
    """Map an enhance's recorded library path onto this process's mounts.

    Stored library paths are the media server's view ("/music/…"); this process
    may mount the same library somewhere else entirely. Returns the recorded
    path unchanged when it already exists or nothing better is found, so the
    caller's existence check stays the single decision point (#1109).
    """
    if not recorded_path or os.path.exists(recorded_path):
        return recorded_path
    try:
        from core.library.path_resolver import resolve_library_file_path
        resolved = resolve_library_file_path(
            recorded_path, config_manager=config_manager)
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort
        logger.debug("[Enhance] could not resolve %r: %s", recorded_path, exc)
        return recorded_path
    return resolved or recorded_path


def _retire_lib2_path_after_redownload(old_path: str, new_path: str) -> None:
    """Point the lib2 row of a redownloaded track at its new file (dd28-08).

    The redownload hook fires outside the autolink callback for tracks that
    were already in the catalog, so the retirement has to happen here too.
    """
    try:
        # No feature gate: `library_v2_enabled()` returns True unconditionally
        # (core/library2/feature.py), so the `if not ...: return` that stood here
        # was unreachable.
        from core.settings import config_manager as _cm
        from core.library2.track_files import retire_replaced_files
        from database.music_database import get_database

        conn = get_database()._get_connection()
        try:
            row = conn.execute(
                "SELECT track_id FROM lib2_track_files WHERE path=? LIMIT 1",
                (old_path,),
            ).fetchone()
            if row is None or row[0] is None:
                return
            retire_replaced_files(
                conn, row[0], keep_path=new_path, removed_paths=[old_path],
                config_manager=_cm,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — reconciliation stays fail-open
        logger.debug("lib2 redownload path retirement skipped: %s", exc)


def _quality_history_payload(context: dict, actual_quality: str | None) -> dict:
    track_info = context.get("track_info")
    track_info = track_info if isinstance(track_info, dict) else {}
    source_info = track_info.get("source_info") or {}
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (TypeError, ValueError):
            source_info = {}
    source_info = source_info if isinstance(source_info, dict) else {}
    profile = _resolve_context_quality_profile(context)
    profile_id = track_info.get("quality_profile_id") or profile.get("id")
    before_quality = (
        source_info.get("original_quality")
        or source_info.get("original_format")
    )
    return {
        key: value
        for key, value in {
            "quality_profile_id": profile_id,
            "before_quality": before_quality,
            "after_quality": actual_quality,
        }.items()
        if value not in (None, "")
    }


def _journal_checks_not_run(
    context: dict,
    *,
    checks: tuple[str, ...],
    reason_code: str,
    message: str,
) -> None:
    for check in checks:
        _journal_pipeline_check(
            context,
            check=check,
            status="not_run",
            reason_code=reason_code,
            message=message,
            payload={"decision": "not_run", "blocked_by": reason_code},
        )


def _build_simple_download_tag_data(
    search_result: dict, album_name: str | None,
) -> dict[str, str]:
    """Return only trustworthy metadata known by a direct/simple grab.

    Simple downloads deliberately skip provider enhancement, but their request
    still carries a title, artist and sometimes an album. Persist those values
    without ever stamping placeholder text over useful source tags.
    """
    values = {
        "title": search_result.get("title"),
        "artist_name": search_result.get("artist"),
        "album_title": album_name,
    }
    return {
        key: str(value).strip()
        for key, value in values.items()
        if not is_placeholder_meta(value)
    }


# Maps _build_simple_download_tag_data's db_data keys to the matching
# read_file_tags() key, so a field can be checked against the file's
# CURRENT value before deciding to write it.
_SIMPLE_DOWNLOAD_TAG_TO_FILE_FIELD = {
    'title': 'title',
    'artist_name': 'artist',
    'album_title': 'album',
}


def _fill_only_simple_download_tags(destination: str, simple_tags: dict[str, str]) -> dict[str, str]:
    """Restrict ``simple_tags`` to fields the file doesn't already have.

    A simple/direct download skips provider enhancement, so its title/artist/
    album come straight from the user's search request rather than verified
    metadata — and Soulseek search titles are often parsed from a messy
    filename. Unlike the enhanced-import path (which trusts DB metadata over
    file tags), a simple download must never stamp a real existing tag with
    a value that's merely "not a placeholder" — only fill in what's actually
    blank or placeholder in the file today. When the file's current tags
    can't be read at all, skip writing entirely rather than guessing.
    """
    if not simple_tags:
        return {}
    current = read_file_tags(destination)
    if current.get('error'):
        return {}
    return {
        key: value
        for key, value in simple_tags.items()
        if is_placeholder_meta(current.get(_SIMPLE_DOWNLOAD_TAG_TO_FILE_FIELD.get(key)))
    }


def _resolve_context_quality_profile(context: dict) -> dict:
    """The item's quality profile — its own assigned one
    (``track_info.quality_profile_id``, e.g. from a wishlist row or the
    Auto-Import override) or the app-wide default — resolved LIVE once per
    file and cached on the context. Every post-processing step reads its own
    settings from this dict (with the legacy global config key as fallback
    when resolution failed entirely), so a per-item profile governs the WHOLE
    pipeline: quality gate, AcoustID strictness, deep verify, replace-lower,
    downsample, and lossy copy — not just search ranking."""
    cached = context.get('_quality_profile')
    if isinstance(cached, dict):
        return cached
    try:
        from core.quality.selection import load_profile_by_id
        track_info = context.get('track_info')
        profile_id = track_info.get('quality_profile_id') if isinstance(track_info, dict) else None
        profile = load_profile_by_id(profile_id)
        if not isinstance(profile, dict):
            profile = {}
    except Exception as e:  # noqa: BLE001
        logger.debug("quality profile resolution failed, falling back to global settings: %s", e)
        profile = {}
    context['_quality_profile'] = profile
    return profile


@dataclass(frozen=True)
class _UpgradeSnapshot:
    track_id: int
    primary_id: int | None
    primary_path: str | None
    primary_resolved_path: str | None
    profile: dict
    profile_fingerprint: str
    acquired_quality_json: str | None = None
    retention_json: str | None = None


def _claim_upgrade_lock(track_id: int) -> threading.Lock:
    with _upgrade_locks_guard:
        lock, users = _upgrade_locks.get(track_id, (threading.Lock(), 0))
        _upgrade_locks[track_id] = (lock, users + 1)
    lock.acquire()
    return lock


def _release_upgrade_lock(track_id: int, lock: threading.Lock) -> None:
    lock.release()
    with _upgrade_locks_guard:
        current, users = _upgrade_locks.get(track_id, (lock, 1))
        if current is lock and users <= 1:
            _upgrade_locks.pop(track_id, None)
        elif current is lock:
            _upgrade_locks[track_id] = (lock, users - 1)


def _profile_fingerprint(profile: dict) -> str:
    return json.dumps(profile, sort_keys=True, default=str, separators=(",", ":"))


def _load_upgrade_snapshot(track_id: int) -> _UpgradeSnapshot:
    from core.library2.paths import resolve_lib2_path
    from core.library2.quality_eval import effective_track_profile
    from core.library2.track_files import primary_file_row

    conn = get_database()._get_connection()
    try:
        profile = effective_track_profile(conn, track_id)
        primary = primary_file_row(conn, track_id)
    finally:
        conn.close()
    path = str(primary.get("path") or "") if primary else None
    resolved = None
    if path:
        resolved = path if os.path.isfile(path) else resolve_lib2_path(
            path, config_manager=config_manager)
    return _UpgradeSnapshot(
        track_id=track_id,
        primary_id=int(primary["id"]) if primary and primary.get("id") else None,
        primary_path=path,
        primary_resolved_path=resolved,
        acquired_quality_json=(primary.get("acquired_quality_json") if primary else None),
        retention_json=(primary.get("retention_json") if primary else None),
        profile=dict(profile),
        profile_fingerprint=_profile_fingerprint(dict(profile)),
    )


def _decide_snapshot_upgrade(snapshot: _UpgradeSnapshot, incoming_path: str):
    from core.imports.file_ops import probe_audio_quality
    from core.library2.quality_eval import (
        UpgradeDecision,
        is_upgrade_policy,
        profile_targets,
        upgrade_complete,
    )
    from core.quality.model import rank_candidate
    from core.quality.retention import best_quality_for_targets
    from core.quality.selection import targets_from_profile

    base = {
        "track_id": snapshot.track_id,
        "profile_id": snapshot.profile.get("id"),
        "existing_path": snapshot.primary_path,
        "existing_resolved_path": snapshot.primary_resolved_path,
    }
    if snapshot.primary_id is None:
        return UpgradeDecision(False, True, "The track has no previous primary file", **base)
    old_quality = (
        probe_audio_quality(snapshot.primary_resolved_path)
        if snapshot.primary_resolved_path else None
    )
    new_quality = probe_audio_quality(incoming_path)
    base.update(existing_quality=old_quality, incoming_quality=new_quality)
    if old_quality is None:
        return UpgradeDecision(True, False, "Existing file quality could not be verified", **base)
    if new_quality is None:
        return UpgradeDecision(True, False, "Retained file quality could not be verified", **base)
    targets, policy, cutoff = profile_targets(snapshot.profile)
    if not targets or not is_upgrade_policy(policy):
        return UpgradeDecision(True, False, "The effective profile no longer requests upgrades", **base)
    _targets, fallback_enabled = targets_from_profile(snapshot.profile)
    effective_old_quality = best_quality_for_targets(
        old_quality,
        targets,
        acquired_quality_json=snapshot.acquired_quality_json,
        retention_json=snapshot.retention_json,
    ) or old_quality
    base["existing_quality"] = effective_old_quality
    old_rank, old_score = rank_candidate(effective_old_quality, targets)
    new_rank, new_score = rank_candidate(new_quality, targets)
    if not fallback_enabled and new_rank == len(targets):
        return UpgradeDecision(True, False, "Retained file is outside the strict profile", **base)
    if upgrade_complete(old_rank, len(targets), policy, cutoff):
        return UpgradeDecision(True, False, "The existing file already meets the upgrade cutoff", **base)
    if not (
        new_rank < old_rank
        or (
            new_rank == old_rank
            and new_score > old_score + 0.001
            and (
                new_rank == len(targets)
                or str(new_quality.format).lower() == str(effective_old_quality.format).lower()
            )
        )
    ):
        return UpgradeDecision(
            True, False,
            f"Retained quality {new_quality.label()} is not better than "
            f"{effective_old_quality.label()}",
            **base,
        )
    return UpgradeDecision(
        True, True,
        f"Upgrade {effective_old_quality.label()} → {new_quality.label()}",
        **base,
    )


def _upgrade_snapshot_still_current(snapshot: _UpgradeSnapshot) -> bool:
    current = _load_upgrade_snapshot(snapshot.track_id)
    return (
        current.primary_id == snapshot.primary_id
        and current.primary_path == snapshot.primary_path
        and current.acquired_quality_json == snapshot.acquired_quality_json
        and current.retention_json == snapshot.retention_json
        and current.profile_fingerprint == snapshot.profile_fingerprint
    )


def _prepare_upgrade_artifact(file_path: str, context: dict, profile: dict):
    """Apply profile transforms in staging and return (primary, companions)."""
    from core.imports.file_ops import probe_audio_quality
    from core.quality.lossless import is_lossless_audio_path

    primary = file_path
    companions: list[str] = []
    before = probe_audio_quality(primary)
    if before is not None:
        context['_acquired_audio_quality'] = before.to_dict()
    expects_downsample = bool(
        profile.get("downsample_enabled")
        and before
        and before.format.lower() == "flac"
        and ((before.bit_depth or 0) > 16 or (before.sample_rate or 0) > 44100)
    )
    downsampled = downsample_hires_flac(
        primary, context, enabled=profile.get("downsample_enabled"))
    if expects_downsample and not downsampled:
        raise RuntimeError("Configured upgrade downsample failed; previous file kept")
    if downsampled:
        primary = downsampled
        context["_quality_fallback_downsample"] = True
        retained_quality = probe_audio_quality(primary)
        context.setdefault('_retention_transforms', []).append({
            'type': 'downsample_hires_flac',
            'source_replaced': True,
            'target_bit_depth': 16,
            'target_sample_rate': 44100,
            'output_quality': retained_quality.to_dict() if retained_quality else None,
        })

    lossy_enabled = bool(profile.get("lossy_copy_enabled"))
    expects_lossy = lossy_enabled and is_lossless_audio_path(primary)
    lossy = create_lossy_copy(primary, settings={
        "enabled": profile.get("lossy_copy_enabled"),
        "codec": profile.get("lossy_copy_codec"),
        "bitrate": profile.get("lossy_copy_bitrate"),
        "delete_original": profile.get("lossy_copy_delete_original"),
    })
    if expects_lossy and not lossy:
        raise RuntimeError("Configured upgrade lossy transform failed; previous file kept")
    if lossy:
        context["_quality_fallback_lossy_copy"] = True
        source_retained = os.path.exists(primary)
        lossy_quality = probe_audio_quality(lossy)
        context.setdefault('_retention_transforms', []).append({
            'type': 'lossy_copy',
            'source_replaced': not source_retained,
            'codec': profile.get('lossy_copy_codec'),
            'bitrate': profile.get('lossy_copy_bitrate'),
            'output_quality': lossy_quality.to_dict() if lossy_quality else None,
        })
        if source_retained:
            companions.append(lossy)
        else:
            primary = lossy
    context["_audio_quality"] = get_audio_quality_string(primary)
    return primary, companions


def _mark_task_quarantined(context: dict, quarantine_path: str | None) -> None:
    if not quarantine_path:
        return
    entry_id = entry_id_from_quarantined_filename(quarantine_path)
    # Always stash the entry id on the context. The verification wrapper
    # (post_process_matched_download_with_verification) pops task_id/batch_id
    # OUT of the context before running the inner pipeline, so when a quarantine
    # happens inside that inner run context.get('task_id') is None here and the
    # direct write below no-ops. The wrapper restores task_id afterward and
    # applies this stashed id to the real task — without this, downloads that go
    # through the wrapper (album-bundle / staging path) quarantined with no
    # quarantine_entry_id, so the UI had no way to manage the file (#756-adjacent).
    context['_quarantine_entry_id'] = entry_id
    task_id = context.get('task_id')
    if not task_id:
        return
    with tasks_lock:
        if task_id in download_tasks:
            download_tasks[task_id]['quarantine_entry_id'] = entry_id


def _requeue_quarantined_task_for_retry(task_id, batch_id, trigger) -> bool:
    """Ask the download monitor to re-run this task on its next-best candidate.

    Thin lazy-import wrapper around
    ``core.downloads.monitor.requeue_quarantined_task_for_retry``. Imported
    lazily (and defensively) so the post-processing pipeline stays importable on
    its own — the monitor's retry globals are wired by web_server at startup, and
    manual-import callers that never started a download worker simply get False.
    Returns True when a retry was queued (caller must not mark the task failed).
    """
    if not task_id:
        return False
    try:
        from core.downloads.monitor import requeue_quarantined_task_for_retry
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"next-candidate retry unavailable ({trigger}): {exc}")
        return False
    try:
        return requeue_quarantined_task_for_retry(task_id, batch_id, trigger)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"next-candidate retry failed ({trigger}): {exc}")
        return False


def import_rejection_reason(context: dict) -> str | None:
    """Human-readable reason if post-processing terminally rejected the file
    (quarantine or race-guard), else ``None`` for a clean import.

    ``post_process_matched_download`` signals these outcomes by setting context
    flags and returning normally — it only raises on unexpected errors. The
    download path reads those flags in
    ``post_process_matched_download_with_verification`` and marks the task
    failed, but the MANUAL-import routes call ``post_process_matched_download``
    directly with no task_id, so without this check a quarantined file (now in
    ss_quarantine, not the library) is counted as a successful import and the
    UI shows a green "Done" (#764). Pure + testable: it only inspects the
    context dict the inner pipeline populated."""
    if context.get('_integrity_failure_msg'):
        return f"integrity check failed: {context['_integrity_failure_msg']}"
    if context.get('_acoustid_quarantined'):
        return (
            "AcoustID verification failed: "
            f"{context.get('_acoustid_failure_msg', 'fingerprint mismatch')}"
        )
    if context.get('_bitdepth_rejected'):
        return "rejected by quality filter"
    if context.get('_silence_rejected'):
        return "rejected by audio guard (incomplete or silent audio)"
    if context.get('_race_guard_failed'):
        return "source file disappeared before import completed"
    if context.get('_context_failure_msg'):
        return str(context['_context_failure_msg'])
    return None


def build_import_pipeline_runtime(
    *,
    automation_engine: Any | None = None,
    on_download_completed: Any | None = None,
    web_scan_manager: Any | None = None,
    repair_worker: Any | None = None,
) -> SimpleNamespace:
    """Build the runtime object consumed by core.imports.pipeline."""
    return SimpleNamespace(
        automation_engine=automation_engine,
        on_download_completed=on_download_completed,
        web_scan_manager=web_scan_manager,
        repair_worker=repair_worker,
    )



def _maybe_stage_album_track(context, final_path):
    """#999 atomic album publishing (opt-in, default off). When this track belongs
    to a FRESH whole-album batch and ``album_downloads.atomic_publish`` is on,
    return the private staging-mirror path to post-process into instead of the
    live library path, and mark the batch so batch-completion publishes it as one
    unit — so Plex never sees a partial album mid-download.

    Returns ``final_path`` UNCHANGED for every normal case (flag off, no batch,
    not an album batch, not a fresh album, or any error), so default behavior is
    byte-for-byte today's. The decision is made once per batch and cached."""
    try:
        if not config_manager.get('album_downloads.atomic_publish', False):
            return final_path
        # Real batched downloads run through the verification wrapper, which pops
        # batch_id out of the context (batch accounting is the wrapper's job) and
        # stashes it under _atomic_publish_batch_id for us. Manual/base-direct
        # calls still carry batch_id normally.
        batch_id = context.get('batch_id') or context.get('_atomic_publish_batch_id')
        if not batch_id:
            logger.debug("[Atomic Publish] track has no batch_id — publishing directly")
            return final_path
        from core.downloads.atomic_album_publish import (
            staging_root_for_batch, to_staging_path, album_folder_is_fresh)
        with tasks_lock:
            batch = download_batches.get(batch_id)
            if not batch:
                logger.debug("[Atomic Publish] batch %s not found — publishing directly", batch_id)
                return final_path
            # Decide once per batch, and LOG the decision + reason so an opted-in
            # user can confirm it in app.log. Cached via _atomic_decided.
            if not batch.get('_atomic_decided'):
                batch['_atomic_decided'] = True
                batch['_atomic_active'] = False
                if not batch.get('is_album_download'):
                    logger.info("[Atomic Publish] Batch %s: NOT flagged as an album download "
                                "— publishing directly (atomic only applies to album batches)", batch_id)
                else:
                    transfer_dir = docker_resolve_path(
                        config_manager.get('soulseek.transfer_path', './Transfer'))
                    album_folder = os.path.dirname(final_path)
                    if album_folder_is_fresh(album_folder):
                        _staging_root = staging_root_for_batch(transfer_dir, batch_id)
                        # Prove we can actually WRITE there before committing the
                        # batch to staging. This used to be assumed, and when the
                        # assumption failed the download failed with it: mkdir
                        # raised PermissionError deep inside safe_move_file, the
                        # track never left downloads, and the user got "File
                        # verification failed: expected file at ... but it was not
                        # found after processing". A feature that cannot stage
                        # must degrade to today's direct publish, never take the
                        # download down with it.
                        try:
                            os.makedirs(_staging_root, exist_ok=True)
                            _staging_ok = os.access(_staging_root, os.W_OK)
                        except OSError as _stage_err:
                            _staging_ok = False
                            logger.warning(
                                "[Atomic Publish] Batch %s: cannot create staging dir %s (%s) "
                                "— publishing directly instead. The album may be visible to "
                                "your media server before every track lands.",
                                batch_id, _staging_root, _stage_err)
                        if _staging_ok:
                            batch['_atomic_active'] = True
                            batch['_atomic_transfer_dir'] = transfer_dir
                            batch['_atomic_staging_root'] = _staging_root
                            logger.info("[Atomic Publish] Batch %s: STAGING album until complete "
                                        "(album=%r, transfer=%s)", batch_id,
                                        os.path.basename(album_folder), transfer_dir)
                    else:
                        logger.info("[Atomic Publish] Batch %s: album folder already has tracks "
                                    "(completeness fill) — publishing directly: %s",
                                    batch_id, album_folder)
            if not batch.get('_atomic_active'):
                return final_path
            staging_root = batch.get('_atomic_staging_root')
            transfer_dir = batch.get('_atomic_transfer_dir')
        staged = to_staging_path(final_path, transfer_dir, staging_root)
        if not staged:
            logger.warning("[Atomic Publish] Batch %s: %r is not under the transfer dir %r "
                           "— publishing directly (check soulseek.transfer_path)",
                           batch_id, final_path, transfer_dir)
            return final_path
        return staged
    except Exception as e:
        logger.error("[Atomic Publish] stage redirect failed (using direct publish): %s", e)
        return final_path


def _persist_verification_status(context, final_path):
    """Compute + persist the verification status (verified / unverified /
    force_imported) for a finished import: embedded tag on the file, plus
    context['_verification_status'] for the history row and the Downloads UI.
    MUST be called on EVERY success exit of post-processing (main, playlist
    folder mode, simple download) — a missed exit means no badge and no tag.
    Never raises."""
    try:
        from core.matching.verification_status import status_for_import
        from core.tag_writer import write_verification_status
        status = status_for_import(context)
        if status:
            context['_verification_status'] = status
            if final_path:
                write_verification_status(str(final_path), status)
    except Exception as _vs_err:
        logger.debug(f"verification-status persist skipped: {_vs_err}")


def _attach_manual_skip_path(context_key, final_path):
    """Bind a dispatch-time Library-v2 skip audit to the successful final file."""
    try:
        from core.library2.manual_skips import attach_manual_skip_file
        attach_manual_skip_file(
            get_database(), content_key=context_key, file_path=final_path
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("manual-skip final-path attach skipped: %s", exc)


def _recover_moved_file_bookkeeping(context, artist_context=None, album_info=None):
    """Best-effort DB reconciliation after a post-move pipeline exception.

    Once ``safe_move_file`` has consumed the source, retrying the ordinary
    pipeline is unsafe.  The destination is nevertheless a real imported file
    and must not remain invisible to both library indexes or leave a correlated
    Acquisition grab open forever.  Every operation here is idempotent by its
    own contract: the standalone-library writer keys the track by final path,
    Library-v2 autolink updates an existing path row, and the Acquisition
    callbacks tolerate an already-terminal request.

    History/provenance are deliberately not replayed here because those tables
    are append-only and an exception may have happened after their first write.
    """
    final_path = context.get('_final_processed_path') or context.get('_final_path')
    if not final_path or not os.path.isfile(str(final_path)):
        return False

    context['_post_move_recovered'] = True
    # Reorganize is not a new import. Its runner owns the existing catalogue
    # row and repoints it after this pipeline returns with a verified
    # destination. Running the normal recovery writers here created a second
    # file row at that destination before the old row was repointed there too.
    if context.get('_library_reorganize') is True:
        return True
    try:
        record_soulsync_library_entry(
            context,
            artist_context if isinstance(artist_context, dict) else {},
            album_info if isinstance(album_info, dict) else {},
        )
    except Exception as exc:  # noqa: BLE001 - keep reconciling the other indexes
        logger.warning("Post-move standalone-library reconciliation failed: %s", exc)
    try:
        from core.library2.autolink import link_download_into_library_v2
        linked_lib2_file_id = link_download_into_library_v2(
            context, raise_on_error=True)
        if linked_lib2_file_id is None:
            raise RuntimeError("Library v2 did not create a file row")
        from core.settings import config_manager
        from database.music_database import get_database
        from core.library2.track_reconcile_trigger import (
            schedule_file_track_reconcile,
        )
        schedule_file_track_reconcile(
            get_database(),
            linked_lib2_file_id,
            config_manager,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-move Library-v2 reconciliation failed: %s", exc)
        return False
    try:
        from core.acquisition.pipeline_callback import notify_pipeline_import_success
        notify_pipeline_import_success(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-move Acquisition import reconciliation failed: %s", exc)
    try:
        from core.acquisition.pipeline_callback import notify_manual_grab_import_success
        notify_manual_grab_import_success(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-move correlated-grab reconciliation failed: %s", exc)
    return True


def _confirm_existing_file_bookkeeping(context, artist_context=None, album_info=None):
    """Treat an already-moved destination as success only after lib2 owns it.

    Race guards and duplicate-download protection legitimately encounter a
    destination after the source has gone.  The file is safe, but its presence
    alone is not the import-success contract: Library v2 must also have an
    active file row.  Recovery is idempotent, so it is safe both after another
    worker completed and after an earlier attempt stopped between move+link.
    """
    if context.get('_pipeline_import_succeeded'):
        return True
    if _recover_moved_file_bookkeeping(context, artist_context, album_info):
        context['_pipeline_import_succeeded'] = True
        return True
    context.setdefault(
        '_context_failure_msg',
        'the file is at its destination, but Library v2 could not register it',
    )
    return False


def _record_completed_import_side_effects(
    context, artist_context, album_info, automation_engine,
):
    """Register a genuine new import and emit its download events.

    Reorganize deliberately reuses the file-processing half of this pipeline,
    but it must leave registration to the runner so one existing file row is
    updated instead of creating a second row for the same destination.
    Returns ``True`` when ordinary import side effects ran.
    """
    if context.get('_library_reorganize') is True:
        logger.info(
            "[Reorganize] Destination ready; catalogue path update deferred "
            "to the reorganize runner"
        )
        return False
    record_download_provenance(context)
    record_soulsync_library_entry(context, artist_context, album_info)
    require_library_v2_registration(context)
    emit_track_downloaded(context, automation_engine)
    record_library_history_download(context)
    return True


def _apply_profile_output_transforms(final_path: str, context: dict,
                                     profile: dict) -> str:
    """Apply downsample/lossy retention while preserving acquisition truth."""
    acquired_quality = probe_audio_quality(final_path)
    if acquired_quality is not None:
        context['_acquired_audio_quality'] = acquired_quality.to_dict()

    downsampled_path = downsample_hires_flac(
        final_path, context, enabled=profile.get('downsample_enabled'))
    if downsampled_path:
        final_path = downsampled_path
        context['_final_processed_path'] = final_path
        retained_quality = probe_audio_quality(final_path)
        context.setdefault('_retention_transforms', []).append({
            'type': 'downsample_hires_flac',
            'source_replaced': True,
            'target_bit_depth': 16,
            'target_sample_rate': 44100,
            'output_quality': retained_quality.to_dict() if retained_quality else None,
        })
        context['_quality_fallback_downsample'] = True

    _persist_verification_status(context, final_path)

    lossy_path = create_lossy_copy(final_path, settings={
        'enabled': profile.get('lossy_copy_enabled'),
        'codec': profile.get('lossy_copy_codec'),
        'bitrate': profile.get('lossy_copy_bitrate'),
        'delete_original': profile.get('lossy_copy_delete_original'),
    } if profile else None)
    if not lossy_path:
        return final_path

    source_retained = os.path.isfile(final_path)
    lossy_quality = probe_audio_quality(lossy_path)
    context.setdefault('_retention_transforms', []).append({
        'type': 'lossy_copy',
        'source_replaced': not source_retained,
        'codec': profile.get('lossy_copy_codec'),
        'bitrate': profile.get('lossy_copy_bitrate'),
        'output_quality': lossy_quality.to_dict() if lossy_quality else None,
    })
    context['_quality_fallback_lossy_copy'] = True
    if source_retained:
        companions = context.setdefault('_companion_file_paths', [])
        if lossy_path not in companions:
            companions.append(lossy_path)
        context['_final_processed_path'] = final_path
        return final_path

    context['_final_processed_path'] = lossy_path
    return lossy_path


def post_process_matched_download(context_key, context, file_path, runtime, metadata_runtime=None):
    on_download_completed = getattr(runtime, "on_download_completed", None)
    automation_engine = getattr(runtime, "automation_engine", None)
    web_scan_manager = getattr(runtime, "web_scan_manager", None)
    repair_worker = getattr(runtime, "repair_worker", None)
    metadata_runtime = metadata_runtime or runtime

    def _notify_download_completed(batch_id, task_id, success=True):
        if on_download_completed:
            on_download_completed(batch_id, task_id, success=success)

    from core.imports.upgrade_intent import get_upgrade_intent
    _server_upgrade_intent = get_upgrade_intent(context)
    _upgrade_track_lock = None
    _upgrade_snapshot = None
    if _server_upgrade_intent is not None:
        _upgrade_track_lock = _claim_upgrade_lock(_server_upgrade_intent.track_id)

    with post_process_locks_lock:
        if context_key not in post_process_locks:
            post_process_locks[context_key] = threading.Lock()
        file_lock = post_process_locks[context_key]

    file_lock.acquire()
    try:
        if not os.path.exists(file_path):
            existing_final = context.get('_final_processed_path')
            if existing_final and os.path.exists(existing_final):
                logger.info(
                    f"[Race Guard] Source gone but destination exists — already processed by another thread: "
                    f"{os.path.basename(existing_final)}"
                )
                _confirm_existing_file_bookkeeping(context)
                return
            # File was intentionally moved to quarantine by a concurrent/earlier
            # post-process call — this is a stale duplicate dispatch, not a race.
            # _mark_task_quarantined sets _quarantine_entry_id for every quarantine
            # trigger (AcoustID, integrity, bit-depth).  The quarantine entry and
            # its retry are already in flight; don't overwrite the task state with
            # a spurious race-guard failure.
            if context.get('_quarantine_entry_id'):
                logger.debug(
                    f"[Race Guard] Source gone but already quarantined (entry %s) — stale duplicate call, ignoring: "
                    f"{os.path.basename(file_path)}",
                    context['_quarantine_entry_id'],
                )
                return
            logger.error(
                f"[Race Guard] Source file gone and no known destination — marking as failed: "
                f"{os.path.basename(file_path)}"
            )
            context['_race_guard_failed'] = True
            return

        _basename = os.path.basename(file_path)
        _prev_size = -1
        for _stability_check in range(5):
            try:
                _cur_size = os.path.getsize(file_path)
            except OSError:
                _cur_size = -1
            if _cur_size == _prev_size and _cur_size > 0:
                break
            _prev_size = _cur_size
            if _stability_check == 0:
                logger.info(f"Waiting for file to stabilise: {_basename} ({_cur_size} bytes)")
            time.sleep(1.5)
        else:
            logger.info(f"File may still be writing after stability checks: {_basename} ({_prev_size} bytes)")

        if _server_upgrade_intent is not None:
            # Resolve one live Track→Album→Artist→Global profile snapshot.
            # Every gate and transform in this import consumes this exact dict;
            # the pre-publish CAS below aborts if either it or the primary moved.
            _upgrade_snapshot = _load_upgrade_snapshot(
                _server_upgrade_intent.track_id)
            context['_quality_profile'] = dict(_upgrade_snapshot.profile)

        # This is the first point where the shared pipeline has claimed a
        # stable source file. Correlated native/manual/scheduled grabs now get
        # an explicit import-start before any guard can fail.
        _journal_pipeline_started(context)

        # File integrity check: catches broken slskd transfers (truncated,
        # corrupted, wrong file masquerading as the target) before we burn
        # cycles on AcoustID + tagging + library sync. Universal across
        # formats; failed files get quarantined and the slot freed.
        try:
            _normalized_for_duration = normalize_import_context(context)
            _duration_track = get_import_track_info(_normalized_for_duration)
            _expected_duration_ms = int(_duration_track.get("duration_ms", 0) or 0) or None
        except Exception:
            _expected_duration_ms = None
        # Local/manual imports are the user's own files, not slskd transfers —
        # skip the duration-agreement leg (it would false-quarantine a file that
        # drifts from a re-resolved release; #804). Size + parse legs still run.
        _is_local_import = bool(context.get('is_local_import')) if isinstance(context, dict) else False
        _expected_duration_ms = duration_reference_for_context(_expected_duration_ms, context)
        _expected_duration_ms = expected_duration_for_check(_expected_duration_ms, _is_local_import)
        if _is_local_import and _expected_duration_ms is None:
            logger.debug("[Integrity] Local import — duration-agreement leg skipped for %s", _basename)

        # User-configurable tolerance override. None = use built-in
        # auto-scaled defaults (3s normal / 5s for tracks >10min). Set
        # higher (e.g. 10) when matched files routinely drift from the
        # source's reported duration (live recordings, alternate
        # masterings, etc).
        _duration_tolerance_override = resolve_duration_tolerance(
            config_manager.get('post_processing.duration_tolerance_seconds', 0)
        )
        # User-approved quarantine restores can bypass quarantine gates
        # for this one post-processing pass.
        if _should_skip_quarantine_check(context, 'integrity'):
            logger.info(f"[Integrity] Skipped (user approval) for {_basename}")
            integrity = None
        else:
            try:
                integrity = check_audio_integrity(
                    file_path,
                    _expected_duration_ms,
                    length_tolerance_s=_duration_tolerance_override,
                )
            except Exception as integrity_error:
                logger.error(f"[Integrity] Check raised unexpectedly (continuing): {integrity_error}")
                integrity = None

        if integrity is not None and not integrity.ok:
            logger.error(f"[Integrity] Rejected {_basename}: {integrity.reason}")
            context['_integrity_failure_msg'] = integrity.reason
            context['_integrity_checks'] = integrity.checks
            _journal_checks_not_run(
                context,
                checks=('quality', 'acoustic_id'),
                reason_code='blocked_by_integrity',
                message=f"Not run because integrity failed: {integrity.reason}",
            )
            try:
                quarantine_path = move_to_quarantine(
                    file_path,
                    context,
                    f"Integrity check failed: {integrity.reason}",
                    automation_engine,
                    trigger='integrity',
                )
                _mark_task_quarantined(context, quarantine_path)
                logger.error(f"File quarantined due to integrity failure: {quarantine_path}")
            except Exception as quarantine_error:
                # Quarantine MOVE failed (e.g. cross-device / permission on a NAS).
                # Do NOT delete — destroying a download we couldn't even quarantine is
                # data loss and forces a re-download. Leave it in place so it can be
                # retried; the task is still marked failed below either way (#kettui).
                logger.error(
                    f"Quarantine failed ({quarantine_error}) — leaving file in place "
                    f"for retry (not deleting): {file_path}"
                )

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            task_id = context.get('task_id')
            batch_id = context.get('batch_id')
            if task_id:
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        download_tasks[task_id]['error_message'] = (
                            f"File integrity check failed: {integrity.reason}"
                        )

            if task_id and batch_id:
                _notify_download_completed(batch_id, task_id, success=False)
            return

        if integrity is not None:
            logger.info(
                f"[Integrity] {_basename} passed "
                f"(size={integrity.checks.get('size_bytes', '?')}b, "
                f"length={integrity.checks.get('actual_length_s', 0):.1f}s, "
                f"drift={integrity.checks.get('length_drift_s', 'n/a')})"
            )

        # Audio-completeness guard — runs right where the length is verified,
        # BEFORE the AcoustID/quality gates, so a truncated file (container
        # claims the full length but only ~30s actually decodes, e.g. HiFi/
        # Monochrome HLS assembly) or a mostly-silent file is caught regardless
        # of its quality verdict and gets the right reason. Same quarantine +
        # next-candidate retry pattern as the integrity check.
        #
        # Opt-in (default OFF): this is the one check that fully DECODES the file
        # with ffmpeg, so it is the most CPU-expensive step in post-processing.
        # Most preview/truncation cases are already caught at the source
        # (HiFi/Qobuz have their own guards), so it stays off unless the user
        # turns it on in the item's quality profile (Settings → Quality).
        _audio_guard_enabled = _resolve_context_quality_profile(context).get(
            'deep_audio_verify',
            config_manager.get('post_processing.audio_completeness_check', False))
        _skip_audio = (not _audio_guard_enabled) or _should_skip_quarantine_check(context, 'silence')
        audio_reason = None if _skip_audio else detect_broken_audio(file_path)
        if audio_reason:
            logger.error(f"[AudioGuard] Rejected {_basename}: {audio_reason}")
            context['_silence_rejected'] = True
            _journal_checks_not_run(
                context,
                checks=('quality', 'acoustic_id'),
                reason_code='blocked_by_audio_guard',
                message=f"Not run because the audio guard failed: {audio_reason}",
            )
            # Stashed for the verification wrapper, which runs this pipeline
            # with task_id popped out of the context (see _mark_task_quarantined)
            # and so must do the retry/fail marking itself afterward.
            context['_quarantine_reject_reason'] = f"Audio guard: {audio_reason}"
            try:
                quarantine_path = move_to_quarantine(
                    file_path, context, audio_reason, automation_engine,
                    trigger='silence',
                )
                _mark_task_quarantined(context, quarantine_path)
                logger.warning("File quarantined — incomplete/silent audio: %s", quarantine_path)
            except Exception as quarantine_error:
                # Don't delete a file we couldn't quarantine — leave it for retry
                # instead of forcing a re-download (data loss). See integrity block.
                logger.error(
                    f"Quarantine failed ({quarantine_error}) — leaving file in place "
                    f"for retry (not deleting): {file_path}"
                )

            with matched_context_lock:
                matched_downloads_context.pop(context_key, None)

            task_id = context.get('task_id')
            batch_id = context.get('batch_id')
            # Try the next-best candidate before giving up — same pattern as
            # the integrity / AcoustID / quality failures.
            if _requeue_quarantined_task_for_retry(task_id, batch_id, 'silence'):
                logger.info(
                    "Incomplete/silent audio for task %s — retrying next-best candidate: %s",
                    task_id, audio_reason,
                )
                return
            if task_id:
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        download_tasks[task_id]['error_message'] = f"Audio guard: {audio_reason}"
            if task_id and batch_id:
                _notify_download_completed(batch_id, task_id, success=False)
            return

        # QUALITY GATE — runs BEFORE AcoustID so (1) a wrong-quality file is
        # rejected without paying for an AcoustID fingerprint, and (2) the real
        # audio quality is recorded on the context (→ quarantine sidecar) for
        # EVERY trigger, so it's known when reviewing/approving any quarantined
        # file. force_import still never fires on a quality mismatch.
        context['_audio_quality'] = get_audio_quality_string(
            file_path,
            claimed_format=(get_import_original_search(context) or {}).get('quality'),
            claimed_bitrate=(get_import_original_search(context) or {}).get('bitrate'),
        )
        if context['_audio_quality']:
            logger.info(f"Audio quality detected: {context['_audio_quality']}")

            _skip_quality = _should_skip_quarantine_check(context, 'bit_depth') or \
                            _should_skip_quarantine_check(context, 'quality')
            _quality_payload = _quality_history_payload(
                context, context['_audio_quality'])
            if _skip_quality:
                rejection_reason = None
            else:
                try:
                    rejection_reason = check_quality_target(file_path, context)
                except Exception as quality_error:
                    _journal_pipeline_check(
                        context,
                        check='quality',
                        status='error',
                        reason_code='quality_check_error',
                        message=str(quality_error),
                        payload={**_quality_payload, 'decision': 'error'},
                    )
                    _journal_checks_not_run(
                        context,
                        checks=('acoustic_id',),
                        reason_code='blocked_by_quality_error',
                        message='Not run because the quality check errored',
                    )
                    raise
            if _skip_quality:
                logger.info(f"[QualityGuard] Skipped (user approval) for {_basename}")
                _journal_pipeline_check(
                    context,
                    check='quality',
                    status='skipped',
                    reason_code='user_override',
                    message='Quality check skipped by user approval',
                    actor='user',
                    payload={**_quality_payload, 'decision': 'user_override'},
                )
            elif rejection_reason:
                # Record the actual guard verdict before a persisted Force-Grab
                # decision potentially approves that exact rejection.
                _journal_pipeline_check(
                    context,
                    check='quality',
                    status='failed',
                    reason_code='quality_not_allowed',
                    message=rejection_reason,
                    payload={**_quality_payload, 'decision': 'rejected'},
                )
            else:
                _journal_pipeline_check(
                    context,
                    check='quality',
                    status='passed',
                    reason_code='quality_allowed',
                    payload={**_quality_payload, 'decision': 'allowed'},
                )
            if rejection_reason and _try_force_grab_quarantine_approval(
                context,
                reason_code='quality_not_allowed',
                trigger='quality',
                reason=rejection_reason,
            ):
                logger.info(
                    "[QualityGuard] Auto-approved exact Force-Grab reason "
                    "quality_not_allowed for %s",
                    _basename,
                )
                rejection_reason = None
            if rejection_reason:
                _journal_checks_not_run(
                    context,
                    checks=('acoustic_id',),
                    reason_code='blocked_by_quality',
                    message='Not run because the quality check rejected the file',
                )
                try:
                    quarantine_path = move_to_quarantine(
                        file_path,
                        context,
                        rejection_reason,
                        automation_engine,
                        trigger='quality',
                    )
                    _mark_task_quarantined(context, quarantine_path)
                    logger.info(f"File quarantined due to quality mismatch: {quarantine_path}")
                except Exception as quarantine_error:
                    # Don't delete a file we couldn't quarantine — leave it for retry
                    # instead of forcing a re-download (data loss). See integrity block.
                    logger.error(
                        f"Quarantine failed ({quarantine_error}) — leaving file in place "
                        f"for retry (not deleting): {file_path}"
                    )

                context['_bitdepth_rejected'] = True
                # Same wrapper stash as the audio guard above.
                context['_quarantine_reject_reason'] = f"Quality filter: {rejection_reason}"
                task_id = context.get('task_id')
                batch_id = context.get('batch_id')
                with matched_context_lock:
                    matched_downloads_context.pop(context_key, None)

                # Try the next-best candidate before giving up — same pattern
                # as AcoustID and integrity failures.
                if _requeue_quarantined_task_for_retry(task_id, batch_id, 'quality'):
                    logger.info(
                        "Quality mismatch for task %s — retrying next-best candidate: %s",
                        task_id, rejection_reason,
                    )
                    return

                if task_id:
                    with tasks_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'failed'
                            download_tasks[task_id]['error_message'] = f"Quality filter: {rejection_reason}"
                if task_id and batch_id:
                    _notify_download_completed(batch_id, task_id, success=False)
                return
        else:
            _journal_pipeline_check(
                context,
                check='quality',
                status='not_run',
                reason_code='quality_detection_unavailable',
                message='Audio quality could not be detected',
                payload={**_quality_history_payload(context, None), 'decision': 'not_run'},
            )

        _skip_acoustid = _should_skip_quarantine_check(context, 'acoustid')
        if _skip_acoustid:
            _automatic_fallback = bool(context.get('_version_mismatch_fallback'))
            logger.info(
                "[AcoustID] Skipped (%s) for %s",
                "version-mismatch fallback" if _automatic_fallback else "user approval",
                _basename,
            )
            context['_acoustid_result'] = 'skip'
            context['_acoustid_message'] = (
                'AcoustID skipped by version-mismatch fallback'
                if _automatic_fallback
                else 'AcoustID skipped by user approval'
            )
            _journal_pipeline_check(
                context,
                check='acoustic_id',
                status='skipped',
                reason_code=(
                    'version_mismatch_fallback'
                    if _automatic_fallback
                    else 'user_override'
                ),
                message=context['_acoustid_message'],
                actor='system' if _automatic_fallback else 'user',
                payload={
                    'decision': (
                        'version_mismatch_fallback'
                        if _automatic_fallback
                        else 'user_override'
                    ),
                },
            )
        else:
            try:
                from core.acoustid_verification import AcoustIDVerification, VerificationResult

                verifier = AcoustIDVerification()
                available, available_reason = verifier.quick_check_available()
                if available:
                    context = normalize_import_context(context)
                    track_info = get_import_track_info(context)
                    original_search = get_import_original_search(context)
                    artist_context = get_import_context_artist(context)

                    expected_track = get_import_clean_title(context, default=original_search.get('title', ''))
                    expected_artist = ''
                    track_artists = track_info.get('artists', [])
                    if track_artists:
                        first = track_artists[0]
                        if isinstance(first, dict):
                            expected_artist = first.get('name', '')
                        elif isinstance(first, str):
                            expected_artist = first
                    if not expected_artist:
                        expected_artist = extract_artist_name(artist_context) or get_import_clean_artist(context, default='')

                    if expected_track and expected_artist:
                        logger.info(f"Running AcoustID verification for: '{expected_track}' by '{expected_artist}'")
                        verification_result, verification_msg = verifier.verify_audio_file(
                            file_path,
                            expected_track,
                            expected_artist,
                            context,
                        )
                        logger.info(f"AcoustID verification result: {verification_result.value} - {verification_msg}")
                        raw_result = str(verification_result.value or '').lower()
                        context['_acoustid_result'] = verification_result.value
                        # Deep-dive A7: the concrete AcoustID reason is otherwise lost the
                        # moment this function returns — stash it now so the lib2 autolink
                        # callback can persist it onto the file row for the Info-tab.
                        context['_acoustid_message'] = verification_msg

                        # Fail-closed mode: when the item's quality profile
                        # requires a hard AcoustID PASS, a SKIP (ran but couldn't
                        # confirm — no fingerprint match / cross-script metadata)
                        # is treated like a FAIL: quarantine + try the next
                        # candidate, instead of importing an unverified file.
                        # ERROR (rate-limit / infra) is NOT blocked — that would
                        # stall the whole pipeline during an outage; those still
                        # import with their existing flag.
                        require_verified = _resolve_context_quality_profile(context).get(
                            'acoustid_required',
                            config_manager.get('acoustid.require_verified', False))
                        _skip_as_fail = raw_result == 'skip' and require_verified
                        if _skip_as_fail:
                            verification_msg = (
                                f"AcoustID could not confirm the track and 'require verified' "
                                f"is on — rejecting unverified file ({verification_msg})"
                            )
                            context['_acoustid_message'] = verification_msg
                            logger.warning("[AcoustID] Require-verified: SKIP treated as FAIL — %s", verification_msg)

                        verification_failed = (
                            verification_result == VerificationResult.FAIL
                            or _skip_as_fail
                        )
                        if verification_failed:
                            check_status = 'failed'
                            reason_code = (
                                'acoustid_unverified_required'
                                if _skip_as_fail
                                else 'acoustid_mismatch'
                            )
                            decision = 'rejected'
                        elif raw_result in {'pass', 'passed'}:
                            check_status = 'passed'
                            reason_code = 'acoustid_verified'
                            decision = 'verified'
                        elif raw_result == 'skip':
                            check_status = 'skipped'
                            reason_code = 'acoustid_inconclusive_allowed'
                            decision = 'inconclusive_allowed'
                        else:
                            check_status = 'error'
                            reason_code = 'acoustid_result_error'
                            decision = 'error_allowed'
                        _journal_pipeline_check(
                            context,
                            check='acoustic_id',
                            status=check_status,
                            reason_code=reason_code,
                            message=verification_msg,
                            payload={
                                'decision': decision,
                                'raw_result': raw_result,
                                'require_verified': bool(require_verified),
                            },
                        )

                        if verification_failed:
                            try:
                                quarantine_path = move_to_quarantine(
                                    file_path,
                                    context,
                                    verification_msg,
                                    automation_engine,
                                    trigger='acoustid_unverified' if _skip_as_fail else 'acoustid',
                                )
                                _mark_task_quarantined(context, quarantine_path)
                                logger.error(f"File quarantined due to verification failure: {quarantine_path}")
                            except Exception as quarantine_error:
                                # Don't delete a file we couldn't quarantine — leave it for
                                # retry instead of forcing a re-download (data loss). The
                                # task is still marked failed / requeued below. See integrity.
                                logger.error(
                                    f"Quarantine failed ({quarantine_error}) — leaving file "
                                    f"in place for retry (not deleting): {file_path}"
                                )

                            context['_acoustid_quarantined'] = True
                            context['_acoustid_failure_msg'] = verification_msg
                            with matched_context_lock:
                                if context_key in matched_downloads_context:
                                    del matched_downloads_context[context_key]

                            task_id = context.get('task_id')
                            batch_id = context.get('batch_id')
                            if task_id:
                                with tasks_lock:
                                    if task_id in download_tasks:
                                        download_tasks[task_id]['status'] = 'failed'
                                        download_tasks[task_id]['error_message'] = (
                                            f"AcoustID verification failed: {verification_msg}"
                                        )

                            if task_id and batch_id:
                                _notify_download_completed(batch_id, task_id, success=False)
                            return
                    else:
                        logger.warning("AcoustID verification not run: missing track/artist info")
                        context['_acoustid_result'] = 'skip'
                        context['_acoustid_message'] = 'missing track/artist info'
                        _journal_pipeline_check(
                            context,
                            check='acoustic_id',
                            status='not_run',
                            reason_code='missing_expected_metadata',
                            message=context['_acoustid_message'],
                            payload={'decision': 'not_run'},
                        )
                else:
                    logger.info(f"ℹ️ AcoustID verification not available: {available_reason}")
                    context['_acoustid_result'] = 'disabled'
                    context['_acoustid_message'] = available_reason
                    _journal_pipeline_check(
                        context,
                        check='acoustic_id',
                        status='not_run',
                        reason_code='verification_unavailable',
                        message=available_reason,
                        payload={'decision': 'not_run'},
                    )
            except Exception as verify_error:
                logger.error(f"AcoustID verification error (continuing normally): {verify_error}")
                context['_acoustid_result'] = 'error'
                context['_acoustid_message'] = str(verify_error)
                _journal_pipeline_check(
                    context,
                    check='acoustic_id',
                    status='error',
                    reason_code='verification_error',
                    message=str(verify_error),
                    payload={'decision': 'error_allowed'},
                )

        # Upgrade authority is a sealed process-local object, never source_info
        # or any other client-/wishlist-serialisable JSON field.  The exact
        # retained artifact is evaluated after profile transforms below.
        _upgrade_decision = None
        _upgrade_companions: list[str] = []

        search_result = context.get('search_result', {}) or {}
        if not isinstance(search_result, dict):
            search_result = {}
        is_simple_download = search_result.get('is_simple_download', False)
        if is_simple_download:
            logger.info(f"Processing simple download (no metadata enhancement): {file_path}")

            destination, album_name, filename = build_simple_download_destination(context, file_path)
            if album_name:
                logger.info(f"Moving to album folder: {album_name}")
            else:
                logger.info("Moving to Transfer root (single track)")

            safe_move_file(file_path, destination)
            logger.info(f"Moved simple download to: {destination}")
            from core.imports.file_ops import move_companion_sidecars
            move_companion_sidecars(file_path, destination)
            cleanup_slskd_dedup_siblings(file_path)

            simple_tags = _build_simple_download_tag_data(
                search_result, album_name)
            simple_tags = _fill_only_simple_download_tags(destination, simple_tags)
            if simple_tags:
                try:
                    tag_result = write_tags_to_file(
                        destination, simple_tags, embed_cover=False)
                    if tag_result.get('success'):
                        logger.info(
                            "Embedded direct-download metadata: %s",
                            ', '.join(simple_tags),
                        )
                    else:
                        logger.warning(
                            "[Simple Download] Could not embed metadata: %s",
                            tag_result.get('error', 'unknown error'),
                        )
                except Exception as tag_error:
                    # The file is already safely moved. Tagging is enrichment,
                    # so a malformed/unsupported source file remains a
                    # successful direct download with a visible warning.
                    logger.warning(
                        "[Simple Download] Metadata write failed: %s", tag_error)

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            # Honor the "Auto-Scan After Downloads" toggle. Batch downloads emit
            # batch_complete and the automation engine only scans when that
            # automation is enabled — but a simple download never forms a batch,
            # so this direct scan used to fire unconditionally and kept scanning
            # even with auto-scan turned off (#995 follow-up). Gate it on the same
            # automation; fail open when the engine isn't available.
            _auto_scan_on = (automation_engine is None
                             or automation_engine.is_event_action_enabled('batch_complete', 'scan_library'))
            if web_scan_manager and _auto_scan_on:
                threading.Thread(
                    target=lambda: web_scan_manager.request_scan("Simple download completed"),
                    daemon=True,
                ).start()

            context['_final_path'] = str(destination)
            _persist_verification_status(context, destination)
            _attach_manual_skip_path(context_key, destination)
            record_download_provenance(context, True)
            activity_target = f"{album_name}/{filename}" if album_name else filename
            add_activity_item("", "Download Complete", activity_target, "Now")
            logger.info(f"Simple download post-processing complete: {activity_target}")
            emit_track_downloaded(context, automation_engine)
            record_library_history_download(context)
            context['_simple_download_completed'] = True
            context['_pipeline_import_succeeded'] = True
            try:
                check_and_remove_from_wishlist(context)
            except Exception as wishlist_error:
                logger.error(f"[Simple Download] Error checking wishlist removal: {wishlist_error}")
            return

        logger.info(f"Starting robust post-processing for: {context_key}")

        context = normalize_import_context(context)
        artist_context = get_import_context_artist(context)
        track_info = get_import_track_info(context)
        original_search = get_import_original_search(context)
        has_clean_metadata = get_import_has_clean_metadata(context)

        if not artist_context:
            # dd28-39: returning with no flag set left the outer wrapper with
            # no ``_final_processed_path`` and no rejection reason, so it logged
            # "cannot verify, assuming success" and marked the task COMPLETED
            # while the file sat unimported in the download folder. Name the
            # failure so both the download path and the manual-import routes
            # report it.
            logger.error("Post-processing failed: Missing artist context.")
            context['_context_failure_msg'] = (
                "no artist context — the download could not be attributed to an artist"
            )
            return

        _junk_artist_names = {'', 'unknown', 'unknown artist', 'various artists', 'none', 'null'}
        _artist_name = (artist_context.get('name', '') if isinstance(artist_context, dict) else '').strip()
        if _artist_name.lower() in _junk_artist_names:
            logger.info(f"[Unknown Artist Guard] Artist name is '{_artist_name}' — attempting to resolve")
            _resolved = False
            track_info_guard = track_info or {}
            original_search_guard = original_search or {}

            _ti_artists = track_info_guard.get('artists', [])
            if isinstance(_ti_artists, list) and _ti_artists:
                _first = _ti_artists[0]
                _name = _first.get('name', '') if isinstance(_first, dict) else str(_first)
                if _name and _name.strip().lower() not in _junk_artist_names:
                    artist_context['name'] = _name.strip()
                    logger.info(f"[Unknown Artist Guard] Resolved from track_info.artists: '{_name}'")
                    _resolved = True

            if not _resolved:
                _os_artist = original_search_guard.get('artist') or original_search_guard.get('artist_name') or ''
                if isinstance(_os_artist, str) and _os_artist.strip().lower() not in _junk_artist_names:
                    artist_context['name'] = _os_artist.strip()
                    logger.info(f"[Unknown Artist Guard] Resolved from original_search_result: '{_os_artist}'")
                    _resolved = True

            if not _resolved:
                _track_id = track_info_guard.get('id') or track_info_guard.get('track_id') or ''
                if _track_id:
                    try:
                        from core.metadata_service import get_client_for_source, get_primary_source

                        _guard_source = get_import_source(context) or get_primary_source()
                        _fb_client = get_client_for_source(_guard_source) or get_client_for_source(get_primary_source())
                        if hasattr(_fb_client, 'get_track_details'):
                            _details = _fb_client.get_track_details(str(_track_id))
                            if _details and isinstance(_details, dict):
                                _d_artists = _details.get('artists', [])
                                if isinstance(_d_artists, list) and _d_artists:
                                    _d_first = _d_artists[0]
                                    _d_name = _d_first.get('name', '') if isinstance(_d_first, dict) else str(_d_first)
                                    if _d_name and _d_name.strip().lower() not in _junk_artist_names:
                                        artist_context['name'] = _d_name.strip()
                                        logger.info(f"[Unknown Artist Guard] Resolved from metadata API: '{_d_name}'")
                                        _resolved = True
                    except Exception as _guard_err:
                        logger.error(f"[Unknown Artist Guard] Metadata re-fetch failed: {_guard_err}")

            if not _resolved:
                logger.error(f"[Unknown Artist Guard] Could not resolve artist — proceeding with '{_artist_name}'")

            context['artist'] = artist_context

        logger.debug(f"[Debug] Post-processing - track_info type: {type(track_info)}, is None: {track_info is None}, is empty: {not track_info}")
        if track_info:
            logger.debug(f"[Debug] Post-processing - track_info keys: {list(track_info.keys())}")

        is_album_download = bool(context.get("is_album_download", False))
        album_info = build_import_album_info(context, force_album=is_album_download)

        if is_album_download:
            if has_clean_metadata:
                logger.info("Album context with clean metadata found - using normalized album info")
            else:
                logger.warning("Album context found without clean metadata - using normalized album info")
        elif not album_info.get('is_album'):
            logger.info("Single track download - attempting album detection")
            detected_album_info = detect_album_info_web(context, artist_context)
            if detected_album_info:
                album_info = detected_album_info

        if album_info and album_info['is_album'] and not is_album_download:
            logger.info(
                "SMART ALBUM GROUPING for track=%r original_album=%r",
                album_info.get('clean_track_name', 'Unknown'),
                album_info.get('album_name', 'None'),
            )
            original_album = original_search.get("album") if original_search.get("album") else None
            consistent_album_name = resolve_album_group(artist_context, album_info, original_album)
            album_info['album_name'] = consistent_album_name
            logger.info("Album grouping complete: final_album=%r", consistent_album_name)
        elif album_info and album_info['is_album'] and is_album_download:
            logger.info(
                "EXPLICIT ALBUM DOWNLOAD - preserving album name=%r; skipping smart grouping",
                album_info.get('album_name', 'None'),
            )

        file_ext = os.path.splitext(file_path)[1]
        clean_track_name = get_import_clean_title(
            context,
            album_info=album_info,
            default=original_search.get('title', 'Unknown Track'),
        )
        # Resolve track_number from the richest available source.
        # See ``core/imports/track_number.py`` for the resolution
        # chain — pure function, unit-tested in isolation, single
        # place to fix the rule.
        from core.imports.track_number import (
            resolve_track_number,
            read_embedded_track_number,
            track_number_from_directory_order,
        )
        track_info_for_resolve = context.get('track_info') if isinstance(context, dict) else None
        # "Track 01" bug: a single Deezer track is matched via an endpoint
        # that omits track_position, so the context never carried the real
        # number. The downloaded file itself does (deemix/source wrote it),
        # so read it as a source between metadata and the filename guess.
        embedded_track_number = read_embedded_track_number(file_path)
        track_number = resolve_track_number(
            album_info, track_info_for_resolve, file_path,
            embedded_track_number=embedded_track_number,
        )
        logger.debug(
            "Final track_number processing: source=%s album_info=%s resolved=%s",
            album_info.get('source', 'unknown'),
            album_info.get('track_number', 'NOT_FOUND'),
            track_number,
        )
        if not isinstance(track_number, int) or track_number < 1:
            # §16.3(a): before collapsing the WHOLE album onto track 1, fall back
            # to this file's sorted position among its audio siblings on disk
            # (album bundles stage all files into one directory together). This
            # only ever replaces the constant-1 default, so it can never override
            # a number a real source produced — it just stops every un-numbered
            # track claiming position 1 and all-but-one showing as "missing".
            #
            # Restricted to genuine album-bundle downloads: a plain
            # (non-bundle) download lands in a SHARED flat directory (e.g.
            # Soulseek's download_path) that can hold other, unrelated
            # in-flight downloads at the same time — applying the fallback
            # there would compute this file's position among files from a
            # completely different download, not a real position within
            # its own album.
            scan_order = (
                track_number_from_directory_order(file_path)
                if is_album_download else None
            )
            if scan_order is not None:
                logger.warning(
                    "No per-track number from any source; using scan-order "
                    "position %s from directory siblings (avoids album collapse "
                    "onto track 1)", scan_order,
                )
                track_number = scan_order
            else:
                logger.error(f"Invalid track number ({track_number}), defaulting to 1")
                track_number = 1

        logger.debug(f"FINAL track_number used for filename: {track_number}")
        album_info['track_number'] = track_number
        album_info['clean_track_name'] = clean_track_name
        logger.info(f"[FIX] Updated album_info track_number to {track_number} for consistent metadata")

        # Sync the disc number the SAME way (and via the SAME resolver) the embedded
        # tag will use — otherwise the "Disc N" folder is built from album_info's
        # original disc while the tag takes the per-track disc, so a disc-2/3 track
        # lands in the Disc 1 folder and every disc's tracks collapse together (Sokhi).
        from core.imports.track_number import resolve_disc_for_track
        _resolved_disc = resolve_disc_for_track(get_import_original_search(context), album_info)
        if album_info.get('disc_number') != _resolved_disc:
            logger.info(f"[FIX] Updated album_info disc_number to {_resolved_disc} for consistent metadata")
        album_info['disc_number'] = _resolved_disc

        final_path, _ = build_final_path_for_track(context, artist_context, album_info, file_ext)
        # #999 atomic album publish (opt-in): redirect to a private staging mirror
        # for fresh whole-album batches; returns final_path unchanged otherwise.
        final_path = _maybe_stage_album_track(context, final_path)
        logger.info(f"Resolved path: '{final_path}'")
        context['_final_processed_path'] = final_path

        try:
            logger.warning(f"[Metadata Input] artist: '{artist_context.get('name', 'MISSING')}' (id: {artist_context.get('id', 'MISSING')})")
            if album_info:
                logger.warning(
                    f"[Metadata Input] album: '{album_info.get('album_name', 'MISSING')}', "
                    f"track#: {album_info.get('track_number', 'MISSING')}, disc#: {album_info.get('disc_number', 'MISSING')}, "
                    f"source: {album_info.get('source', 'unknown')}"
                )
            else:
                logger.info("[Metadata Input] album_info: None (single track)")
            _enhance_started = time.time()
            enhance_file_metadata(file_path, context, artist_context, album_info, runtime=metadata_runtime)
            # The enhancement block is the pipeline's biggest variable cost
            # (external source lookups) and used to be a silent multi-minute
            # gap in the log — always say how long it took.
            logger.info(f"Metadata enhancement took {time.time() - _enhance_started:.1f}s")
        except Exception as meta_err:
            import traceback
            pp_logger.info(f"[inner] Metadata enhancement FAILED for {context_key}: {meta_err}\n{traceback.format_exc()}")
            if should_wipe_tags_on_enhancement_failure(has_clean_metadata):
                wipe_source_tags(file_path)
            else:
                logger.warning(
                    "[Metadata] Enhancement failed but import has clean/matched metadata — "
                    "preserving the file's existing tags (not wiping): %s",
                    os.path.basename(file_path))

        if _upgrade_snapshot is not None:
            file_path, _upgrade_companions = _prepare_upgrade_artifact(
                file_path, context, _upgrade_snapshot.profile)
            file_ext = os.path.splitext(file_path)[1]
            final_path, _ = build_final_path_for_track(
                context, artist_context, album_info, file_ext)
            final_path = _maybe_stage_album_track(context, final_path)
            context['_final_processed_path'] = final_path
            _upgrade_decision = _decide_snapshot_upgrade(
                _upgrade_snapshot, file_path)
            if _upgrade_decision.applicable and not _upgrade_decision.allowed:
                reason = f"Upgrade rejected: {_upgrade_decision.reason}"
                context['_upgrade_rejected'] = True
                context['_upgrade_failure_msg'] = reason
                try:
                    quarantine_path = move_to_quarantine(
                        file_path, context, reason, automation_engine,
                        trigger='quality')
                    _mark_task_quarantined(context, quarantine_path)
                except Exception as quarantine_error:
                    logger.error(
                        "Upgrade quarantine failed (%s) — retaining the old "
                        "primary and transformed incoming artifact",
                        quarantine_error,
                    )
                with matched_context_lock:
                    matched_downloads_context.pop(context_key, None)
                task_id = context.get('task_id')
                batch_id = context.get('batch_id')
                if task_id and _requeue_quarantined_task_for_retry(
                    task_id, batch_id, 'upgrade_quality'
                ):
                    return
                if task_id:
                    with tasks_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'failed'
                            download_tasks[task_id]['error_message'] = reason
                    if batch_id:
                        _notify_download_completed(batch_id, task_id, success=False)
                return
            if _upgrade_decision.applicable:
                logger.info("[Upgrade] %s", _upgrade_decision.reason)

        _enhance_source_info = context.get('track_info', {}).get('source_info') or {}
        if isinstance(_enhance_source_info, str):
            try:
                _enhance_source_info = json.loads(_enhance_source_info)
            except (json.JSONDecodeError, TypeError):
                _enhance_source_info = {}
        is_enhance_download = _enhance_source_info.get('enhance', False)
        # "Force download" is replace-intent: the user explicitly re-downloaded
        # something they already own, so the metadata-protection skip must not
        # discard it (#1045). Rides the same replace path as enhance — the
        # short-file replacement guard above still applies.
        force_replace = _batch_force_replace(context)
        # F-10 history step: set when replacement is selected, then journaled
        # only after the atomic publish succeeds.
        _verified_upgrade = bool(
            _upgrade_decision
            and _upgrade_decision.applicable
            and _upgrade_decision.allowed
        )
        _upgrade_old_path = (
            _upgrade_decision.existing_path if _verified_upgrade else None)
        _upgrade_old_local = (
            _upgrade_decision.existing_resolved_path if _verified_upgrade else None)
        _replace_reason: str | None = "quality_upgrade" if _verified_upgrade else None

        logger.info(f"Moving '{os.path.basename(file_path)}' to '{final_path}'")
        if os.path.exists(final_path):
            if not os.path.exists(file_path):
                logger.info(f"[Protection] Destination exists and source already gone - file already transferred: {os.path.basename(final_path)}")
                _confirm_existing_file_bookkeeping(
                    context, artist_context, album_info)
                return
            # THE backstop for sella's incident: an upgrade/replace must NEVER
            # swap a good library file for a materially shorter one. Before any
            # branch atomically publishes over final_path, compare decoded lengths.
            # before any of them, compare the REAL decoded lengths. A 30s HiFi
            # preview replacing a 220s track is caught here even if every header
            # (and AcoustID) was faked, and even for files no other gate saw.
            if not _replacement_length_is_safe(final_path, file_path):
                logger.error(
                    "[Protection] Refusing to replace %s — the incoming file is "
                    "far shorter (likely a preview/truncated clip). Quarantining it.",
                    os.path.basename(final_path))
                try:
                    qpath = move_to_quarantine(
                        file_path, context,
                        "Replacement guard: incoming file is far shorter than the "
                        "library file it would replace (preview/truncated clip)",
                        automation_engine, trigger='integrity')
                    _mark_task_quarantined(context, qpath)
                except Exception as _qe:   # noqa: BLE001
                    logger.error("[Protection] Could not quarantine the short replacement "
                                 "(leaving in place, NOT replacing): %s", _qe)
                task_id = context.get('task_id')
                batch_id = context.get('batch_id')
                if task_id:
                    with tasks_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'failed'
                            download_tasks[task_id]['error_message'] = (
                                "Replacement rejected: incoming file is far shorter "
                                "than the existing library file")
                    if batch_id:
                        _notify_download_completed(batch_id, task_id, success=False)
                with matched_context_lock:
                    if context_key in matched_downloads_context:
                        del matched_downloads_context[context_key]
                return
            try:
                from mutagen import File as MutagenFile
                existing_file = MutagenFile(final_path)
                has_metadata = existing_file is not None and len(existing_file.tags or {}) > 2
                _same_path_upgrade = bool(
                    _upgrade_old_local
                    and os.path.normcase(os.path.normpath(_upgrade_old_local))
                    == os.path.normcase(os.path.normpath(final_path))
                )
                if _same_path_upgrade:
                    logger.info(
                        "[Upgrade] Atomically replacing %s after real-quality comparison",
                        os.path.basename(final_path),
                    )
                elif has_metadata and not is_enhance_download and not force_replace:
                    _replace_lower = _resolve_context_quality_profile(context).get(
                        'replace_lower_quality',
                        config_manager.get('import.replace_lower_quality', False))
                    if _replace_lower:
                        _existing_tier = get_quality_tier_from_extension(final_path)
                        _incoming_tier = get_quality_tier_from_extension(file_path)
                        if _incoming_tier[1] < _existing_tier[1]:
                            logger.info(f"[Quality Replace] Replacing {_existing_tier[0]} with {_incoming_tier[0]}: {os.path.basename(final_path)}")
                            _replace_reason = "quality_upgrade"
                        else:
                            logger.info(
                                f"[Protection] Existing file is same or better quality ({_existing_tier[0]} vs {_incoming_tier[0]}) - skipping: "
                                f"{os.path.basename(final_path)}"
                            )
                            try:
                                os.remove(file_path)
                            except FileNotFoundError:
                                pass
                            except Exception as e:
                                logger.error(f"[Protection] Error removing redundant file: {e}")
                            if os.path.exists(file_path):
                                context['_context_failure_msg'] = 'could not remove redundant import source'
                            else:
                                _confirm_existing_file_bookkeeping(
                                    context, artist_context, album_info)
                            return
                    else:
                        logger.info(f"[Protection] Existing file already has metadata enhancement - skipping overwrite: {os.path.basename(final_path)}")
                        logger.info(f"[Protection] Removing redundant download file: {os.path.basename(file_path)}")
                        try:
                            os.remove(file_path)
                        except FileNotFoundError:
                            logger.error(f"[Protection] Could not remove redundant file (already gone): {file_path}")
                        except Exception as e:
                            logger.error(f"[Protection] Error removing redundant file: {e}")
                        if os.path.exists(file_path):
                            context['_context_failure_msg'] = 'could not remove redundant import source'
                        else:
                            _confirm_existing_file_bookkeeping(
                                context, artist_context, album_info)
                        return
                elif is_enhance_download or force_replace:
                    if is_enhance_download:
                        logger.info(f"[Enhance] Quality enhance mode — replacing existing file: {os.path.basename(final_path)}")
                    else:
                        logger.info(f"[Force] User-forced re-download — replacing existing file: {os.path.basename(final_path)}")
                    _replace_reason = "enhance" if is_enhance_download else "force"
                else:
                    logger.info(f"[Protection] Existing file lacks metadata - safe to overwrite: {os.path.basename(final_path)}")
                    _replace_reason = "no_metadata_overwrite"
            except Exception as check_error:
                logger.error(f"[Protection] Error checking existing file metadata, proceeding with overwrite: {check_error}")
                _replace_reason = "metadata_check_error_overwrite"

        if not os.path.exists(file_path):
            if os.path.exists(final_path):
                logger.info(f"[Pre-Move] Source already gone and destination exists - another thread completed transfer: {os.path.basename(final_path)}")
                download_cover_art(album_info, os.path.dirname(final_path), context)
                generate_lrc_file(final_path, context, artist_context, album_info)
                _confirm_existing_file_bookkeeping(
                    context, artist_context, album_info)
                return
            expected_dir = os.path.dirname(final_path)
            expected_stem = os.path.splitext(os.path.basename(final_path))[0]
            expected_ext = os.path.splitext(final_path)[1]
            found_variant = None
            check_exts = {expected_ext}
            _qp = _resolve_context_quality_profile(context)
            _lossy_on = _qp.get('lossy_copy_enabled', config_manager.get('lossy_copy.enabled', False))
            _lossy_del = _qp.get('lossy_copy_delete_original', config_manager.get('lossy_copy.delete_original', False))
            if expected_ext == '.flac' and _lossy_on and _lossy_del:
                _lossy_ext_map = {'mp3': '.mp3', 'opus': '.opus', 'aac': '.m4a'}
                _lossy_codec = _qp.get('lossy_copy_codec', config_manager.get('lossy_copy.codec', 'mp3'))
                check_exts.add(_lossy_ext_map.get(_lossy_codec, '.mp3'))
            if os.path.exists(expected_dir):
                for f in os.listdir(expected_dir):
                    f_ext = os.path.splitext(f)[1].lower()
                    if f_ext in check_exts and os.path.splitext(f)[0].startswith(expected_stem):
                        found_variant = os.path.join(expected_dir, f)
                        break
            if found_variant:
                logger.debug(f"[Pre-Move] Source gone but found variant in destination (stream processor handled it): {os.path.basename(found_variant)}")
                context['_final_processed_path'] = found_variant
                download_cover_art(album_info, expected_dir, context)
                generate_lrc_file(found_variant, context, artist_context, album_info)
                _confirm_existing_file_bookkeeping(
                    context, artist_context, album_info)
                return
            logger.warning(f"[Pre-Move] Source file gone and no matching file in destination: {os.path.basename(file_path)}")
            raise FileNotFoundError(f"Source file vanished before move and destination does not exist: {file_path}")

        if _verified_upgrade:
            if not _upgrade_snapshot_still_current(_upgrade_snapshot):
                raise RuntimeError(
                    "Library-v2 upgrade conflict: primary file or profile changed; "
                    "previous file kept and incoming artifact left recoverable"
                )
            if (_upgrade_old_local and os.path.exists(_upgrade_old_local)
                    and not _replacement_length_is_safe(
                        _upgrade_old_local, file_path)):
                raise RuntimeError(
                    "Library-v2 upgrade rejected: retained artifact is materially "
                    "shorter than the current primary"
                )

        safe_move_file(file_path, final_path)
        for companion_source in _upgrade_companions:
            companion_final = os.path.splitext(final_path)[0] + os.path.splitext(
                companion_source)[1]
            safe_move_file(companion_source, companion_final)
            context.setdefault('_companion_file_paths', []).append(companion_final)
        if _verified_upgrade and _upgrade_old_local:
            same_upgrade_path = (
                os.path.normcase(os.path.normpath(_upgrade_old_local))
                == os.path.normcase(os.path.normpath(final_path))
            )
            if not same_upgrade_path and os.path.exists(_upgrade_old_local):
                try:
                    os.remove(_upgrade_old_local)
                except OSError as retirement_error:
                    # Keep the known-good file and restore the candidate to
                    # staging when a cross-extension retirement cannot finish.
                    try:
                        safe_move_file(final_path, file_path)
                    except Exception as rollback_error:  # noqa: BLE001
                        logger.error("Upgrade rollback also failed: %s", rollback_error)
                    raise RuntimeError(
                        f"Could not retire previous upgrade file: {retirement_error}"
                    ) from retirement_error
                _record_replaced_file_path(context, _upgrade_old_path)
        from core.imports.file_ops import move_companion_sidecars
        move_companion_sidecars(file_path, final_path)
        cleanup_slskd_dedup_siblings(file_path)
        if _replace_reason:
            _journal_previous_file_replaced(context, reason=_replace_reason)

        if is_enhance_download and _enhance_source_info.get('original_file_path'):
            # #1109: the recorded path is the media server's view of the
            # library. Resolve it to this process's mount first, or the
            # superseded file is never found — os.path.exists() said False and
            # the branch below happily logged "Replaced in-place" while the old
            # copy stayed on disk next to the new one.
            _recorded_enhance_path = _enhance_source_info['original_file_path']
            original_enhance_path = _resolve_enhance_original_path(
                _recorded_enhance_path)
            if os.path.normpath(original_enhance_path) != os.path.normpath(final_path) and os.path.exists(original_enhance_path):
                try:
                    os.remove(original_enhance_path)
                    # dd28-08: the new file lands at a DIFFERENT path (new
                    # extension), so the catalog row for the old one has to be
                    # retired explicitly — autolink keys on (track_id, path)
                    # and would otherwise just add a second row.
                    _record_replaced_file_path(context, original_enhance_path)
                    # The catalog row may hold either form of the path; retire
                    # both so a resolved deletion still clears the stored row.
                    _record_replaced_file_path(context, _recorded_enhance_path)
                    old_fmt = os.path.splitext(original_enhance_path)[1]
                    new_fmt = os.path.splitext(final_path)[1]
                    logger.info(f"[Enhance] Upgraded {old_fmt} → {new_fmt}: {os.path.basename(final_path)}")
                except Exception as e:
                    logger.error(f"[Enhance] Could not remove old-format file: {e}")
            elif is_enhance_download:
                old_fmt = _enhance_source_info.get('original_format', 'unknown')
                new_fmt = os.path.splitext(final_path)[1]
                logger.info(f"[Enhance] Replaced in-place ({old_fmt} → {new_fmt}): {os.path.basename(final_path)}")

        download_cover_art(album_info, os.path.dirname(final_path), context)
        generate_lrc_file(final_path, context, artist_context, album_info)

        if config_manager.get('post_processing.replaygain_enabled', False):
            try:
                from core.replaygain import analyze_track as _rg_analyze, write_replaygain_tags as _rg_write, is_ffmpeg_available as _rg_ffmpeg_ok, get_target_lufs as _rg_target
                if _rg_ffmpeg_ok():
                    lufs, peak_dbfs = _rg_analyze(final_path)
                    gain_db = _rg_target(config_manager) - lufs   # #1060 target setting
                    _rg_write(final_path, gain_db, peak_dbfs)
                    pp_logger.info(f"ReplayGain: {gain_db:+.2f} dB, peak {peak_dbfs:.2f} dBFS — {os.path.basename(final_path)}")
            except Exception as rg_err:
                pp_logger.debug(f"ReplayGain analysis skipped: {rg_err}")

        _qp_post = _resolve_context_quality_profile(context)
        if _upgrade_snapshot is None:
            final_path = _apply_profile_output_transforms(
                final_path, context, _qp_post)
        else:
            # Upgrade artifacts were transformed transactionally in staging;
            # only persist the verification tag after their final move.
            _persist_verification_status(context, final_path)

        _attach_manual_skip_path(context_key, final_path)

        downloads_path = docker_resolve_path(config_manager.get('soulseek.download_path', './downloads'))
        cleanup_empty_directories(downloads_path, file_path)

        logger.info(f"Post-processing complete for: {context.get('_final_processed_path', final_path)}")

        # Genuine downloads run both catalogue writers before the registration
        # gate. Reorganize is excluded: the runner updates the one existing
        # file row after this pipeline proves the destination exists.
        _record_completed_import_side_effects(
            context, artist_context, album_info, automation_engine,
        )

        try:
            completed_path = context.get('_final_processed_path', final_path)
            batch_id_for_repair = context.get('batch_id')
            if completed_path and batch_id_for_repair and repair_worker:
                album_folder = os.path.dirname(str(completed_path))
                if album_folder:
                    repair_worker.register_folder(batch_id_for_repair, album_folder)
        except Exception as repair_err:
            logger.error(f"[Post-Process] Repair folder registration failed: {repair_err}")

        try:
            completed_path = context.get('_final_processed_path', final_path)
            batch_id_for_consistency = context.get('batch_id')
            if completed_path and batch_id_for_consistency and album_info and album_info.get('is_album'):
                _file_info = {
                    'path': str(completed_path),
                    'track_number': album_info.get('track_number', 1),
                    'disc_number': album_info.get('disc_number', 1),
                    'title': get_import_clean_title(
                        context,
                        album_info=album_info,
                        default=album_info.get('clean_track_name', ''),
                    ),
                }
                with tasks_lock:
                    if batch_id_for_consistency in download_batches:
                        download_batches[batch_id_for_consistency].setdefault('_consistency_files', []).append(_file_info)
        except Exception as cons_err:
            logger.error(f"[Post-Process] Album consistency registration failed: {cons_err}")

        if context.get('_library_reorganize') is not True:
            try:
                check_and_remove_from_wishlist(context)
            except Exception as wishlist_error:
                logger.error(f"[Post-Process] Error checking wishlist removal: {wishlist_error}")

        task_id = context.get('task_id')
        batch_id = context.get('batch_id')
        if task_id and batch_id:
            logger.info(f"[Post-Process] Calling completion callback for task {task_id} in batch {batch_id}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['stream_processed'] = True
                    download_tasks[task_id]['status'] = 'completed'
                    # Additive: record where the imported file landed so downstream
                    # (playlist materialization) knows the real path of a freshly
                    # downloaded track without re-resolving it.
                    download_tasks[task_id]['final_file_path'] = context.get('_final_processed_path')
                    logger.info(f"[Post-Process] Marked task {task_id} as completed")
            _notify_download_completed(batch_id, task_id, success=True)
        context['_pipeline_import_succeeded'] = True

    except Exception as e:
        import traceback
        pp_logger.info(f"[inner] EXCEPTION in post-processing for {context_key}: {e}")
        pp_logger.info(traceback.format_exc())
        logger.error(f"\nCRITICAL ERROR in post-processing for {context_key}: {e}")
        traceback.print_exc()

        source_exists = os.path.exists(file_path) if file_path else False
        if source_exists:
            context.setdefault('_context_failure_msg', f'post-processing failed: {e}')
            if context_key in processed_download_ids:
                processed_download_ids.remove(context_key)
                logger.warning(f"Removed {context_key} from processed set - will retry on next check")
            with matched_context_lock:
                if context_key not in matched_downloads_context:
                    matched_downloads_context[context_key] = context
                    logger.warning(f"Re-added {context_key} to context for retry")
        else:
            if _recover_moved_file_bookkeeping(
                context,
                locals().get('artist_context'),
                locals().get('album_info'),
            ):
                context['_pipeline_import_succeeded'] = True
                logger.warning(
                    "Source consumed but destination exists; recovered library "
                    "bookkeeping for %s",
                    context_key,
                )
            else:
                context.setdefault(
                    '_context_failure_msg',
                    'the file was moved, but Library v2 could not register it',
                )
                logger.warning(f"Source file gone, not retrying: {context_key}")
    finally:
        file_lock.release()
        with post_process_locks_lock:
            post_process_locks.pop(context_key, None)
        if _upgrade_track_lock is not None:
            _release_upgrade_lock(
                _server_upgrade_intent.track_id, _upgrade_track_lock)


def _attempt_version_mismatch_fallback(context, task_id, batch_id, runtime, metadata_runtime):
    """Opt-in last resort once AcoustID retries are exhausted: accept the best
    quarantined version-mismatch candidate for this track instead of failing.

    Delegates the decision + safety rules to
    ``core.imports.version_mismatch_fallback`` (version-mismatch only, all the
    same matched version, >= min_count, AcoustID-only bypass). Returns True when
    a candidate was accepted and re-dispatched — the caller then skips marking
    the task failed.
    """
    try:
        download_path = docker_resolve_path(
            config_manager.get('soulseek.download_path', './downloads')
        )
        quarantine_dir = os.path.join(download_path, 'ss_quarantine')
        restore_dir = os.path.join(download_path, 'Transfer')
        expected_title = get_import_clean_title(context, default='')
        expected_artist = get_import_clean_artist(context, default='')
        if not expected_title or not expected_artist:
            return False

        def _reprocess(restored_path, ctx, tid, bid):
            new_key = f"vmfallback_{tid}_{int(time.time())}"
            threading.Thread(
                target=lambda: post_process_matched_download_with_verification(
                    new_key, ctx, restored_path, tid, bid, runtime, metadata_runtime
                ),
                daemon=True,
            ).start()

        return try_accept_version_mismatch_fallback(
            quarantine_dir=quarantine_dir,
            restore_dir=restore_dir,
            expected_title=expected_title,
            expected_artist=expected_artist,
            task_id=task_id,
            batch_id=batch_id,
            config_get=config_manager.get,
            list_entries=list_quarantine_entries,
            approve_entry=approve_quarantine_entry,
            reprocess=_reprocess,
        )
    except Exception as exc:
        pp_logger.debug("[Version-Mismatch Fallback] skipped due to error: %s", exc)
        return False


def post_process_matched_download_with_verification(context_key, context, file_path, task_id, batch_id, runtime, metadata_runtime=None):
    on_download_completed = getattr(runtime, "on_download_completed", None)

    def _notify_download_completed(batch_id, task_id, success=True):
        if on_download_completed:
            on_download_completed(batch_id, task_id, success=success)

    logger = pp_logger
    try:
        original_task_id = context.pop('task_id', None)
        original_batch_id = context.pop('batch_id', None)
        # #999: the atomic-publish stage redirect runs inside the inner pipeline
        # and needs the batch id — which we just popped so the inner batch
        # accounting stays owned by this wrapper. Stash it under a dedicated key
        # the redirect reads. Flag-gated so flag-off downloads are byte-identical
        # (no extra context key), and popped again before the wrapper continues.
        if original_batch_id and config_manager.get('album_downloads.atomic_publish', False):
            context['_atomic_publish_batch_id'] = original_batch_id
        post_process_matched_download(context_key, context, file_path, runtime, metadata_runtime=metadata_runtime)
        context.pop('_atomic_publish_batch_id', None)
        if original_task_id:
            context['task_id'] = original_task_id
        if original_batch_id:
            context['batch_id'] = original_batch_id

        if context.get('_upgrade_rejected'):
            failure_msg = context.get(
                '_upgrade_failure_msg', 'Downloaded file was not a valid upgrade')
            with matched_context_lock:
                matched_downloads_context.pop(context_key, None)
            if _requeue_quarantined_task_for_retry(
                task_id, batch_id, 'upgrade_quality'
            ):
                return
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = failure_msg
                    entry_id = context.get('_quarantine_entry_id')
                    if entry_id:
                        download_tasks[task_id]['quarantine_entry_id'] = entry_id
            _notify_download_completed(batch_id, task_id, success=False)
            return

        # Quality / audio-guard quarantine. The wrapper must NOT continue to the
        # "assume success" fall-through — that marked the quarantined file
        # Completed, so the same track showed in BOTH Completed and Quarantine
        # (e.g. an MP3-VBR rejected by a FLAC-only profile).
        #
        # And the retry/fail marking has to happen HERE, not in the inner
        # pipeline: this wrapper pops task_id/batch_id out of the context before
        # the inner run (line ~1434), so the inner branch's own
        # ``context.get('task_id')`` was None — its requeue returned False
        # unfired and its failed-marking no-op'd. This code used to just return
        # on the claim that "the inner pipeline already handled retry/fail",
        # which is only ever true for DIRECT callers; through the wrapper the
        # task stayed in post_processing until Stuck Detection V2 reaped it at
        # 1800s, wedging a download slot for half an hour per quarantine
        # (Sokhi's "stuck on Processing" report, Aug 16). Same stash-and-apply
        # pattern _mark_task_quarantined already uses for the entry id.
        if context.get('_bitdepth_rejected') or context.get('_silence_rejected'):
            trigger = 'quality' if context.get('_bitdepth_rejected') else 'silence'
            reason = context.get('_quarantine_reject_reason') or \
                f"{'Quality' if trigger == 'quality' else 'Audio'} guard rejected the file"
            # Requeue-first, fail-second — the same order the direct path uses.
            # A successful requeue means the task is live again on its next-best
            # candidate, so it must not be marked failed.
            if _requeue_quarantined_task_for_retry(task_id, batch_id, trigger):
                logger.info(
                    f"Task {task_id} quarantined by the {trigger} guard — "
                    f"retrying next-best candidate"
                )
                return
            logger.info(
                f"Task {task_id} quarantined by the {trigger} guard with no "
                f"retry candidate — marking failed: {reason}"
            )
            if task_id:
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        download_tasks[task_id]['error_message'] = reason
            if task_id and batch_id:
                _notify_download_completed(batch_id, task_id, success=False)
            return

        if context.get('_race_guard_failed'):
            logger.info(f"Race guard: source file gone for task {task_id} — marking as failed")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = 'Source file was already processed or removed by another task'
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        if context.get('_acoustid_quarantined'):
            # Race-condition guard: if the user approved an alternative quarantine
            # entry for this track while this download was already in flight, the
            # pipeline will have created a new quarantine entry for the just-finished
            # file. Delete that entry and exit — the user's choice already won.
            _approved_alt = False
            if task_id:
                with tasks_lock:
                    _ct = download_tasks.get(task_id)
                    if isinstance(_ct, dict) and _ct.get('_quarantine_approved_alternative'):
                        _approved_alt = True
            if _approved_alt:
                _eid = context.get('_quarantine_entry_id')
                if _eid:
                    try:
                        from core.imports.guards import _get_config_manager
                        _dl_dir = _get_config_manager().get('soulseek.download_path', './downloads')
                        _qdir = os.path.join(docker_resolve_path(_dl_dir), 'ss_quarantine')
                        delete_quarantine_entry(_qdir, _eid)
                        logger.info(
                            f"[Quarantine] Discarded late entry {_eid} — task {task_id} "
                            f"was cancelled by prior quarantine approval"
                        )
                    except Exception as _dqe:
                        logger.debug(f"[Quarantine] Could not clean up late entry {_eid}: {_dqe}")
                _notify_download_completed(batch_id, task_id, success=False)
                return

            failure_msg = context.get('_acoustid_failure_msg', 'AcoustID verification failed')
            # Before failing outright, try the next-best candidate. The wrong
            # file was just quarantined; re-running the worker (with the bad
            # source flagged used) picks the runner-up match instead.
            with matched_context_lock:
                matched_downloads_context.pop(context_key, None)
            if _requeue_quarantined_task_for_retry(task_id, batch_id, 'acoustid'):
                logger.info(
                    f"AcoustID mismatch for task {task_id} — retrying next-best candidate: {failure_msg}"
                )
                return
            # Retries exhausted. Opt-in last resort: if every quarantined
            # candidate for this track failed the SAME version mismatch (e.g. all
            # instrumental), accept the best one rather than leaving it missing.
            if _attempt_version_mismatch_fallback(context, task_id, batch_id, runtime, metadata_runtime):
                return
            logger.info(f"File was quarantined by AcoustID verification (task={task_id}): {failure_msg}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = f"AcoustID verification failed: {failure_msg}"
                    _eid = context.get('_quarantine_entry_id')
                    if _eid:
                        download_tasks[task_id]['quarantine_entry_id'] = _eid
            _notify_download_completed(batch_id, task_id, success=False)
            return

        if context.get('_simple_download_completed'):
            expected_final_path = context.get('_final_path')
            if expected_final_path and os.path.exists(expected_final_path):
                with tasks_lock:
                    if task_id in download_tasks:
                        _mark_task_completed(task_id, context.get('track_info'))
                with matched_context_lock:
                    if context_key in matched_downloads_context:
                        del matched_downloads_context[context_key]
                _notify_download_completed(batch_id, task_id, success=True)
                return
            logger.info(
                f"FAILED simple download file not found at: {expected_final_path} "
                f"(task={task_id}, context={context_key})"
            )
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = (
                        f"Downloaded file not found at expected location: {os.path.basename(expected_final_path)}"
                    )
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        # Integrity rejection — the inner pipeline quarantined the file
        # because audio integrity (size / parse / duration) failed. Wrapper
        # was previously falling through to "assuming success" because
        # quarantined files have no _final_processed_path, which left the
        # task showing ✅ Completed in the UI even though the file is in
        # quarantine. Reported by user when downloading Mr. Morale: 3
        # tracks (Rich Interlude, Savior Interlude, Savior) showed
        # Completed in the modal but were missing on disk because their
        # source files failed integrity and were quarantined.
        if context.get('_integrity_failure_msg'):
            failure_msg = context.get('_integrity_failure_msg', 'unknown')
            # Integrity/duration mismatch (truncated transfer, wrong-length cut,
            # etc). Same treatment as an AcoustID mismatch: quarantine the bad
            # file and retry the next-best candidate before failing.
            with matched_context_lock:
                matched_downloads_context.pop(context_key, None)
            if _requeue_quarantined_task_for_retry(task_id, batch_id, 'integrity'):
                logger.info(
                    f"Integrity check failed for task {task_id} — retrying next-best candidate: {failure_msg}"
                )
                return
            logger.error(
                f"Task {task_id} failed integrity check — marking failed: {failure_msg}"
            )
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = (
                        f"File integrity check failed: {failure_msg}"
                    )
                    _eid = context.get('_quarantine_entry_id')
                    if _eid:
                        download_tasks[task_id]['quarantine_entry_id'] = _eid
            _notify_download_completed(batch_id, task_id, success=False)
            return

        # Race guard failure — inner code set this when the source file
        # disappeared and there was no known destination to fall back on
        # (vs the legitimate race-guard skip where a sibling thread
        # already moved the file to its destination).
        if context.get('_race_guard_failed'):
            logger.error(f"Task {task_id} failed race guard — source file gone with no known destination")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = (
                        "Source file disappeared before post-processing could complete"
                    )
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        expected_final_path = context.get('_final_processed_path')
        if not context.get('_pipeline_import_succeeded'):
            # A file on disk is only half an import.  Every successful path in
            # the inner pipeline now sets this flag *after* the strict lib2
            # registration (including race/recovery paths), so absence is an
            # explicit failure rather than an unverifiable success.
            failure_msg = str(context.get(
                '_context_failure_msg',
                'post-processing did not complete Library v2 registration',
            ))
            logger.error(
                "Post-processing did not import (task=%s): %s", task_id, failure_msg,
            )
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = failure_msg
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return
        if not expected_final_path:
            failure_msg = 'post-processing reported success without a destination path'
            logger.error("Task %s: %s", task_id, failure_msg)
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = failure_msg
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        if os.path.exists(expected_final_path):
            redownload_ctx = None
            with tasks_lock:
                if task_id in download_tasks:
                    _mark_task_completed(task_id, context.get('track_info'))
                    # The playback queue polls the task status and needs the
                    # verified library path before it can replace its original
                    # ``missing`` row with a playable one.  The inner pipeline
                    # cannot persist this itself because this wrapper removes
                    # task_id/batch_id while post-processing runs.  Keep the
                    # path on the task at the same point we mark it completed.
                    download_tasks[task_id]['final_file_path'] = expected_final_path
                    download_tasks[task_id]['metadata_enhanced'] = True
                    if context.get('_verification_status'):
                        download_tasks[task_id]['verification_status'] = context['_verification_status']
                    if context.get('_history_id'):
                        download_tasks[task_id]['history_id'] = context['_history_id']
                    if context.get('_audio_quality'):
                        download_tasks[task_id]['quality'] = context['_audio_quality']
                    redownload_ctx = download_tasks[task_id].get('_redownload_context')

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            if redownload_ctx:
                try:
                    old_path = redownload_ctx.get('old_file_path')
                    lib_track_id = redownload_ctx.get('library_track_id')
                    if redownload_ctx.get('delete_old_file') and old_path and os.path.exists(old_path):
                        if os.path.normpath(old_path) != os.path.normpath(expected_final_path):
                            os.remove(old_path)
                            # dd28-08: the redownload hook used to update only
                            # the LEGACY tracks.file_path, leaving the lib2 row
                            # pointing at the file it had just deleted.
                            _record_replaced_file_path(context, old_path)
                            _retire_lib2_path_after_redownload(old_path, expected_final_path)
                            logger.info(f"[Redownload] Deleted old file: {old_path}")
                    # Guarded on `old_path`, because that is what the repoint
                    # matches on — the row is keyed by the path we stored, not
                    # by the track id the old legacy UPDATE used. Report the
                    # rows actually moved: zero is normal once the delete branch
                    # above has retired the row, but it is not "updated".
                    if old_path and expected_final_path:
                        _rd_db = get_database()
                        _rd_conn = _rd_db._get_connection()
                        from core.library2.track_files import repoint_file_path
                        _rd_moved = repoint_file_path(_rd_conn, old_path, expected_final_path)
                        _rd_conn.commit()
                        _rd_conn.close()
                        logger.info(
                            f"[Redownload] Repointed {_rd_moved} lib2 file row(s) "
                            f"for track {lib_track_id}")
                except Exception as e:
                    logger.error(f"[Redownload] Post-processing hook error: {e}")

            _notify_download_completed(batch_id, task_id, success=True)
        else:
            track_name = get_import_clean_title(context, default=context_key)
            logger.info(f"FAILED verification for '{track_name}' (task={task_id})")
            logger.info(f"  expected_final_path: {expected_final_path}")
            logger.info(f"  file_path (source): {file_path}, exists={os.path.exists(file_path)}")
            logger.info(
                f"  is_album={context.get('is_album_download', False)}, "
                f"has_clean_data={get_import_has_clean_metadata(context)}"
            )
            expected_dir = os.path.dirname(expected_final_path)
            if os.path.exists(expected_dir):
                dir_contents = os.listdir(expected_dir)
                logger.info(f"  directory contains {len(dir_contents)} files: {dir_contents[:20]}")
            else:
                logger.info(f"  directory does not exist: {expected_dir}")

            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = (
                        f'File verification failed: expected file at {os.path.basename(expected_final_path)} but it was not found after processing'
                    )

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            _notify_download_completed(batch_id, task_id, success=False)
    except Exception as e:
        import traceback
        logger.info(f"EXCEPTION in post-processing for '{context_key}' (task={task_id}): {e}")
        logger.info(traceback.format_exc())
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error_message'] = f"Post-processing verification failed: {str(e)}"
        with matched_context_lock:
            if context_key in matched_downloads_context:
                del matched_downloads_context[context_key]
        _notify_download_completed(batch_id, task_id, success=False)
