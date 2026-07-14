"""Correlate legacy grabs into the acquisition contract.

Roadmap step 3 (docs/library-v2.md section 7): an admin-profile legacy
download gains persistent Request -> Candidate -> Gate-Run -> Grab -> History
correlation. Two legacy dispatch paths feed this adapter:

- **Manual** (slices 1/4): an Interactive-Search grab, with its exact lib2
  entity when present or an explicitly namespaced legacy-shadow identity
  otherwise (``trigger='manual'``). A manual pick is the user asserting the
  match (Lidarr interactive-search semantics).
- **Scheduled** (slices 2/4): a wishlist-worker candidate dispatch, again
  preserving an exact lib2 mirror identity when available and otherwise
  using the server-owned task target as a legacy-shadow identity
  (``trigger='scheduled'``); the pick was made by the legacy worker's own
  battle-tested matching.

Both are strictly observational: the download was already dispatched by the
legacy path, and the Entity-Eligibility-Gate result is recorded, never
enforced. The gate run is persisted with ``forced=0`` so the
force<->quarantine bridge can never auto-approve anything on behalf of a
correlated grab; source selection, quality gating, AcoustID, quarantine and
retry stay owned by the shared main pipeline at import time.

Bundle-scope sources (usenet/torrent/lidarr) are deliberately excluded:
their client plugins already record their own grab rows, and their full
conversion is the acquisition-native bundle path.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Dict, Mapping, Optional

from utils.logging_config import get_logger


logger = get_logger("acquisition.manual_grab")

# Context marker carried through matched-download registration, quarantine
# sidecars and post-processing back to pipeline_callback.
GRAB_MARKER = "_acquisition_grab_download_id"

MANUAL_GRAB_KEY_PREFIX = "manual-grab:"
SCHEDULED_GRAB_KEY_PREFIX = "scheduled-grab:"
CORRELATION_ENFORCEMENT_KEY = "features.acquisition_contract_enforce"

# Mirrors retry_state.RETRY_STATE_TTL_SECONDS: a correlated grab whose
# download never reached a pipeline outcome must not stay "grabbing" forever.
MANUAL_GRAB_TTL_SECONDS = 7 * 24 * 60 * 60


def correlation_enforcement_enabled(
    config_get: Optional[Callable[..., Any]] = None,
) -> bool:
    """Whether converted legacy consumers must prepare before dispatch.

    Default-off is deliberate: operators can observe the correlation path
    before turning bookkeeping availability into an admission requirement.
    """
    if config_get is None:
        from config.settings import config_manager
        config_get = config_manager.get
    try:
        return config_get(CORRELATION_ENFORCEMENT_KEY, False) is True
    except Exception:
        return False


def _resolve_lib2_scope_entity(
    conn: Any, lib2_context: Optional[Mapping[str, Any]],
) -> tuple[Optional[str], Optional[int]]:
    if not lib2_context:
        return None, None
    track_id = lib2_context.get("track_id")
    album_id = lib2_context.get("album_id")
    if track_id:
        row = conn.execute(
            "SELECT MIN(recording_id) FROM lib2_release_tracks WHERE track_id=?",
            (int(track_id),),
        ).fetchone()
        if row is not None and row[0]:
            return "recording", int(row[0])
    if album_id:
        return "release_group", int(album_id)
    return None, None


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("name") or value.get("title")
        text = str(value or "").strip()
        if text and text.lower() != "unknown":
            return text
    return None


def _first_artist(context: Mapping[str, Any]) -> Optional[str]:
    artists = context.get("artists")
    if isinstance(artists, (list, tuple)) and artists:
        return _first_text(artists[0])
    return _first_text(
        context.get("artist"),
        context.get("artist_name"),
        context.get("spotify_clean_artist"),
    )


def _legacy_shadow_context(
    target_context: Optional[Mapping[str, Any]],
    search_result: Mapping[str, Any],
) -> tuple[Optional[int], Optional[Any], Dict[str, Any]]:
    """Build a non-lib2 recording identity for observational correlation.

    ``acquisition_requests.entity_id`` is a positive integer without a
    catalog foreign key. Legacy consumers often have a durable provider id
    but no lib2 row, so namespace a stable digest explicitly instead of
    inventing a lib2 entity or copying catalog rows.
    """
    from core.acquisition.eligibility_gate import CatalogContext

    target = dict(target_context or {})
    artist = _first_artist(target) or _first_artist(search_result)
    title = _first_text(
        target.get("name"), target.get("title"), target.get("track_name"),
        target.get("spotify_clean_title"), search_result.get("title"),
    )
    album = _first_text(
        target.get("album"), target.get("album_name"),
        target.get("spotify_clean_album"), search_result.get("album"),
    )
    if not artist and not title:
        return None, None, {}

    raw_source_info = target.get("source_info")
    if isinstance(raw_source_info, str):
        try:
            raw_source_info = json.loads(raw_source_info)
        except (TypeError, ValueError):
            raw_source_info = {}
    source_info = raw_source_info if isinstance(raw_source_info, Mapping) else {}
    provider_id = _first_text(
        target.get("id"), target.get("track_id"),
        target.get("spotify_track_id"), target.get("deezer_track_id"),
        target.get("itunes_track_id"), source_info.get("track_id"),
    )
    provider = _first_text(
        target.get("source"), source_info.get("source"),
    ) or "legacy"
    if provider_id:
        identity = {
            "provider": provider.casefold(),
            "provider_id": provider_id,
        }
    else:
        identity = {
            "provider": provider.casefold(),
            "artist": (artist or "").casefold(),
            "title": (title or "").casefold(),
            "album": (album or "").casefold(),
        }
    digest = hashlib.blake2b(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        digest_size=8,
        person=b"ss-acq-v1",
    ).digest()
    entity_id = int.from_bytes(digest, "big") & ((1 << 63) - 1)
    if entity_id == 0:
        entity_id = 1
    snapshot = {"artist": artist, "title": title, "album": album}
    return (
        entity_id,
        CatalogContext(artist=artist, release_title=title, track_count=1),
        {
            "entity_namespace": "legacy_shadow",
            "legacy_identity_digest": digest.hex(),
            "catalog_snapshot": snapshot,
        },
    )


def _context_quality_profile_id(
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]],
) -> Optional[int]:
    for context in (lib2_context, target_context):
        if not isinstance(context, Mapping):
            continue
        value = context.get("quality_profile_id")
        if value:
            return int(value)
        source_info = context.get("source_info")
        if isinstance(source_info, str):
            try:
                source_info = json.loads(source_info)
            except (TypeError, ValueError):
                source_info = {}
        if isinstance(source_info, Mapping) and source_info.get("quality_profile_id"):
            return int(source_info["quality_profile_id"])
    return None


def _candidate_title(search_result: Mapping[str, Any]) -> str:
    title = str(search_result.get("title") or "").strip()
    if title and title.lower() != "unknown":
        return title
    filename = str(search_result.get("filename") or "").replace("\\", "/")
    basename = filename.rsplit("/", 1)[-1].strip()
    return basename or "Unknown manual grab"


def _candidate_facts(search_result: Mapping[str, Any], *, scope: str) -> Dict[str, Any]:
    quality = str(search_result.get("quality") or "").strip().lower()
    known_formats = {"flac", "mp3", "ogg", "opus", "aac", "m4a", "alac", "wav", "wma"}
    return {
        "artist": search_result.get("artist"),
        # The gate matches release_title against the catalog projection:
        # recording title (== track title) for recording scope, album title
        # for release_group scope.
        "release_title": (
            search_result.get("title") if scope == "recording"
            else search_result.get("album")),
        "format": quality if quality in known_formats else search_result.get("format"),
        "bitrate": search_result.get("bitrate"),
        "sample_rate": search_result.get("sample_rate"),
        "bit_depth": search_result.get("bit_depth"),
        "track_count": 1,
    }


def _correlate_grab(
    conn: Any,
    *,
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]],
    search_result: Mapping[str, Any],
    source: str,
    trigger: str,
    download_id: str,
    idempotency_key: str,
    shadow_source: str,
    dispatch_options: Mapping[str, Any],
    grab_context_extra: Mapping[str, Any],
    legacy_download_id: Optional[str],
    history_event: str,
    rejection_reason_code: str,
    grab_status: str,
    config_get: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, str]]:
    """Shared correlation core for one dispatched legacy grab.

    Returns ``{"download_id", "request_id"}`` markers for the download
    context, or None when the grab cannot be correlated (no usable identity,
    bundle-scope source). The caller owns the transaction.
    """
    from core.acquisition import ensure_acquisition_schema
    from core.acquisition.candidates import register_candidate
    from core.acquisition.capabilities import get_source_capabilities
    from core.acquisition.catalog import resolve_request_context
    from core.acquisition.decisions import record_decision
    from core.acquisition.eligibility_gate import EligibilityGate, RuntimeContext
    from core.acquisition.grabs import record_grab
    from core.acquisition.history import record_history_event
    from core.acquisition.requests import create_request, transition_request

    source = str(source or "").strip().lower()
    capabilities = get_source_capabilities(source)
    if capabilities is None or not capabilities.recording_download:
        return None
    scope, entity_id = _resolve_lib2_scope_entity(conn, lib2_context)
    catalog = None
    shadow_options: Dict[str, Any] = {}
    if scope is None or entity_id is None:
        entity_id, catalog, shadow_options = _legacy_shadow_context(
            target_context, search_result)
        scope = "recording"
    if entity_id is None:
        return None
    quality_profile_id = _context_quality_profile_id(
        lib2_context, target_context)
    if not quality_profile_id:
        from core.library2.profile_lookup import default_quality_profile_id
        quality_profile_id = default_quality_profile_id(conn)

    ensure_acquisition_schema(conn)
    search_options: Dict[str, Any] = {
        # Legacy grabs fulfil even release_group intents one file at a time,
        # so every candidate is a recording, never a bundle.
        "content_scope": "recording",
        "shadow_source": shadow_source,
        **shadow_options,
    }
    if lib2_context and lib2_context.get("track_id"):
        search_options["lib2_track_id"] = int(lib2_context["track_id"])
    if lib2_context and lib2_context.get("album_id"):
        search_options["lib2_album_id"] = int(lib2_context["album_id"])
    search_options.update(dispatch_options)

    request, _created = create_request(
        conn,
        profile_id=1,
        scope=scope,
        entity_id=entity_id,
        quality_profile_id=int(quality_profile_id),
        trigger=trigger,
        idempotency_key=idempotency_key,
        search_options=search_options,
    )
    record_history_event(
        conn,
        "request_created",
        request_id=request.id,
        payload={
            "scope": request.scope,
            "entity_id": request.entity_id,
            "quality_profile_id": request.quality_profile_id,
            "trigger": request.trigger,
            "shadow_source": shadow_source,
            "entity_namespace": search_options.get("entity_namespace", "lib2"),
        },
    )
    request = transition_request(
        conn, request.id, "searching", expected_status="pending",
        increment_attempts=True)
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source=source,
        protocol="p2p" if source == "soulseek" else "streaming",
        content_scope="recording",
        server_ref=trigger + ":" + download_id,
        title=_candidate_title(search_result),
        size_bytes=search_result.get("size") or None,
        facts=_candidate_facts(search_result, scope=scope),
        raw_payload={
            "username": search_result.get("username"),
            "filename": search_result.get("filename"),
        },
        now=now,
    )
    request = transition_request(
        conn, request.id, "candidates_ready", expected_status="searching")

    if catalog is None:
        catalog, policy = resolve_request_context(
            conn, request, config_get=config_get)
    else:
        from core.acquisition.catalog import load_effective_policy
        policy = load_effective_policy(
            conn, request.quality_profile_id, config_get=config_get)
    decision = EligibilityGate.evaluate(
        request, candidate, catalog, RuntimeContext(), policy, now=now)
    run = record_decision(conn, decision)

    request = transition_request(
        conn, request.id, "grabbing", expected_status="candidates_ready")
    record_grab(
        conn,
        download_id,
        source,
        title=candidate.title,
        acquisition_request_id=request.id,
        release_candidate_id=candidate.id,
        decision_run_id=run.id,
        context={
            "acquisition_request_id": request.id,
            "release_candidate_id": candidate.id,
            "decision_run_id": run.id,
            "quality_profile_id": request.quality_profile_id,
            "scope": request.scope,
            "entity_id": request.entity_id,
            # The legacy client transfer has its own id.  Keep it only as
            # correlation data: the Downloads cancel endpoint receives this
            # value, whereas the acquisition grab uses its durable synthetic
            # id above.
            "legacy_download_id": str(legacy_download_id) if legacy_download_id else None,
            **grab_context_extra,
        },
        status=grab_status,
    )
    record_history_event(
        conn,
        history_event,
        request_id=request.id,
        candidate_id=candidate.id,
        download_id=download_id,
        reason_code=None if decision.accepted else rejection_reason_code,
        payload={
            "decision_run_id": run.id,
            "accepted": decision.accepted,
            "rejections": [reason.code for reason in decision.rejections],
            "warnings": [reason.code for reason in decision.warnings],
            "source": source,
            **dispatch_options,
        },
    )
    return {"download_id": download_id, "request_id": request.id}


def correlate_manual_grab(
    conn: Any,
    *,
    lib2_context: Optional[Mapping[str, Any]] = None,
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    batch_id: Optional[str] = None,
    legacy_download_id: Optional[str] = None,
    config_get: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, str]]:
    """Correlate one dispatched Interactive-Search grab (trigger=manual)."""
    download_id = "manual-" + str(uuid.uuid4())
    dispatch_options: Dict[str, Any] = {}
    if batch_id:
        dispatch_options["manual_batch_id"] = str(batch_id)
    return _correlate_grab(
        conn,
        lib2_context=lib2_context,
        target_context=target_context,
        search_result=search_result,
        source=source,
        trigger="manual",
        download_id=download_id,
        idempotency_key=MANUAL_GRAB_KEY_PREFIX + download_id,
        shadow_source="legacy_interactive",
        dispatch_options=dispatch_options,
        legacy_download_id=legacy_download_id,
        grab_context_extra={
            "manual_pick": True,
            "manual_batch_id": str(batch_id) if batch_id else None,
        },
        history_event="manual_grab_correlated",
        rejection_reason_code="gate_rejections_overridden_by_manual_pick",
        grab_status="downloading",
        config_get=config_get,
        now=now,
    )


def prepare_manual_grab(
    conn: Any,
    *,
    lib2_context: Optional[Mapping[str, Any]] = None,
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    batch_id: Optional[str] = None,
    config_get: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, str]]:
    """Persist a manual correlation before the external client dispatch."""
    download_id = "manual-" + str(uuid.uuid4())
    dispatch_options: Dict[str, Any] = {}
    if batch_id:
        dispatch_options["manual_batch_id"] = str(batch_id)
    return _correlate_grab(
        conn,
        lib2_context=lib2_context,
        target_context=target_context,
        search_result=search_result,
        source=source,
        trigger="manual",
        download_id=download_id,
        idempotency_key=MANUAL_GRAB_KEY_PREFIX + download_id,
        shadow_source="legacy_interactive",
        dispatch_options=dispatch_options,
        legacy_download_id=None,
        grab_context_extra={
            "manual_pick": True,
            "manual_batch_id": str(batch_id) if batch_id else None,
        },
        history_event="manual_grab_correlated",
        rejection_reason_code="gate_rejections_overridden_by_manual_pick",
        grab_status="submitting",
        config_get=config_get,
        now=now,
    )


def correlate_scheduled_grab(
    conn: Any,
    *,
    lib2_context: Optional[Mapping[str, Any]] = None,
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    task_id: str,
    batch_id: Optional[str] = None,
    legacy_download_id: Optional[str] = None,
    config_get: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, str]]:
    """Correlate one dispatched wishlist-worker grab (trigger=scheduled).

    The legacy worker's own matching picked the candidate; the gate result
    is recorded for observability only. Every dispatch of the same legacy
    task (e.g. the quarantine-retry walk moving to the next candidate) is
    its own request — ``legacy_task_id`` in the search options ties them
    together, and the stale sweep closes rows the pipeline never resolved.
    """
    download_id = "scheduled-" + str(uuid.uuid4())
    dispatch_options: Dict[str, Any] = {"legacy_task_id": str(task_id)}
    if batch_id:
        dispatch_options["legacy_batch_id"] = str(batch_id)
    return _correlate_grab(
        conn,
        lib2_context=lib2_context,
        target_context=target_context,
        search_result=search_result,
        source=source,
        trigger="scheduled",
        download_id=download_id,
        idempotency_key=SCHEDULED_GRAB_KEY_PREFIX + download_id,
        shadow_source="legacy_wishlist_worker",
        dispatch_options=dispatch_options,
        legacy_download_id=legacy_download_id,
        grab_context_extra={
            "manual_pick": False,
            "legacy_task_id": str(task_id),
            "legacy_batch_id": str(batch_id) if batch_id else None,
        },
        history_event="scheduled_grab_correlated",
        rejection_reason_code="gate_rejections_observed_not_enforced",
        grab_status="downloading",
        config_get=config_get,
        now=now,
    )


def prepare_scheduled_grab(
    conn: Any,
    *,
    lib2_context: Optional[Mapping[str, Any]] = None,
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    task_id: str,
    batch_id: Optional[str] = None,
    config_get: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, str]]:
    """Persist a scheduled correlation before the external dispatch."""
    download_id = "scheduled-" + str(uuid.uuid4())
    dispatch_options: Dict[str, Any] = {"legacy_task_id": str(task_id)}
    if batch_id:
        dispatch_options["legacy_batch_id"] = str(batch_id)
    return _correlate_grab(
        conn,
        lib2_context=lib2_context,
        target_context=target_context,
        search_result=search_result,
        source=source,
        trigger="scheduled",
        download_id=download_id,
        idempotency_key=SCHEDULED_GRAB_KEY_PREFIX + download_id,
        shadow_source="legacy_wishlist_worker",
        dispatch_options=dispatch_options,
        legacy_download_id=None,
        grab_context_extra={
            "manual_pick": False,
            "legacy_task_id": str(task_id),
            "legacy_batch_id": str(batch_id) if batch_id else None,
        },
        history_event="scheduled_grab_correlated",
        rejection_reason_code="gate_rejections_observed_not_enforced",
        grab_status="submitting",
        config_get=config_get,
        now=now,
    )


def _try_correlate(
    correlate: Callable[..., Optional[Dict[str, str]]],
    *,
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]],
    connection_factory: Optional[Callable[[], Any]],
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Fail-open wrapper: correlation must never block or fail a download."""
    if not lib2_context and not target_context and not kwargs.get("search_result"):
        return None
    try:
        if connection_factory is None:
            from database.music_database import get_database
            connection_factory = get_database()._get_connection
        conn = connection_factory()
        try:
            markers = correlate(
                conn,
                lib2_context=lib2_context,
                target_context=target_context,
                **kwargs,
            )
            conn.commit()
            return markers
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - observational bookkeeping only
        logger.debug("legacy grab correlation skipped: %s", exc)
        return None


def try_correlate_manual_grab(
    *,
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    batch_id: Optional[str] = None,
    legacy_download_id: Optional[str] = None,
    connection_factory: Optional[Callable[[], Any]] = None,
    config_get: Optional[Callable[..., Any]] = None,
) -> Optional[Dict[str, str]]:
    """Fail-open correlation of one dispatched manual grab."""
    return _try_correlate(
        correlate_manual_grab,
        lib2_context=lib2_context,
        target_context=target_context,
        connection_factory=connection_factory,
        search_result=search_result,
        source=source,
        batch_id=batch_id,
        legacy_download_id=legacy_download_id,
        config_get=config_get,
    )


def try_prepare_manual_grab(
    *,
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    batch_id: Optional[str] = None,
    connection_factory: Optional[Callable[[], Any]] = None,
    config_get: Optional[Callable[..., Any]] = None,
) -> Optional[Dict[str, str]]:
    """Fail-open preparation before one manual external dispatch."""
    return _try_correlate(
        prepare_manual_grab,
        lib2_context=lib2_context,
        target_context=target_context,
        connection_factory=connection_factory,
        search_result=search_result,
        source=source,
        batch_id=batch_id,
        config_get=config_get,
    )


def try_correlate_scheduled_grab(
    *,
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    task_id: str,
    batch_id: Optional[str] = None,
    legacy_download_id: Optional[str] = None,
    connection_factory: Optional[Callable[[], Any]] = None,
    config_get: Optional[Callable[..., Any]] = None,
) -> Optional[Dict[str, str]]:
    """Fail-open correlation of one dispatched wishlist-worker grab."""
    return _try_correlate(
        correlate_scheduled_grab,
        lib2_context=lib2_context,
        target_context=target_context,
        connection_factory=connection_factory,
        search_result=search_result,
        source=source,
        task_id=task_id,
        batch_id=batch_id,
        legacy_download_id=legacy_download_id,
        config_get=config_get,
    )


def try_prepare_scheduled_grab(
    *,
    lib2_context: Optional[Mapping[str, Any]],
    target_context: Optional[Mapping[str, Any]] = None,
    search_result: Mapping[str, Any],
    source: str,
    task_id: str,
    batch_id: Optional[str] = None,
    connection_factory: Optional[Callable[[], Any]] = None,
    config_get: Optional[Callable[..., Any]] = None,
) -> Optional[Dict[str, str]]:
    """Fail-open preparation before one wishlist-worker dispatch."""
    return _try_correlate(
        prepare_scheduled_grab,
        lib2_context=lib2_context,
        target_context=target_context,
        connection_factory=connection_factory,
        search_result=search_result,
        source=source,
        task_id=task_id,
        batch_id=batch_id,
        config_get=config_get,
    )


def bind_correlated_grab_transfer(
    markers: Optional[Mapping[str, Any]],
    legacy_download_id: Any,
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Attach a successful legacy-client transfer to a prepared grab."""
    download_id = str((markers or {}).get("download_id") or "").strip()
    transfer_id = str(legacy_download_id or "").strip()
    if not download_id or not transfer_id:
        return False
    try:
        if connection_factory is None:
            from database.music_database import get_database
            connection_factory = get_database()._get_connection
        conn = connection_factory()
        try:
            from core.acquisition.grabs import (
                get_grab,
                patch_grab_context,
                update_grab,
            )
            from core.acquisition.history import record_history_event

            grab = get_grab(conn, download_id)
            if grab is None or grab["status"] not in {"submitting", "downloading"}:
                return False
            existing = str(grab["context"].get("legacy_download_id") or "")
            if existing:
                return existing == transfer_id
            if not patch_grab_context(
                conn, download_id, {"legacy_download_id": transfer_id}
            ):
                return False
            update_grab(
                conn,
                download_id,
                status="downloading",
                last_client_state="legacy_dispatched",
            )
            record_history_event(
                conn,
                "grab_submitted",
                request_id=grab.get("acquisition_request_id"),
                candidate_id=grab.get("release_candidate_id"),
                download_id=download_id,
                payload={"source": grab.get("source"), "client": "legacy"},
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - dispatch already succeeded
        logger.debug("legacy grab transfer binding skipped: %s", exc)
        return False


def fail_prepared_correlated_grab(
    markers: Optional[Mapping[str, Any]],
    error: Any,
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Close a prepared correlation after an unambiguous dispatch failure."""
    download_id = str((markers or {}).get("download_id") or "").strip()
    if not download_id:
        return False
    try:
        if connection_factory is None:
            from database.music_database import get_database
            connection_factory = get_database()._get_connection
        conn = connection_factory()
        try:
            from core.acquisition.workflow import record_grab_outcome
            record_grab_outcome(
                conn,
                download_id,
                completed=False,
                failure_kind="runtime",
                error=str(error or "legacy client rejected the dispatch"),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - fail-open bookkeeping
        logger.debug("prepared legacy grab failure could not be recorded: %s", exc)
        return False


def fail_stale_correlated_grabs(
    conn: Any,
    *,
    now: Optional[float] = None,
    ttl_seconds: int = MANUAL_GRAB_TTL_SECONDS,
    limit: int = 50,
) -> int:
    """Close correlated legacy grabs whose download never reached an outcome.

    Covers both manual (interactive) and scheduled (wishlist-worker)
    correlations. ``failure_kind='runtime'`` deliberately never blocklists
    the release — nothing is known to be wrong with the candidate itself.
    """
    from core.acquisition.workflow import record_grab_outcome

    timestamp = time.time() if now is None else float(now)
    cutoff = int(timestamp - int(ttl_seconds))
    rows = conn.execute(
        """SELECT g.download_id
             FROM acquisition_requests r
             JOIN acquisition_grabs g ON g.acquisition_request_id = r.id
            WHERE r.trigger IN ('manual','scheduled')
              AND (r.idempotency_key LIKE ? OR r.idempotency_key LIKE ?)
              AND r.status='grabbing'
              AND g.status NOT IN ('completed','failed','cancelled')
              AND CAST(strftime('%s', r.updated_at) AS INTEGER) <= ?
            ORDER BY r.updated_at
            LIMIT ?""",
        (
            MANUAL_GRAB_KEY_PREFIX + "%",
            SCHEDULED_GRAB_KEY_PREFIX + "%",
            cutoff,
            max(int(limit), 0),
        ),
    ).fetchall()
    closed = 0
    for row in rows:
        download_id = str(row[0])
        try:
            record_grab_outcome(
                conn,
                download_id,
                completed=False,
                failure_kind="runtime",
                error="correlated grab expired without a pipeline outcome",
            )
            closed += 1
        except Exception as exc:  # noqa: BLE001 - one row must not stop the sweep
            logger.warning(
                "Stale correlated grab %s could not be closed: %s",
                download_id, exc)
    return closed


__all__ = [
    "GRAB_MARKER",
    "CORRELATION_ENFORCEMENT_KEY",
    "MANUAL_GRAB_KEY_PREFIX",
    "MANUAL_GRAB_TTL_SECONDS",
    "SCHEDULED_GRAB_KEY_PREFIX",
    "correlate_manual_grab",
    "correlate_scheduled_grab",
    "correlation_enforcement_enabled",
    "bind_correlated_grab_transfer",
    "fail_prepared_correlated_grab",
    "fail_stale_correlated_grabs",
    "prepare_manual_grab",
    "prepare_scheduled_grab",
    "try_correlate_manual_grab",
    "try_correlate_scheduled_grab",
    "try_prepare_manual_grab",
    "try_prepare_scheduled_grab",
]
