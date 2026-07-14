"""Library Manager v2 — UI-facing API (opt-in, Lidarr-style).

Routes are mounted directly on the Flask ``app`` under ``/api/library/v2/*`` and
gated on the ``features.library_v2`` config flag.

Design notes:
- **Artwork is media-server-independent.** Image URLs returned here point at the
  local ``/api/library/v2/artwork/<kind>/<id>`` endpoint, which resolves art from the
  files' own embedded covers (or metadata providers) and caches it on local disk —
  never from Plex/Jellyfin/Navidrome (see ``core/library2/artwork.py``).
- **Monitoring mirrors the existing systems.** Toggling an artist's monitor flag
  also adds/removes it from the WATCHLIST; an album/single/track monitor mirrors to
  the WISHLIST — via internal DB calls, so existing scan/auto-download keeps working.

Registered from ``web_server.py`` via ``register_library_v2_routes(app, ...)``.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from flask import jsonify, request, send_file

from core.library2 import ADMIN_PROFILE_ID
from core.library2.job_registry import JobAlreadyRunning, JobRegistry
from utils.logging_config import get_logger

logger = get_logger("api.library_v2")

# In-process import job state (single library, single job at a time).
_import_lock = threading.Lock()
_import_state: Dict[str, Any] = {"running": False, "stage": None, "current": 0,
                                 "total": 0, "stats": None, "error": None,
                                 "finished_at": None}

# Background jobs are independent by opaque id. Duplicate kinds serialize;
# monitor/upgrade/retag may run concurrently without overwriting each other.
_job_registry = JobRegistry()

_MONITOR_TABLES = {"artists": "lib2_artists", "albums": "lib2_albums", "tracks": "lib2_tracks"}
_PROFILE_TABLES = {"artists": "lib2_artists", "albums": "lib2_albums", "tracks": "lib2_tracks"}

# Serializes slow-path artwork resolution per entity so a page of 75 uncached
# covers doesn't fire 75 concurrent provider lookups for the same image.
_artwork_locks: Dict[str, threading.Lock] = {}
_artwork_locks_guard = threading.Lock()

# A track is "consolidated away" when it deliberately has no file while its
# canonical duplicate partner (either link direction) owns one — the user just
# moved/deduped it. Bulk re-monitor paths must not re-want those, or the
# pipeline would immediately re-download the variant the user removed.
_NOT_CONSOLIDATED_SQL = """
    NOT (
        NOT EXISTS(SELECT 1 FROM lib2_track_files tf
                   WHERE tf.track_id = lib2_tracks.id
                     AND tf.path IS NOT NULL AND tf.path <> '')
        AND EXISTS(
            SELECT 1 FROM lib2_tracks o
            JOIN lib2_track_files otf ON otf.track_id = o.id
                 AND otf.path IS NOT NULL AND otf.path <> ''
            WHERE o.id = lib2_tracks.canonical_track_id
               OR o.canonical_track_id = lib2_tracks.id
        )
    )
"""


def _artwork_lock(kind: str, eid: int) -> threading.Lock:
    key = f"{kind}:{eid}"
    with _artwork_locks_guard:
        lock = _artwork_locks.get(key)
        if lock is None:
            lock = _artwork_locks.setdefault(key, threading.Lock())
        return lock


def _artwork_url(kind: str, entity_id: int) -> str:
    return f"/api/library/v2/artwork/{kind}/{int(entity_id)}"


def _apply_artwork_urls(data: Any, kind: str) -> Any:
    """Point a serialized entity's ``image_url`` at the local artwork endpoint."""
    if isinstance(data, dict) and "id" in data:
        data["image_url"] = _artwork_url(kind, data["id"])
    return data


def register_library_v2_routes(app, *, get_database: Callable[[], Any],
                               config_get: Callable[..., Any],
                               config_manager: Any = None,
                               profile_id_getter: Optional[Callable[[], int]] = None,
                               acquisition_runtime_getter: Optional[Callable[..., Any]] = None,
                               acquisition_search_adapters_getter: Optional[Callable[..., Any]] = None,
                               acquisition_async_runner: Optional[Callable[..., Any]] = None,
                               acquisition_submission_adapter_getter: Optional[Callable[..., Any]] = None) -> None:
    """Attach the Library v2 routes to ``app``.

    ``get_database`` → shared ``MusicDatabase``; ``config_get(key, default)`` reads
    config (feature flag); ``config_manager`` is passed to the artwork/path resolver;
    ``profile_id_getter`` resolves the active profile (defaults to 1).
    """

    def _enabled() -> bool:
        return config_get("features.library_v2", False) is True

    def _guard():
        if not _enabled():
            return jsonify({"success": False, "error": "Library v2 is disabled"}), 403
        # ADR-01 (admin-only): Library v2 has exactly ONE authoritative user
        # intent — the admin profile (profiles.id = 1). Mutations from any
        # other profile are rejected outright, not silently ignored: the lib2
        # monitored columns are global, so a non-admin write would overwrite
        # the admin's state and mirror into the wrong profile's wishlist
        # (audit P0-02). Other profiles keep read access.
        if request.method not in ("GET", "HEAD", "OPTIONS") \
                and _profile() != ADMIN_PROFILE_ID:
            return jsonify({
                "success": False,
                "error": "Library v2 changes require the admin profile",
            }), 403
        return None

    def _conn():
        return get_database()._get_connection()

    def _profile() -> int:
        try:
            return int(profile_id_getter()) if profile_id_getter else 1
        except Exception:
            return 1

    def _acquisition_search_adapters(criteria):
        if acquisition_search_adapters_getter:
            return tuple(acquisition_search_adapters_getter(criteria) or ())
        from core.acquisition.prowlarr_adapter import default_usenet_search_adapter
        return (default_usenet_search_adapter(),)

    def _run_acquisition_async(coro):
        if acquisition_async_runner:
            return acquisition_async_runner(coro)
        from utils.async_helpers import run_async
        return run_async(coro)

    def _acquisition_submission_adapter(source: str):
        if acquisition_submission_adapter_getter:
            return acquisition_submission_adapter_getter(source)
        if source == "usenet":
            from core.acquisition.submission import UsenetSubmissionAdapter
            return UsenetSubmissionAdapter()
        return None

    # -- read endpoints -------------------------------------------------------

    @app.route("/api/library/v2/enabled")
    def lib2_enabled():
        return jsonify({"success": True, "enabled": _enabled()})

    # -- acquisition requests / decisions (Phase 4) -------------------------

    @app.route("/api/library/v2/acquisition/requests", methods=["POST"])
    def lib2_create_acquisition_request():
        guard = _guard()
        if guard:
            return guard
        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "").strip().lower()
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        try:
            entity_id = int(body.get("entity_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "entity_id must be an integer"}), 400
        if body.get("search_options") not in (None, {}):
            return jsonify({
                "success": False,
                "error": "search_options are server-managed",
            }), 400
        conn = _conn()
        try:
            from core.acquisition import ensure_acquisition_schema
            from core.acquisition.catalog import (
                resolve_entity_quality_profile,
                resolve_public_request_search_options,
            )
            from core.acquisition.requests import create_request, transition_request
            ensure_acquisition_schema(conn)
            search_options = resolve_public_request_search_options(
                conn, scope, entity_id)
            quality_profile_id = resolve_entity_quality_profile(
                conn, scope, entity_id, search_options=search_options)
            acquisition_request, created = create_request(
                conn,
                profile_id=ADMIN_PROFILE_ID,
                scope=scope,
                entity_id=entity_id,
                quality_profile_id=quality_profile_id,
                trigger="manual",
                idempotency_key=idempotency_key,
                search_options=search_options,
            )
            if created:
                acquisition_request = transition_request(
                    conn, acquisition_request.id, "searching",
                    expected_status="pending", increment_attempts=True)
                from core.acquisition.history import record_history_event
                record_history_event(
                    conn,
                    "request_created",
                    request_id=acquisition_request.id,
                    payload={
                        "scope": acquisition_request.scope,
                        "entity_id": acquisition_request.entity_id,
                        "quality_profile_id": acquisition_request.quality_profile_id,
                        "trigger": acquisition_request.trigger,
                    },
                )
            conn.commit()
            return jsonify({
                "success": True,
                "created": created,
                "request": acquisition_request.to_dict(),
            }), 201 if created else 200
        except ValueError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/requests/<request_id>")
    def lib2_get_acquisition_request(request_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.requests import get_request
            acquisition_request = get_request(conn, request_id)
            if acquisition_request is None:
                return jsonify({"success": False, "error": "Request not found"}), 404
            if acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            return jsonify({"success": True, "request": acquisition_request.to_dict()})
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/requests/<request_id>/history")
    def lib2_get_acquisition_history(request_id):
        guard = _guard()
        if guard:
            return guard
        try:
            limit = int(request.args.get("limit", 200))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "limit must be an integer"}), 400
        conn = _conn()
        try:
            from core.acquisition.history import list_history_events
            from core.acquisition.requests import get_request
            acquisition_request = get_request(conn, request_id)
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            events = list_history_events(
                conn, request_id=request_id, limit=limit)
            return jsonify({
                "success": True,
                "events": [event.to_public_dict() for event in events],
            })
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/requests/<request_id>/retry",
        methods=["POST"],
    )
    def lib2_retry_acquisition_request(request_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.requests import get_request
            from core.acquisition.workflow import retry_acquisition_request
            acquisition_request = get_request(conn, request_id)
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            retried = retry_acquisition_request(conn, request_id)
            conn.commit()
            return jsonify({"success": True, "request": retried.to_dict()})
        except ValueError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 409
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/blocklist")
    def lib2_get_acquisition_blocklist():
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.blocklist import list_blocklist_entries
            entries = list_blocklist_entries(conn)
            return jsonify({
                "success": True,
                "entries": [entry.to_public_dict() for entry in entries],
            })
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/blocklist/<entry_id>",
        methods=["DELETE"],
    )
    def lib2_delete_acquisition_blocklist_entry(entry_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.blocklist import unblock_candidate
            entry, changed = unblock_candidate(
                conn, entry_id, actor_profile_id=ADMIN_PROFILE_ID)
            conn.commit()
            return jsonify({
                "success": True,
                "changed": changed,
                "entry": entry.to_public_dict(),
            })
        except KeyError:
            conn.rollback()
            return jsonify({"success": False, "error": "Blocklist entry not found"}), 404
        except ValueError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/requests/<request_id>/candidates")
    def lib2_get_acquisition_candidates(request_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.candidates import list_request_candidates
            from core.acquisition.decisions import latest_decision_run
            from core.acquisition.requests import get_request
            acquisition_request = get_request(conn, request_id)
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            payload = []
            for candidate in list_request_candidates(conn, request_id):
                decision = latest_decision_run(conn, candidate.id)
                payload.append({
                    **candidate.to_public_dict(),
                    "decision_run_id": decision.id if decision else None,
                    "decision": decision.decision.to_public_dict() if decision else None,
                })
            return jsonify({"success": True, "candidates": payload})
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/requests/<request_id>/evaluate",
        methods=["POST"],
    )
    def lib2_evaluate_acquisition_request(request_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.catalog import resolve_request_context
            from core.acquisition.eligibility_gate import RuntimeContext
            from core.acquisition.requests import get_request
            from core.acquisition.workflow import evaluate_request_candidates
            acquisition_request = get_request(conn, request_id)
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            automatic = acquisition_request.trigger != "manual"
            catalog, policy = resolve_request_context(
                conn, acquisition_request, config_get=config_get)
            runtime = (
                acquisition_runtime_getter(acquisition_request)
                if acquisition_runtime_getter else RuntimeContext()
            )
            if not isinstance(runtime, RuntimeContext):
                raise ValueError("acquisition runtime provider returned an invalid context")
            result = evaluate_request_candidates(
                conn,
                request_id,
                catalog=catalog,
                runtime=runtime,
                policy=policy,
                automatic=automatic,
            )
            conn.commit()
            return jsonify({"success": True, **result.to_public_dict()})
        except ValueError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/requests/<request_id>/grab",
        methods=["POST"],
    )
    def lib2_grab_acquisition_candidate(request_id):
        """Persist intent first, then submit to the external client."""
        guard = _guard()
        if guard:
            return guard
        body = request.get_json(silent=True) or {}
        candidate_id = str(body.get("candidate_id") or "").strip()
        if not candidate_id:
            return jsonify({"success": False, "error": "candidate_id is required"}), 400
        force = body.get("force", False)
        if not isinstance(force, bool):
            return jsonify({"success": False, "error": "force must be a boolean"}), 400

        prepare_conn = _conn()
        try:
            import uuid

            from core.acquisition.catalog import resolve_request_context
            from core.acquisition.eligibility_gate import RuntimeContext
            from core.acquisition.grabs import (
                find_request_candidate_grab,
                public_grab,
            )
            from core.acquisition.requests import get_request
            from core.acquisition.workflow import prepare_candidate_grab
            acquisition_request = get_request(prepare_conn, request_id)
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            existing = find_request_candidate_grab(
                prepare_conn, request_id, candidate_id)
            if existing is not None:
                return jsonify({
                    "success": True,
                    "created": False,
                    "grab": public_grab(existing),
                })
            catalog, policy = resolve_request_context(
                prepare_conn, acquisition_request, config_get=config_get)
            runtime = (
                acquisition_runtime_getter(acquisition_request)
                if acquisition_runtime_getter else RuntimeContext()
            )
            if not isinstance(runtime, RuntimeContext):
                raise ValueError("acquisition runtime provider returned an invalid context")
            prepared = prepare_candidate_grab(
                prepare_conn,
                request_id,
                candidate_id,
                download_id="acq-" + str(uuid.uuid4()),
                catalog=catalog,
                runtime=runtime,
                policy=policy,
                profile_id=ADMIN_PROFILE_ID,
                force=force,
                is_admin=True,
            )
            prepare_conn.commit()
        except ValueError as exc:
            prepare_conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 409
        finally:
            prepare_conn.close()

        from core.acquisition.submission import (
            SubmissionError,
            record_external_submission,
            record_uncertain_submission,
        )
        adapter = _acquisition_submission_adapter(prepared.candidate.source)
        if adapter is None or str(getattr(adapter, "source", "")) != prepared.candidate.source:
            submission_error = SubmissionError(
                "No submission adapter is available for this candidate source")
        else:
            try:
                submission = _run_acquisition_async(adapter.submit(prepared))
                submission_error = None
            except SubmissionError as exc:
                submission_error = exc
            except Exception as exc:  # noqa: BLE001 - accepted remotely is possible
                from core.acquisition.search_contract import safe_external_error
                submission_error = SubmissionError(
                    f"External submission outcome is unknown: {safe_external_error(exc)}",
                    uncertain=True,
                )

        if submission_error is not None:
            outcome_conn = _conn()
            try:
                from core.acquisition.grabs import get_grab, public_grab
                if submission_error.uncertain:
                    grab = record_uncertain_submission(
                        outcome_conn, prepared, str(submission_error))
                    outcome_conn.commit()
                    return jsonify({
                        "success": True,
                        "created": True,
                        "submission_status": "unknown",
                        "grab": public_grab(grab),
                    }), 202
                from core.acquisition.workflow import record_grab_outcome
                record_grab_outcome(
                    outcome_conn,
                    prepared.download_id,
                    completed=False,
                    error=str(submission_error),
                    failure_kind=submission_error.failure_kind,
                )
                grab = get_grab(outcome_conn, prepared.download_id)
                outcome_conn.commit()
                return jsonify({
                    "success": False,
                    "error": str(submission_error),
                    "created": True,
                    "submission_status": "failed",
                    "grab": public_grab(grab),
                }), 502
            except Exception:
                outcome_conn.rollback()
                raise
            finally:
                outcome_conn.close()

        submit_conn = _conn()
        try:
            from core.acquisition.grabs import public_grab
            grab = record_external_submission(
                submit_conn, prepared, submission)
            submit_conn.commit()
        except Exception as exc:  # noqa: BLE001 - external job may already exist
            submit_conn.rollback()
            from core.acquisition.search_contract import safe_external_error
            safe_error = safe_external_error(exc)
            recovery_conn = _conn()
            try:
                grab = record_uncertain_submission(
                    recovery_conn,
                    prepared,
                    f"External job accepted but correlation persistence failed: {safe_error}",
                )
                recovery_conn.commit()
            finally:
                recovery_conn.close()
            return jsonify({
                "success": True,
                "created": True,
                "submission_status": "unknown",
                "grab": public_grab(grab),
            }), 202
        finally:
            submit_conn.close()

        monitor_attached = True
        try:
            adapter.start_monitor(prepared, submission)
        except Exception as exc:  # noqa: BLE001 - persisted job is restart-adoptable
            monitor_attached = False
            logger.warning(
                "Acquisition monitor attach failed for %s: %s",
                prepared.download_id,
                exc,
            )
        return jsonify({
            "success": True,
            "created": True,
            "submission_status": "queued",
            "monitor_attached": monitor_attached,
            "grab": public_grab(grab),
        }), 202

    @app.route("/api/library/v2/acquisition/grabs/<download_id>")
    def lib2_get_acquisition_grab(download_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.grabs import get_grab, public_grab
            from core.acquisition.requests import get_request
            grab = get_grab(conn, download_id)
            if grab is None or not grab.get("acquisition_request_id"):
                return jsonify({"success": False, "error": "Grab not found"}), 404
            acquisition_request = get_request(
                conn, grab["acquisition_request_id"])
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Grab not found"}), 404
            return jsonify({"success": True, "grab": public_grab(grab)})
        finally:
            conn.close()

    # -- acquisition import review; filesystem paths remain server-only -----

    def _owned_import(conn, import_id):
        from core.acquisition.imports import get_import
        from core.acquisition.requests import get_request
        record = get_import(conn, import_id)
        if record is None:
            return None, None
        owner = get_request(conn, record.request_id)
        if owner is None or owner.profile_id != ADMIN_PROFILE_ID:
            return None, None
        return record, owner

    def _public_import_detail(record):
        quarantined = [
            {
                "relative_path": item.get("relative_path"),
                "track_id": item.get("track_id"),
                "trigger": item.get("trigger"),
                "reason": item.get("reason"),
            }
            for item in record.result.get("quarantined", [])
            if isinstance(item, dict)
        ]
        return {
            **record.to_public_dict(),
            "inventory": [dict(item) for item in record.inventory],
            "matches": [dict(item) for item in record.matches],
            "rejections": [dict(item) for item in record.rejections],
            "processed_count": len(record.result.get("processed", [])),
            "quarantined_count": len(quarantined),
            "quarantined": quarantined,
        }

    @app.route("/api/library/v2/acquisition/imports")
    def lib2_list_acquisition_imports():
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.acquisition.imports import list_open_imports
            records = list_open_imports(conn)
            return jsonify({
                "success": True,
                "imports": [record.to_public_dict() for record in records],
            })
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/path-health")
    def lib2_acquisition_path_health():
        guard = _guard()
        if guard:
            return guard
        from core.acquisition.path_health import (
            inspect_mapping_configuration,
            inspect_reported_path,
        )
        conn = _conn()
        try:
            from core.acquisition.grabs import get_grab
            from core.acquisition.imports import list_open_imports
            imports = []
            for record in list_open_imports(conn):
                grab = get_grab(conn, record.download_id) or {}
                imports.append({
                    "import_id": record.id,
                    "request_id": record.request_id,
                    "source": grab.get("source"),
                    "import_status": record.status,
                    **inspect_reported_path(
                        record.output_path,
                        config_get=config_get,
                    ).to_public_dict(),
                })
            return jsonify({
                "success": True,
                "mappings": inspect_mapping_configuration(
                    config_get).to_public_dict(),
                "imports": imports,
            })
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/correlation-coverage")
    def lib2_acquisition_correlation_coverage():
        guard = _guard()
        if guard:
            return guard
        try:
            days = int(request.args.get("days", 7))
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "days must be an integer between 1 and 90",
            }), 400
        conn = _conn()
        try:
            from core.acquisition.correlation_coverage import (
                correlation_coverage_summary,
            )
            from core.acquisition.manual_grab import (
                CORRELATION_ENFORCEMENT_KEY,
            )
            try:
                coverage = correlation_coverage_summary(conn, days=days)
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400
            return jsonify({
                "success": True,
                "enforcement_key": CORRELATION_ENFORCEMENT_KEY,
                "enforced": config_get(
                    CORRELATION_ENFORCEMENT_KEY, False) is True,
                "coverage": coverage,
            })
        finally:
            conn.close()

    @app.route("/api/library/v2/acquisition/imports/<import_id>")
    def lib2_get_acquisition_import(import_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            record, _owner = _owned_import(conn, import_id)
            if record is None:
                return jsonify({"success": False, "error": "Import not found"}), 404
            return jsonify({"success": True, "import": _public_import_detail(record)})
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/imports/<import_id>/resolve",
        methods=["POST"],
    )
    def lib2_resolve_acquisition_import(import_id):
        guard = _guard()
        if guard:
            return guard
        assignments = (request.get_json(silent=True) or {}).get("assignments")
        if not isinstance(assignments, list):
            return jsonify({
                "success": False,
                "error": "assignments must be a list",
            }), 400
        conn = _conn()
        try:
            from core.acquisition.bundle_matching import (
                build_manual_matches,
                load_expected_tracks,
            )
            from core.acquisition.imports import record_manual_resolution
            record, owner = _owned_import(conn, import_id)
            if record is None:
                return jsonify({"success": False, "error": "Import not found"}), 404
            expected = load_expected_tracks(
                conn,
                record.expected_scope,
                record.expected_entity_id,
                search_options=owner.search_options,
            )
            matches = build_manual_matches(record, expected, assignments)
            updated = record_manual_resolution(conn, record.id, matches)
            conn.commit()
            return jsonify({"success": True, "import": _public_import_detail(updated)})
        except ValueError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/imports/<import_id>/rescan",
        methods=["POST"],
    )
    def lib2_rescan_acquisition_import(import_id):
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            record, _owner = _owned_import(conn, import_id)
            if record is None:
                return jsonify({"success": False, "error": "Import not found"}), 404
            if record.status not in {"pending", "matching", "needs_review"}:
                return jsonify({
                    "success": False,
                    "error": f"Import cannot be rescanned while {record.status}",
                }), 409
            output_path = record.output_path
        finally:
            conn.close()

        from core.acquisition.bundle_inventory import collect_bundle_inventory
        inventory = collect_bundle_inventory(output_path, config_get=config_get)
        if not inventory.ok:
            return jsonify({
                "success": False,
                "error": inventory.error or "Bundle is not readable",
                "status": inventory.status,
            }), 409
        conn = _conn()
        try:
            from core.acquisition.imports import record_inventory_result
            record, _owner = _owned_import(conn, import_id)
            if record is None:
                return jsonify({"success": False, "error": "Import not found"}), 404
            updated = record_inventory_result(
                conn,
                record.id,
                [item.to_dict() for item in inventory.files],
                resolved_path=inventory.resolved_path,
            )
            conn.commit()
            return jsonify({"success": True, "import": _public_import_detail(updated)})
        except ValueError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route(
        "/api/library/v2/acquisition/requests/<request_id>/search",
        methods=["POST"],
    )
    def lib2_search_acquisition_request(request_id):
        """Search configured sources without holding a database transaction."""
        guard = _guard()
        if guard:
            return guard

        read_conn = _conn()
        try:
            from core.acquisition.catalog import (
                load_effective_policy,
                resolve_catalog_context,
            )
            from core.acquisition.requests import get_request
            from core.acquisition.search_contract import build_search_criteria
            acquisition_request = get_request(read_conn, request_id)
            if acquisition_request is None or acquisition_request.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            if acquisition_request.status != "searching":
                return jsonify({
                    "success": False,
                    "error": f"Request cannot be searched while {acquisition_request.status}",
                }), 409
            criteria = build_search_criteria(
                acquisition_request,
                resolve_catalog_context(read_conn, acquisition_request),
            )
            acquisition_policy = load_effective_policy(
                read_conn,
                acquisition_request.quality_profile_id,
                config_get=config_get,
            )
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            read_conn.close()

        try:
            from core.acquisition.search_service import collect_search_results
            collection = _run_acquisition_async(collect_search_results(
                criteria,
                _acquisition_search_adapters(criteria),
                timeout_seconds=float(config_get(
                    "acquisition.search_timeout_seconds", 30.0)),
                source_policy=acquisition_policy.source_policy,
            ))
        except Exception as exc:  # noqa: BLE001 - external source boundary
            from core.acquisition.search_contract import safe_external_error
            error = safe_external_error(exc)
            logger.warning("Acquisition search setup failed for %s: %s", request_id, error)
            collection = None

        searched_sources = (
            [item for item in collection.outcomes if item.status == "searched"]
            if collection else []
        )
        if not searched_sources:
            error = (
                "No configured compatible acquisition source"
                if collection is not None and not any(
                    item.status == "failed" for item in collection.outcomes)
                else "All compatible acquisition source searches failed"
            )
            write_conn = _conn()
            try:
                from core.acquisition.requests import get_request, transition_request
                current = get_request(write_conn, request_id)
                if current is None or current.profile_id != ADMIN_PROFILE_ID:
                    return jsonify({"success": False, "error": "Request not found"}), 404
                if current.status != "searching":
                    return jsonify({
                        "success": False,
                        "error": "Request changed while sources were being searched",
                    }), 409
                current = transition_request(
                    write_conn,
                    current.id,
                    "failed",
                    expected_status="searching",
                    error=error,
                )
                from core.acquisition.history import record_history_event
                record_history_event(
                    write_conn,
                    "search_failed",
                    request_id=current.id,
                    reason_code="sources_unavailable",
                    message=error,
                    payload=(
                        collection.to_public_dict() if collection else {
                            "candidate_count": 0,
                            "sources": [],
                        }
                    ),
                )
                write_conn.commit()
                payload = collection.to_public_dict() if collection else {
                    "candidate_count": 0,
                    "sources": [],
                }
                return jsonify({
                    "success": False,
                    "error": error,
                    "request": current.to_dict(),
                    "search": payload,
                }), 503
            except Exception:
                write_conn.rollback()
                raise
            finally:
                write_conn.close()

        write_conn = _conn()
        try:
            from core.acquisition.catalog import resolve_request_context
            from core.acquisition.eligibility_gate import RuntimeContext
            from core.acquisition.requests import get_request
            from core.acquisition.search_service import persist_search_results
            from core.acquisition.workflow import evaluate_request_candidates
            current = get_request(write_conn, request_id)
            if current is None or current.profile_id != ADMIN_PROFILE_ID:
                return jsonify({"success": False, "error": "Request not found"}), 404
            if current.status != "searching":
                return jsonify({
                    "success": False,
                    "error": "Request changed while sources were being searched",
                }), 409
            persisted = persist_search_results(write_conn, collection)
            from core.acquisition.history import record_history_event
            record_history_event(
                write_conn,
                "search_completed",
                request_id=current.id,
                payload={
                    **collection.to_public_dict(),
                    "created": persisted.created_count,
                    "refreshed": persisted.refreshed_count,
                },
            )
            catalog, policy = resolve_request_context(
                write_conn, current, config_get=config_get)
            runtime = (
                acquisition_runtime_getter(current)
                if acquisition_runtime_getter else RuntimeContext()
            )
            if not isinstance(runtime, RuntimeContext):
                raise ValueError("acquisition runtime provider returned an invalid context")
            evaluated = evaluate_request_candidates(
                write_conn,
                current.id,
                catalog=catalog,
                runtime=runtime,
                policy=policy,
                automatic=current.trigger != "manual",
            )
            write_conn.commit()
            return jsonify({
                "success": True,
                "search": collection.to_public_dict(),
                "persisted": {
                    "created": persisted.created_count,
                    "refreshed": persisted.refreshed_count,
                },
                **evaluated.to_public_dict(),
            })
        except ValueError as exc:
            write_conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            write_conn.close()

    @app.route(
        "/api/library/v2/acquisition/wanted/materialize",
        methods=["POST"],
    )
    def lib2_materialize_wanted_acquisition():
        """Create Phase-4 shadow requests; legacy Wishlist remains operative."""
        guard = _guard()
        if guard:
            return guard
        body = request.get_json(silent=True) or {}
        track_ids = body.get("track_ids")
        if track_ids is not None and not isinstance(track_ids, list):
            return jsonify({"success": False, "error": "track_ids must be an array"}), 400
        conn = _conn()
        try:
            from core.acquisition import ensure_acquisition_schema
            from core.acquisition.wanted_adapter import materialize_wanted_requests
            ensure_acquisition_schema(conn)
            results = materialize_wanted_requests(
                conn,
                profile_id=ADMIN_PROFILE_ID,
                track_ids=track_ids,
                trigger="manual",
            )
            conn.commit()
            return jsonify({
                "success": True,
                "shadow": True,
                "requests": [item.to_dict() for item in results],
            })
        except (TypeError, ValueError) as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.route("/api/library/v2/wanted-projection/status")
    def lib2_wanted_projection_status():
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.library2.wanted import wanted_projection_status
            status = wanted_projection_status(conn, profile_id=ADMIN_PROFILE_ID)
            return jsonify({"success": True, **status})
        finally:
            conn.close()

    @app.route("/api/library/v2/artists")
    def lib2_list_artists():
        guard = _guard()
        if guard:
            return guard
        from core.library2 import queries as Q
        search = request.args.get("search", "")
        sort = request.args.get("sort", "name")
        monitored = request.args.get("monitored", "all")
        try:
            page = int(request.args.get("page", 1))
            limit = int(request.args.get("limit", 75))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "page/limit must be integers"}), 400
        conn = _conn()
        try:
            artists, total = Q.list_artists(conn, search=search, sort=sort,
                                            monitored=monitored, page=page, limit=limit)
        finally:
            conn.close()
        for a in artists:
            _apply_artwork_urls(a, "artist")
        total_pages = (total + limit - 1) // limit if limit else 0
        return jsonify({
            "success": True,
            "artists": artists,
            "pagination": {
                "page": page, "limit": limit, "total_count": total,
                "total_pages": total_pages,
                "has_prev": page > 1, "has_next": page < total_pages,
            },
        })

    @app.route("/api/library/v2/artists/<int:artist_id>")
    def lib2_get_artist(artist_id):
        guard = _guard()
        if guard:
            return guard
        from core.library2 import queries as Q
        conn = _conn()
        try:
            data = Q.get_artist(conn, artist_id)
        finally:
            conn.close()
        if data is None:
            return jsonify({"success": False, "error": "Artist not found"}), 404
        _apply_artwork_urls(data, "artist")
        for entry in data.get("albums", []) + data.get("eps", []) + data.get("singles", []):
            _apply_artwork_urls(entry, "album")
        return jsonify({"success": True, "artist": data})

    @app.route("/api/library/v2/albums/<int:album_id>")
    def lib2_get_album(album_id):
        guard = _guard()
        if guard:
            return guard
        from core.library2 import queries as Q
        conn = _conn()
        try:
            # ``?resolve=1``: materialize the provider tracklist first, so a
            # discography-only release (no track rows yet) shows its real
            # tracklist when the user expands it — Lidarr-style.
            if request.args.get("resolve") == "1":
                has_tracks = conn.execute(
                    "SELECT 1 FROM lib2_tracks WHERE album_id=? LIMIT 1", (album_id,)
                ).fetchone()
                if not has_tracks:
                    try:
                        from core.library2.completeness import resolve_tracklist
                        resolve_tracklist(config_manager, conn, album_id)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("on-demand tracklist resolve failed (%s): %s", album_id, e)
            data = Q.get_album(conn, album_id)
        finally:
            conn.close()
        if data is None:
            return jsonify({"success": False, "error": "Album not found"}), 404
        _apply_artwork_urls(data, "album")
        return jsonify({"success": True, "album": data})

    @app.route("/api/library/v2/tracks/<int:track_id>")
    def lib2_get_track(track_id):
        guard = _guard()
        if guard:
            return guard
        from core.library2 import queries as Q
        conn = _conn()
        try:
            data = Q.get_track(conn, track_id)
        finally:
            conn.close()
        if data is None:
            return jsonify({"success": False, "error": "Track not found"}), 404
        return jsonify({"success": True, "track": data})

    @app.route("/api/library/v2/quality-profiles/sync", methods=["POST"])
    def lib2_sync_quality_profiles():
        """Compatibility endpoint: profiles are the app-wide ``quality_profiles``
        rows (managed in Settings → Quality) — there is nothing to sync anymore.
        Returns the live count so old UIs still show a sensible number."""
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM quality_profiles").fetchone()[0]
        finally:
            conn.close()
        return jsonify({"success": True, "synced": count})

    @app.route("/api/library/v2/quality-profiles")
    def lib2_quality_profiles():
        """List app-wide profiles with the canonical upgrade-policy contract.

        ``upgrade_policy`` is one of ``acceptable``, ``until_cutoff`` or the
        persisted legacy alias ``until_top``. For ``until_cutoff``, clients
        use ``upgrade_cutoff_index``; ``until_top`` always means index 0.
        """
        guard = _guard()
        if guard:
            return guard
        from core.library2 import queries as Q
        conn = _conn()
        try:
            profiles = Q.list_quality_profiles(conn)
        finally:
            conn.close()
        return jsonify({"success": True, "profiles": profiles})

    # -- artwork (media-server-independent, disk-cached) ----------------------

    def _send_art(path):
        resp = send_file(str(path), mimetype="image/jpeg", conditional=True)
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return resp

    @app.route("/api/library/v2/artwork/<kind>/<int:eid>")
    def lib2_artwork(kind, eid):
        guard = _guard()
        if guard:
            return guard
        if kind not in ("artist", "album"):
            return "", 404
        from core.library2.artwork import (
            artwork_file, build_artwork, thumb_file, _write_thumbnail,
        )
        db = get_database()
        want_thumb = request.args.get("size") == "thumb"
        force = request.args.get("force") == "1"
        if force:
            # A forced rebuild must bust BOTH cached variants — build_artwork
            # only replaces the full image; a surviving stale thumb would keep
            # winning the fast path forever.
            t = thumb_file(db, kind, eid)
            if t.exists():
                try:
                    t.unlink()
                except OSError:
                    pass
        # Fast path: serve the cached file directly with NO database/resolve work.
        if not force:
            target = thumb_file(db, kind, eid) if want_thumb else artwork_file(db, kind, eid)
            if target.exists():
                return _send_art(target)
            full = artwork_file(db, kind, eid)
            if want_thumb and full.exists():
                _write_thumbnail(full, target)
                if target.exists():
                    return _send_art(target)
        # Slow path: resolve + cache (opens a DB connection). Serialized per
        # entity so concurrent first-views don't stampede the providers; the
        # second waiter finds the file cached and returns without resolving.
        with _artwork_lock(kind, eid):
            if not force and artwork_file(db, kind, eid).exists():
                path = str(artwork_file(db, kind, eid))
            else:
                conn = db._get_connection()
                try:
                    path = build_artwork(db, conn, config_manager, kind, eid, force=force)
                finally:
                    conn.close()
        if not path:
            return "", 404
        target = thumb_file(db, kind, eid) if want_thumb else artwork_file(db, kind, eid)
        if want_thumb and not target.exists():
            _write_thumbnail(artwork_file(db, kind, eid), target)
        return _send_art(target if target.exists() else artwork_file(db, kind, eid))

    # -- monitoring (mirrors watchlist / wishlist) ----------------------------

    @app.route("/api/library/v2/mirror-status")
    def lib2_mirror_status():
        """Outbox visibility (audit P0-04): pending/failed mirror ops and the
        most recent errors, so a mirror failure is a UI state, not a log line."""
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            from core.library2.mirror_outbox import outbox_status
            return jsonify({"success": True, **outbox_status(conn)})
        finally:
            conn.close()

    @app.route("/api/library/v2/mirror-retry", methods=["POST"])
    def lib2_mirror_retry():
        """Reset failed mirror ops and drain the outbox once."""
        guard = _guard()
        if guard:
            return guard
        db = get_database()
        conn = db._get_connection()
        try:
            from core.library2.mirror_outbox import drain, outbox_status, prune_done, retry_failed
            retried = retry_failed(conn)
            prune_done(conn)
            conn.commit()
            result = drain(db)
            return jsonify({"success": True, "retried": retried, **result,
                            **outbox_status(conn)})
        finally:
            conn.close()

    @app.route("/api/library/v2/<entity>/<int:eid>/monitor", methods=["POST"])
    def lib2_set_monitored(entity, eid):
        guard = _guard()
        if guard:
            return guard
        table = _MONITOR_TABLES.get(entity)
        if not table:
            return jsonify({"success": False, "error": "Unknown entity"}), 400
        monitored = bool((request.json or {}).get("monitored", True))
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            # Monitoring a discography-only release must first materialize its
            # provider tracklist into real, monitorable track rows — otherwise
            # there is nothing to mirror into the wishlist (Lidarr: monitoring
            # an unowned album makes its tracks "wanted").
            if entity == "albums" and monitored:
                has_tracks = conn.execute(
                    "SELECT 1 FROM lib2_tracks WHERE album_id=? LIMIT 1", (eid,)
                ).fetchone()
                if not has_tracks:
                    try:
                        from core.library2.completeness import resolve_tracklist
                        resolve_tracklist(config_manager, conn, eid)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("monitor tracklist resolve failed (%s): %s", eid, e)
            cur.execute(f"UPDATE {table} SET monitored=? WHERE id=?", (1 if monitored else 0, eid))
            if not cur.rowcount:
                return jsonify({"success": False, "error": "Not found"}), 404
            # Monitor provenance (audit P1-13/P1-14): this endpoint is a direct
            # user action on exactly this entity — record the intent so later
            # cascades know it was deliberate.
            from core.library2.monitor_rules import (
                PROVENANCE_CASCADE,
                PROVENANCE_USER,
                explicit_track_rules_for_album,
                record_rule,
                record_rules,
            )
            record_rule(conn, {"artists": "artist", "albums": "album",
                               "tracks": "track"}[entity], eid, monitored,
                        PROVENANCE_USER, profile_id=_profile())
            track_ids: List[int] = []
            preserved_track_ids: List[int] = []
            if entity == "albums":
                # P1-14: an album toggle is a cascade — it re-projects only
                # tracks WITHOUT an explicit per-track choice. A track the
                # user deliberately (un)monitored keeps its state; re-deciding
                # it takes another direct action on the track itself.
                explicit = explicit_track_rules_for_album(conn, eid,
                                                          profile_id=_profile())
                all_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM lib2_tracks WHERE album_id=?", (eid,))]
                preserved_track_ids = [t for t in all_ids
                                       if t in explicit and explicit[t] != monitored]
                track_ids = [t for t in all_ids if t not in preserved_track_ids]
                if preserved_track_ids:
                    keep = ",".join("?" for _ in preserved_track_ids)
                    cur.execute(
                        f"UPDATE lib2_tracks SET monitored=? "
                        f"WHERE album_id=? AND id NOT IN ({keep})",
                        (1 if monitored else 0, eid, *preserved_track_ids))
                else:
                    cur.execute("UPDATE lib2_tracks SET monitored=? WHERE album_id=?",
                                (1 if monitored else 0, eid))
                # The re-projected tracks carry the cascade as their own rule
                # so the wanted projection sees the current per-track intent.
                # EVERY explicitly-ruled track keeps its user_explicit rule —
                # also when its value happens to match the cascade; a
                # deliberate choice must never be downgraded to 'cascade'.
                record_rules(conn, "track",
                             [t for t in track_ids if t not in explicit],
                             monitored, PROVENANCE_CASCADE,
                             profile_id=_profile())
            elif entity == "tracks":
                track_ids = [eid]
            # Transactional outbox (audit P0-04): the mirror intents commit in
            # the SAME transaction as the monitor flag — a crash or legacy-DB
            # failure can no longer leave lib2 saying "monitored" while the
            # pipeline never learned about it. The drain below replays them;
            # failures stay visible in lib2_mirror_outbox instead of a 200.
            # Recompute before enqueue: the derived Wishlist consumes the
            # authoritative projection, never the compatibility flag/command.
            from core.library2.wanted import recompute_wanted_for_entity
            recompute_wanted_for_entity(conn, entity, eid, profile_id=_profile())
            from core.library2.mirror_outbox import (
                drain as drain_mirror_outbox,
                enqueue_artist_watchlist,
                enqueue_projected_tracks,
            )
            outbox_ids: List[int] = []
            if entity == "artists":
                outbox_ids = enqueue_artist_watchlist(conn, eid, monitored,
                                                      profile_id=_profile())
            elif track_ids:
                # Only the track-level toggle is a direct user action on that
                # track; an album toggle is a cascade and must respect a
                # per-track ignore (user cancelled that download on purpose).
                outbox_ids = enqueue_projected_tracks(
                    conn,
                    track_ids,
                    profile_id=_profile(),
                    user_initiated=(entity == "tracks"),
                )
            conn.commit()
            mirrored = mirror_pending = 0
            if outbox_ids:
                drain_mirror_outbox(db)
                marks = ",".join("?" for _ in outbox_ids)
                mirrored = conn.execute(
                    f"SELECT COUNT(*) FROM lib2_mirror_outbox "
                    f"WHERE id IN ({marks}) AND status='done'", outbox_ids).fetchone()[0]
                mirror_pending = len(outbox_ids) - mirrored
        finally:
            conn.close()
        return jsonify({"success": True, "monitored": monitored,
                        "mirrored": mirrored, "mirror_pending": mirror_pending,
                        "preserved_tracks": len(preserved_track_ids)})

    @app.route("/api/library/v2/<entity>/<int:eid>/quality-profile", methods=["POST"])
    def lib2_set_quality_profile(entity, eid):
        """Assign a profile without changing its upgrade-policy semantics.

        Explicit ``monitor_existing`` applies to both upgrade modes:
        ``until_cutoff`` and legacy ``until_top``. ``acceptable`` assignment
        alone never turns existing tracks into wanted upgrades.
        """
        guard = _guard()
        if guard:
            return guard
        table = _PROFILE_TABLES.get(entity)
        if not table:
            return jsonify({"success": False, "error": "Unknown entity"}), 400
        profile_id = int((request.json or {}).get("quality_profile_id") or 0)
        cascade = bool((request.json or {}).get("cascade", True))
        # P1-15: assigning a profile is a QUALITY decision, not a wanted-
        # action. Monitoring existing tracks (and thereby queueing upgrade
        # downloads) only happens on explicit opt-in from the UI.
        monitor_existing = bool((request.json or {}).get("monitor_existing", False))
        db = get_database()
        conn = db._get_connection()
        try:
            profile = conn.execute(
                "SELECT id, upgrade_policy, repair_job_id, repair_settings "
                "FROM quality_profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
            if profile is None:
                return jsonify({"success": False, "error": "Quality profile not found"}), 404
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET quality_profile_id=? WHERE id=?", (profile_id, eid))
            if not cur.rowcount:
                return jsonify({"success": False, "error": "Not found"}), 404
            updated = cur.rowcount
            if cascade and entity == "artists":
                cur.execute(
                    "UPDATE lib2_albums SET quality_profile_id=? WHERE primary_artist_id=?",
                    (profile_id, eid),
                )
                updated += cur.rowcount
                cur.execute(
                    "UPDATE lib2_tracks SET quality_profile_id=? "
                    "WHERE album_id IN (SELECT id FROM lib2_albums WHERE primary_artist_id=?)",
                    (profile_id, eid),
                )
                updated += cur.rowcount
            elif cascade and entity == "albums":
                cur.execute(
                    "UPDATE lib2_tracks SET quality_profile_id=? WHERE album_id=?",
                    (profile_id, eid),
                )
                updated += cur.rowcount
            auto_monitored = 0
            auto_monitor_track_ids: List[int] = []
            from core.library2.quality_eval import is_upgrade_policy
            # Bulk auto-monitor skips consolidated-away duplicates (a track the
            # user deliberately left fileless while its canonical partner owns
            # the file) — re-wanting those would re-download what Manage Tracks
            # just cleaned up. An explicit single-track assignment still wins.
            if monitor_existing and is_upgrade_policy(profile["upgrade_policy"]):
                if entity == "artists":
                    auto_monitor_track_ids = [r["id"] for r in conn.execute(
                        f"SELECT id FROM lib2_tracks "
                        f"WHERE album_id IN (SELECT id FROM lib2_albums WHERE primary_artist_id=?) "
                        f"AND {_NOT_CONSOLIDATED_SQL}",
                        (eid,),
                    )]
                elif entity == "albums":
                    auto_monitor_track_ids = [r["id"] for r in conn.execute(
                        f"SELECT id FROM lib2_tracks WHERE album_id=? "
                        f"AND {_NOT_CONSOLIDATED_SQL}",
                        (eid,),
                    )]
                elif entity == "tracks":
                    auto_monitor_track_ids = [eid]
                # Monitor provenance (audit P1-14): the bulk opt-in is a
                # cascade — it must not overturn a track the user explicitly
                # unmonitored. A single-track assignment IS a direct action.
                from core.library2.monitor_rules import (
                    PROVENANCE_CASCADE,
                    PROVENANCE_USER,
                    explicitly_unmonitored_track_ids,
                    record_rules,
                )
                if entity != "tracks" and auto_monitor_track_ids:
                    vetoed = explicitly_unmonitored_track_ids(
                        conn, auto_monitor_track_ids, profile_id=_profile())
                    auto_monitor_track_ids = [
                        t for t in auto_monitor_track_ids if t not in vetoed]
                if auto_monitor_track_ids:
                    marks = ",".join("?" for _ in auto_monitor_track_ids)
                    cur.execute(
                        f"UPDATE lib2_tracks SET monitored=1 WHERE id IN ({marks})",
                        auto_monitor_track_ids,
                    )
                    auto_monitored = cur.rowcount
                    record_rules(
                        conn, "track", auto_monitor_track_ids, True,
                        PROVENANCE_USER if entity == "tracks" else PROVENANCE_CASCADE,
                        profile_id=_profile())
                    from core.library2.wanted import recompute_wanted
                    recompute_wanted(conn, profile_id=_profile(),
                                     track_ids=auto_monitor_track_ids)
            conn.commit()
            mirrored = 0
            if auto_monitor_track_ids:
                from core.library2.wishlist_mirror import (
                    mirror_projected_tracks_wishlist,
                )
                mirrored = mirror_projected_tracks_wishlist(
                    db,
                    conn,
                    auto_monitor_track_ids,
                    profile_id=_profile(),
                )
            settings = json.loads(profile["repair_settings"] or "{}")
        finally:
            conn.close()
        return jsonify({
            "success": True,
            "quality_profile_id": profile_id,
            "updated": updated,
            "upgrade_policy": profile["upgrade_policy"],
            "auto_monitored": auto_monitored,
            "mirrored": mirrored,
            "repair_job": {
                "id": profile["repair_job_id"],
                "settings": settings,
                "requires_top_target": bool(settings.get("require_top_target")),
            },
        })

    # -- discography (all releases of an artist, Lidarr-style) ----------------

    @app.route("/api/library/v2/artists/<int:artist_id>/discography/refresh", methods=["POST"])
    def lib2_discography_refresh(artist_id):
        """Fetch the artist's full provider discography and persist it as
        browsable (unmonitored) ``origin='discography'`` releases."""
        guard = _guard()
        if guard:
            return guard
        db = get_database()
        try:
            from core.library2.discography import expand_artist_discography
            stats = expand_artist_discography(db, artist_id)
        except ValueError:
            return jsonify({"success": False, "error": "Artist not found"}), 404
        except Exception as e:  # noqa: BLE001
            logger.error("Discography refresh failed (artist %s): %s", artist_id, e)
            return jsonify({"success": False, "error": str(e)}), 500
        # monitor_new_items enforcement: releases discovered on a re-expansion
        # of a monitored 'all'/'new' artist come back pre-monitored — give them
        # real track rows and mirror those into the wishlist (shared helper,
        # also used by the periodic lib2_discography_refresh repair job).
        auto_ids = stats.pop("auto_monitor_album_ids", []) or []
        mirrored = 0
        if auto_ids:
            from core.library2.discography import auto_monitor_releases
            mirrored = auto_monitor_releases(db, config_manager, auto_ids,
                                             wishlist_profile_id=_profile())
        return jsonify({"success": True, **stats,
                        "auto_monitored_releases": len(auto_ids),
                        "auto_monitor_mirrored": mirrored})

    def _bulk_track_ids_for_albums(conn, album_ids: List[int]) -> List[int]:
        if not album_ids:
            return []
        marks = ",".join("?" for _ in album_ids)
        return [r["id"] for r in conn.execute(
            f"SELECT id FROM lib2_tracks WHERE album_id IN ({marks})", album_ids)]

    @app.route("/api/library/v2/artists/<int:artist_id>/releases/monitor", methods=["POST"])
    def lib2_bulk_monitor(artist_id):
        """Bulk-set the monitor flag on an artist's releases.

        Body: ``{"scope": "albums"|"eps"|"singles"|"all", "monitored": bool}``.
        Runs in the background: monitoring unowned releases resolves each
        tracklist from a metadata provider before mirroring to the wishlist.
        """
        guard = _guard()
        if guard:
            return guard
        body = request.json or {}
        scope = str(body.get("scope") or "all")
        monitored = bool(body.get("monitored", True))
        type_filter = {
            "albums": "al.album_type NOT IN ('single','ep')",
            "eps": "al.album_type = 'ep'",
            "singles": "al.album_type = 'single'",
            "all": "1=1",
            # Lidarr's "Monitor missing": only releases that are incomplete.
            "missing": """(
                COALESCE(al.expected_track_count,
                         (SELECT COUNT(*) FROM lib2_tracks t2 WHERE t2.album_id = al.id)) >
                (SELECT COUNT(DISTINCT t3.id) FROM lib2_tracks t3
                   JOIN lib2_track_files tf3 ON tf3.track_id = t3.id
                  WHERE t3.album_id = al.id)
            )""",
        }.get(scope)
        if not type_filter:
            return jsonify({"success": False, "error": "Unknown scope"}), 400
        try:
            # All monitor scopes mutate the same rule/projection set and must
            # therefore serialize with each other. Other job kinds stay free.
            job = _job_registry.start("monitor")
        except JobAlreadyRunning as exc:
            return jsonify({
                "success": False,
                "error": str(exc),
                "job_id": exc.state["job_id"],
            }), 409
        job_id = job["job_id"]

        # Resolve the active profile OUTSIDE the thread (request context) —
        # _profile() degrades to 1 without one, which would mirror into the
        # wrong user's wishlist on multi-profile installs.
        active_profile = _profile()

        def _run():
            db = get_database()
            try:
                conn = db._get_connection()
                try:
                    albums = [r["id"] for r in conn.execute(
                        f"""SELECT al.id FROM lib2_album_artists aa
                            JOIN lib2_albums al ON al.id = aa.album_id
                           WHERE aa.artist_id = ? AND {type_filter}""", (artist_id,))]
                    _job_registry.update(job_id, total=len(albums))
                    mirrored = 0
                    for i, album_id in enumerate(albums):
                        _job_registry.update(job_id, current=i)
                        if monitored:
                            has_tracks = conn.execute(
                                "SELECT 1 FROM lib2_tracks WHERE album_id=? LIMIT 1",
                                (album_id,)).fetchone()
                            if not has_tracks:
                                try:
                                    from core.library2.completeness import resolve_tracklist
                                    resolve_tracklist(config_manager, conn, album_id)
                                except Exception as e:  # noqa: BLE001
                                    logger.debug("bulk tracklist resolve failed (%s): %s",
                                                 album_id, e)
                        conn.execute("UPDATE lib2_albums SET monitored=? WHERE id=?",
                                     (1 if monitored else 0, album_id))
                        from core.library2.monitor_rules import (
                            PROVENANCE_CASCADE,
                            PROVENANCE_USER,
                            explicit_track_rules_for_album,
                            record_rule,
                            record_rules,
                        )
                        record_rule(
                            conn,
                            "album",
                            album_id,
                            monitored,
                            PROVENANCE_USER,
                            profile_id=active_profile,
                        )
                        # Re-monitoring skips consolidated-away duplicates (the
                        # user just moved their file to the other variant) —
                        # both for the flag AND the wishlist mirror; unmonitoring
                        # always applies to every track.
                        if monitored:
                            candidate_track_ids = [r["id"] for r in conn.execute(
                                f"SELECT id FROM lib2_tracks WHERE album_id=? "
                                f"AND {_NOT_CONSOLIDATED_SQL}", (album_id,))]
                        else:
                            candidate_track_ids = _bulk_track_ids_for_albums(
                                conn, [album_id]
                            )
                        explicit = explicit_track_rules_for_album(
                            conn, album_id, profile_id=active_profile
                        )
                        track_ids = [
                            track_id for track_id in candidate_track_ids
                            if track_id not in explicit
                        ]
                        if track_ids:
                            marks = ",".join("?" for _ in track_ids)
                            conn.execute(
                                f"UPDATE lib2_tracks SET monitored=? WHERE id IN ({marks})",
                                [1 if monitored else 0, *track_ids])
                            record_rules(
                                conn,
                                "track",
                                track_ids,
                                monitored,
                                PROVENANCE_CASCADE,
                                profile_id=active_profile,
                            )
                        from core.library2.wanted import recompute_wanted_for_entity
                        recompute_wanted_for_entity(
                            conn,
                            "album",
                            album_id,
                            profile_id=active_profile,
                        )
                        conn.commit()
                        if track_ids:
                            from core.library2.wishlist_mirror import (
                                mirror_projected_tracks_wishlist,
                            )
                            mirrored += mirror_projected_tracks_wishlist(
                                db,
                                conn,
                                track_ids,
                                profile_id=active_profile,
                            )
                    _job_registry.update(
                        job_id,
                        result={"albums": len(albums), "mirrored": mirrored},
                    )
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                logger.error("Bulk monitor failed (artist %s): %s", artist_id, e, exc_info=True)
                _job_registry.update(job_id, error=str(e))
            finally:
                _job_registry.finish(job_id)

        threading.Thread(target=_run, name="lib2-bulk-monitor", daemon=True).start()
        return jsonify({"success": True, "started": True, "job_id": job_id})

    @app.route("/api/library/v2/jobs/status")
    def lib2_job_status():
        guard = _guard()
        if guard:
            return guard
        job_id = str(request.args.get("job_id") or "").strip()
        state = _job_registry.get(job_id) if job_id else _job_registry.latest()
        if job_id and state is None:
            return jsonify({"success": False, "error": "Job not found"}), 404
        if state is None:
            state = {
                "job_id": None,
                "running": False,
                "kind": None,
                "current": 0,
                "total": 0,
                "result": None,
                "error": None,
                "started_at": None,
                "finished_at": None,
            }
        return jsonify({"success": True, **state, "jobs": _job_registry.list()})

    # -- edit / delete / history (Lidarr artist-page actions) ------------------

    @app.route("/api/library/v2/artists/<int:artist_id>/edit", methods=["POST"])
    def lib2_edit_artist(artist_id):
        """Update artist-level settings. Currently: ``monitor_new_items``
        ('all'|'none'|'new') — how future discography refreshes should treat
        newly discovered releases."""
        guard = _guard()
        if guard:
            return guard
        body = request.json or {}
        monitor_new = str(body.get("monitor_new_items") or "").strip()
        if monitor_new not in ("all", "none", "new"):
            return jsonify({"success": False, "error": "monitor_new_items must be all|none|new"}), 400
        conn = _conn()
        try:
            cur = conn.execute(
                "UPDATE lib2_artists SET monitor_new_items=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (monitor_new, artist_id))
            if not cur.rowcount:
                return jsonify({"success": False, "error": "Artist not found"}), 404
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "monitor_new_items": monitor_new})

    _ALBUM_TYPES = ("album", "single", "ep", "compilation", "live")

    @app.route("/api/library/v2/albums/<int:album_id>/edit", methods=["POST"])
    def lib2_edit_album(album_id):
        """Correct the effective album type without rewriting provider data."""
        guard = _guard()
        if guard:
            return guard
        body = request.json or {}
        album_type = str(body.get("album_type") or "").strip().lower()
        if album_type not in _ALBUM_TYPES:
            return jsonify({"success": False,
                            "error": f"album_type must be one of {'|'.join(_ALBUM_TYPES)}"}), 400
        conn = _conn()
        try:
            from core.library2.metadata_overrides import (
                MetadataOverrideError,
                set_field_override,
            )
            set_field_override(
                conn,
                entity_type="release_group",
                entity_id=album_id,
                field_name="album_type",
                value=album_type,
                profile_id=_profile(),
                reason="album_type_edit",
            )
            conn.commit()
        except MetadataOverrideError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), exc.status
        finally:
            conn.close()
        return jsonify({"success": True, "album_type": album_type})

    @app.route(
        "/api/library/v2/metadata-overrides/<entity_type>/<int:entity_id>",
        methods=["PATCH"],
    )
    def lib2_metadata_overrides_batch(entity_type, entity_id):
        """Atomically set and clear validated metadata corrections."""
        guard = _guard()
        if guard:
            return guard
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"success": False, "error": "JSON body is required"}), 400
        values = body.get("set", {})
        clear = body.get("clear", [])
        if not isinstance(values, dict) or not isinstance(clear, list) or not all(
            isinstance(field, str) for field in clear
        ):
            return jsonify({
                "success": False,
                "error": "set must be an object and clear must be a string array",
            }), 400
        overlap = set(values).intersection(clear)
        if overlap:
            return jsonify({
                "success": False,
                "error": "fields cannot be both set and cleared: " + ",".join(sorted(overlap)),
            }), 400
        if not values and not clear:
            return jsonify({"success": False, "error": "no metadata changes supplied"}), 400

        from core.library2.metadata_overrides import (
            MetadataOverrideError,
            clear_field_override,
            get_field_overrides,
            set_field_override,
        )
        conn = _conn()
        try:
            for field_name, value in values.items():
                set_field_override(
                    conn,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    field_name=field_name,
                    value=value,
                    profile_id=_profile(),
                    reason="metadata_edit",
                )
            for field_name in clear:
                clear_field_override(
                    conn,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    field_name=field_name,
                    profile_id=_profile(),
                )
            overrides = get_field_overrides(
                conn,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            conn.commit()
        except MetadataOverrideError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), exc.status
        finally:
            conn.close()
        return jsonify({
            "success": True,
            "overrides": {
                field_name: override.value
                for field_name, override in overrides.items()
            },
        })

    @app.route(
        "/api/library/v2/metadata-overrides/<entity_type>/<int:entity_id>/<field_name>",
        methods=["PUT", "DELETE"],
    )
    def lib2_metadata_override(entity_type, entity_id, field_name):
        """Set or clear one validated admin metadata correction."""
        guard = _guard()
        if guard:
            return guard
        from core.library2.metadata_overrides import (
            MetadataOverrideError,
            clear_field_override,
            set_field_override,
        )
        conn = _conn()
        try:
            if request.method == "DELETE":
                removed = clear_field_override(
                    conn,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    field_name=field_name,
                    profile_id=_profile(),
                )
                conn.commit()
                return jsonify({"success": True, "removed": removed})

            body = request.get_json(silent=True)
            if not isinstance(body, dict) or "value" not in body:
                return jsonify({
                    "success": False,
                    "error": "JSON body must contain value",
                }), 400
            override = set_field_override(
                conn,
                entity_type=entity_type,
                entity_id=entity_id,
                field_name=field_name,
                value=body["value"],
                profile_id=_profile(),
                reason=body.get("reason"),
            )
            conn.commit()
            return jsonify({
                "success": True,
                "override": {
                    "entity_type": override.entity_type,
                    "entity_id": override.entity_id,
                    "field_name": override.field_name,
                    "value": override.value,
                    "reason": override.reason,
                },
            })
        except MetadataOverrideError as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), exc.status
        finally:
            conn.close()

    def _unmonitor_tracks_and_delete(db, conn, *, artist_id: Optional[int] = None,
                                     album_ids: Optional[List[int]] = None) -> Dict[str, int]:
        """Shared delete path: unmirror wishlist entries, then delete lib2 rows.

        NEVER touches files on disk — this removes library entries only,
        exactly like Lidarr's 'delete artist' without the delete-files box.

        For an artist, only albums whose ``primary_artist_id`` IS the artist
        cascade. Albums the artist merely appears on (featured/various) belong
        to another primary artist and must survive; only the credit rows are
        detached by the caller (audit P0-01).
        """
        if album_ids is None:
            album_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                (artist_id,))]
        track_ids = _bulk_track_ids_for_albums(conn, album_ids)
        # Enqueue the wishlist un-mirrors BEFORE the rows vanish (the payload
        # builder needs them) and in the SAME transaction as the deletes —
        # the route drains the outbox after committing (audit P0-04).
        from core.library2.mirror_outbox import enqueue_tracks
        unmirrored = len(enqueue_tracks(conn, track_ids, False,
                                        profile_id=_profile())) if track_ids else 0
        removed_albums = 0
        for aid_ in album_ids:
            conn.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (aid_,))
            conn.execute(
                "DELETE FROM lib2_track_artists WHERE track_id IN "
                "(SELECT id FROM lib2_tracks WHERE album_id=?)", (aid_,))
            conn.execute(
                "DELETE FROM lib2_track_files WHERE track_id IN "
                "(SELECT id FROM lib2_tracks WHERE album_id=?)", (aid_,))
            conn.execute(
                "DELETE FROM lib2_wanted_tracks WHERE track_id IN "
                "(SELECT id FROM lib2_tracks WHERE album_id=?)", (aid_,))
            conn.execute("DELETE FROM lib2_tracks WHERE album_id=?", (aid_,))
            conn.execute("DELETE FROM lib2_albums WHERE id=?", (aid_,))
            removed_albums += 1
            _delete_artwork_files(db, "album", aid_)
        return {"albums": removed_albums, "tracks": len(track_ids), "unmirrored": unmirrored}

    def _delete_artwork_files(db, kind: str, eid: int) -> None:
        """Remove the cached artwork (full + thumb) of a deleted entity."""
        try:
            from core.library2.artwork import artwork_file, thumb_file
            for f in (artwork_file(db, kind, eid), thumb_file(db, kind, eid)):
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass
        except Exception as e:  # noqa: BLE001
            logger.debug("artwork cleanup failed (%s %s): %s", kind, eid, e)

    @app.route("/api/library/v2/artists/<int:artist_id>/delete-preview")
    def lib2_artist_delete_preview(artist_id):
        """Impact preview for artist delete: what cascades, what survives.

        Only releases the artist owns (``primary_artist_id``) are removed.
        Featured/various participations on other artists' releases are merely
        detached; the UI shows both numbers before the user commits."""
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            row = conn.execute("SELECT name FROM lib2_artists WHERE id=?",
                               (artist_id,)).fetchone()
            if not row:
                return jsonify({"success": False, "error": "Artist not found"}), 404
            album_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM lib2_albums WHERE primary_artist_id=?", (artist_id,))]
            track_ids = _bulk_track_ids_for_albums(conn, album_ids)
            file_links = 0
            if track_ids:
                marks = ",".join("?" for _ in track_ids)
                file_links = conn.execute(
                    f"SELECT COUNT(*) FROM lib2_track_files WHERE track_id IN ({marks})",
                    track_ids).fetchone()[0]
            detached = conn.execute(
                """SELECT COUNT(*) FROM lib2_album_artists aa
                   JOIN lib2_albums al ON al.id = aa.album_id
                   WHERE aa.artist_id=? AND al.primary_artist_id<>?""",
                (artist_id, artist_id)).fetchone()[0]
            return jsonify({"success": True, "artist": row["name"],
                            "albums": len(album_ids), "tracks": len(track_ids),
                            "file_links": file_links, "detached_albums": detached})
        finally:
            conn.close()

    @app.route("/api/library/v2/artists/<int:artist_id>", methods=["DELETE"])
    def lib2_delete_artist(artist_id):
        """Remove an artist (and their releases/tracks/file links) from
        Library v2. Files on disk are untouched; watchlist + wishlist mirrors
        are removed so nothing keeps auto-downloading for it."""
        guard = _guard()
        if guard:
            return guard
        db = get_database()
        conn = db._get_connection()
        try:
            row = conn.execute("SELECT id FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()
            if not row:
                return jsonify({"success": False, "error": "Artist not found"}), 404
            from core.library2.mirror_outbox import drain as drain_mirror_outbox
            from core.library2.mirror_outbox import enqueue_artist_watchlist
            enqueue_artist_watchlist(conn, artist_id, False, profile_id=_profile())
            stats = _unmonitor_tracks_and_delete(db, conn, artist_id=artist_id)
            # Detach the artist from releases owned by OTHER primary artists
            # (featured/various credits). Those albums, tracks, files and
            # monitor state stay untouched — deleting a featured artist must
            # never take another artist's album with it.
            cur = conn.execute("DELETE FROM lib2_album_artists WHERE artist_id=?", (artist_id,))
            stats["detached_albums"] = cur.rowcount
            conn.execute("DELETE FROM lib2_track_artists WHERE artist_id=?", (artist_id,))
            conn.execute("DELETE FROM lib2_artists WHERE id=?", (artist_id,))
            conn.commit()
            drain_mirror_outbox(db)
            _delete_artwork_files(db, "artist", artist_id)
        finally:
            conn.close()
        return jsonify({"success": True, **stats})

    @app.route("/api/library/v2/albums/<int:album_id>", methods=["DELETE"])
    def lib2_delete_album(album_id):
        guard = _guard()
        if guard:
            return guard
        db = get_database()
        conn = db._get_connection()
        try:
            row = conn.execute("SELECT id FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
            if not row:
                return jsonify({"success": False, "error": "Album not found"}), 404
            stats = _unmonitor_tracks_and_delete(db, conn, album_ids=[album_id])
            conn.commit()
            from core.library2.mirror_outbox import drain as drain_mirror_outbox
            drain_mirror_outbox(db)
        finally:
            conn.close()
        return jsonify({"success": True, **stats})

    @app.route("/api/library/v2/artists/<int:artist_id>/duplicates")
    def lib2_artist_duplicates(artist_id):
        """Single↔album duplicate pairs for Manage Tracks: tracks whose
        ``canonical_track_id`` links a single release to the same recording on
        a regular album (linked by the importer). Each side carries its file's
        quality and monitor state so the user can decide which version to keep
        wanted; the actual file dedup stays with the ``single_album_dedup``
        maintenance job."""
        guard = _guard()
        if guard:
            return guard
        conn = _conn()
        try:
            if not conn.execute("SELECT 1 FROM lib2_artists WHERE id=?", (artist_id,)).fetchone():
                return jsonify({"success": False, "error": "Artist not found"}), 404
            rows = conn.execute(
                """SELECT s.id AS single_id, s.title, s.monitored AS single_monitored,
                          sal.title AS single_album,
                          c.id AS canonical_id, c.monitored AS canonical_monitored,
                          cal.title AS canonical_album
                     FROM lib2_tracks s
                     JOIN lib2_albums sal ON sal.id = s.album_id
                     JOIN lib2_tracks c ON c.id = s.canonical_track_id
                     JOIN lib2_albums cal ON cal.id = c.album_id
                     JOIN lib2_album_artists aa ON aa.album_id = s.album_id
                    WHERE aa.artist_id = ? AND s.canonical_track_id IS NOT NULL
                    ORDER BY s.title COLLATE NOCASE""",
                (artist_id,),
            ).fetchall()

            def _file_summary(track_id: int) -> Optional[Dict[str, Any]]:
                from core.library2.track_files import primary_order
                f = conn.execute(
                    f"SELECT path, format, bitrate, sample_rate, bit_depth "
                    f"FROM lib2_track_files WHERE track_id=? "
                    f"ORDER BY {primary_order()} LIMIT 1", (track_id,)).fetchone()
                return dict(f) if f else None

            pairs = [{
                "title": r["title"],
                "single": {
                    "track_id": r["single_id"],
                    "album_title": r["single_album"],
                    "monitored": bool(r["single_monitored"]),
                    "file": _file_summary(r["single_id"]),
                },
                "album": {
                    "track_id": r["canonical_id"],
                    "album_title": r["canonical_album"],
                    "monitored": bool(r["canonical_monitored"]),
                    "file": _file_summary(r["canonical_id"]),
                },
            } for r in rows]
        finally:
            conn.close()
        return jsonify({"success": True, "pairs": pairs})

    @app.route("/api/library/v2/tracks/<int:track_id>/canonical", methods=["POST"])
    def lib2_set_canonical(track_id):
        """Link/unlink a track to the canonical recording it duplicates.

        Body ``{"canonical_track_id": <id>}`` links (the importer's automatic
        single↔album detection can then be corrected/extended manually);
        ``{"canonical_track_id": null}`` unlinks — the track becomes its own
        canonical again and stops showing as "also on album"."""
        guard = _guard()
        if guard:
            return guard
        body = request.json or {}
        raw = body.get("canonical_track_id")
        conn = _conn()
        try:
            if not conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (track_id,)).fetchone():
                return jsonify({"success": False, "error": "Track not found"}), 404
            if raw in (None, "", 0):
                conn.execute(
                    "UPDATE lib2_tracks SET canonical_track_id=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (track_id,))
                conn.commit()
                return jsonify({"success": True, "canonical_track_id": None})
            canonical_id = int(raw)
            if canonical_id == track_id:
                return jsonify({"success": False, "error": "A track cannot duplicate itself"}), 400
            target = conn.execute(
                "SELECT canonical_track_id FROM lib2_tracks WHERE id=?", (canonical_id,)
            ).fetchone()
            if not target:
                return jsonify({"success": False, "error": "Canonical track not found"}), 404
            if target["canonical_track_id"]:
                return jsonify({"success": False,
                                "error": "Target is itself a duplicate — link to its canonical instead"}), 400
            conn.execute(
                "UPDATE lib2_tracks SET canonical_track_id=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (canonical_id, track_id))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "canonical_track_id": canonical_id})

    @app.route("/api/library/v2/tracks/<int:track_id>/move-file", methods=["POST"])
    def lib2_move_track_file(track_id):
        """Move this track's file link onto another track (single↔album move).

        Body ``{"to_track_id": <id>}``. The file on disk is untouched — only
        the library's file↔track link moves; run Rename/Reorganize afterwards
        to re-folder it. The source track is unmonitored so the consolidated-
        away variant isn't immediately re-downloaded."""
        guard = _guard()
        if guard:
            return guard
        body = request.json or {}
        try:
            to_track_id = int(body.get("to_track_id") or 0)
        except (TypeError, ValueError):
            to_track_id = 0
        if not to_track_id:
            return jsonify({"success": False, "error": "to_track_id required"}), 400
        from core.library2.track_file_move import MoveError, move_track_file
        db = get_database()
        conn = db._get_connection()
        try:
            result = move_track_file(db, conn, track_id, to_track_id,
                                     wishlist_profile_id=_profile())
        except MoveError as e:
            return jsonify({"success": False, "error": str(e)}), e.status
        except Exception as e:  # noqa: BLE001
            logger.error("track file move failed (%s → %s): %s", track_id,
                         to_track_id, e, exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            conn.close()
        return jsonify({"success": True, **result})

    @app.route("/api/library/v2/artists/<int:artist_id>/history")
    def lib2_artist_history(artist_id):
        """Recent download/import provenance for this artist (Lidarr's History
        tab), read from the existing ``track_downloads`` table by artist name."""
        guard = _guard()
        if guard:
            return guard
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        conn = _conn()
        try:
            artist = conn.execute(
                "SELECT name FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()
            if not artist:
                return jsonify({"success": False, "error": "Artist not found"}), 404
            try:
                # Provenance rows store the full credit string ("Drake feat.
                # Wizkid"), so match exact OR name-as-prefix — an exact-only
                # match made History look empty for multi-artist downloads.
                rows = conn.execute(
                    """SELECT track_title, track_album, source_service, source_username,
                              audio_quality, bit_depth, sample_rate, bitrate,
                              file_path, status, created_at
                       FROM track_downloads
                       WHERE lower(track_artist) = lower(?)
                          OR lower(track_artist) LIKE lower(?) || ' %'
                       ORDER BY id DESC LIMIT ?""",
                    (artist["name"], artist["name"], limit),
                ).fetchall()
            except Exception:  # table/columns may not exist on a fresh DB
                rows = []
            history = [{
                "title": r["track_title"],
                "album": r["track_album"],
                "source": r["source_service"],
                "source_detail": r["source_username"],
                "quality": r["audio_quality"],
                "bit_depth": r["bit_depth"],
                "sample_rate": r["sample_rate"],
                "bitrate": r["bitrate"],
                "file_path": r["file_path"],
                "status": r["status"],
                "date": r["created_at"],
            } for r in rows]
        finally:
            conn.close()
        return jsonify({"success": True, "history": history})

    # -- upgrade scan (lib2-aware quality upgrade pass) ------------------------

    @app.route("/api/library/v2/upgrade-scan", methods=["POST"])
    def lib2_upgrade_scan():
        """Queue every monitored track whose file is an upgrade candidate under
        its ``until_top`` quality profile into the wishlist (lib2-aware pass;
        the legacy quality_upgrade worker only scans the legacy tables)."""
        guard = _guard()
        if guard:
            return guard
        try:
            job = _job_registry.start("upgrade-scan")
        except JobAlreadyRunning as exc:
            return jsonify({
                "success": False,
                "error": str(exc),
                "job_id": exc.state["job_id"],
            }), 409
        job_id = job["job_id"]

        # Resolve the active profile OUTSIDE the thread (request context).
        active_profile = _profile()

        def _run():
            db = get_database()
            try:
                conn = db._get_connection()
                try:
                    from core.library2.wishlist_mirror import upgrade_candidate_track_ids
                    track_ids = upgrade_candidate_track_ids(conn)
                    _job_registry.update(job_id, total=len(track_ids))
                    # _mirror_tracks_wishlist re-checks upgrade_candidate per
                    # track and only queues genuine upgrade candidates.
                    from core.library2.wishlist_mirror import (
                        mirror_projected_tracks_wishlist,
                    )
                    queued = mirror_projected_tracks_wishlist(
                        db,
                        conn,
                        track_ids,
                        profile_id=active_profile,
                    )
                    _job_registry.update(
                        job_id,
                        result={"checked": len(track_ids), "queued": queued},
                    )
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                logger.error("Upgrade scan failed: %s", e, exc_info=True)
                _job_registry.update(job_id, error=str(e))
            finally:
                _job_registry.finish(job_id)

        threading.Thread(target=_run, name="lib2-upgrade-scan", daemon=True).start()
        return jsonify({"success": True, "started": True, "job_id": job_id})

    # -- Phase C: tag preview / re-tag -----------------------------------------

    @app.route("/api/library/v2/<entity>/<int:eid>/tag-preview")
    def lib2_tag_preview(entity, eid):
        """Diff of file tags vs lib2 metadata for an album's or artist's tracks."""
        guard = _guard()
        if guard:
            return guard
        if entity not in ("artists", "albums"):
            return jsonify({"success": False, "error": "Unsupported entity"}), 400
        from core.library2 import retag
        db = get_database()
        conn = db._get_connection()
        try:
            exists = conn.execute(
                f"SELECT 1 FROM lib2_{entity} WHERE id=?", (eid,)).fetchone()
            if not exists:
                return jsonify({"success": False, "error": "Not found"}), 404
            track_ids = (retag.album_track_ids(conn, eid) if entity == "albums"
                         else retag.artist_track_ids(conn, eid))
            truncated = len(track_ids) > retag.MAX_TRACKS
            preview = retag.tag_preview(db, conn, track_ids)
        finally:
            conn.close()
        return jsonify({
            "success": True,
            "tracks": preview,
            "changed_count": sum(1 for p in preview if p.get("has_changes")),
            "truncated": truncated,
        })

    @app.route("/api/library/v2/tags/write", methods=["POST"])
    def lib2_write_tags():
        """Write lib2 metadata into the given tracks' file tags (background job).

        Body: ``{"track_ids": [...], "embed_cover": true}``. Poll
        ``/api/library/v2/jobs/status`` for progress/result.
        """
        guard = _guard()
        if guard:
            return guard
        body = request.json or {}
        track_ids = [int(t) for t in (body.get("track_ids") or []) if t]
        embed_cover = bool(body.get("embed_cover", True))
        if not track_ids:
            return jsonify({"success": False, "error": "No track_ids"}), 400
        try:
            job = _job_registry.start("retag", total=len(track_ids))
        except JobAlreadyRunning as exc:
            return jsonify({
                "success": False,
                "error": str(exc),
                "job_id": exc.state["job_id"],
            }), 409
        job_id = job["job_id"]

        def _run():
            from core.library2 import retag
            db = get_database()
            try:
                conn = db._get_connection()
                try:
                    def _progress(_stage, current, total):
                        _job_registry.update(job_id, current=current, total=total)
                    stats = retag.write_tags(db, conn, track_ids,
                                             embed_cover=embed_cover,
                                             progress=_progress)
                    _job_registry.update(job_id, result=stats)
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                logger.error("Library v2 retag failed: %s", e, exc_info=True)
                _job_registry.update(job_id, error=str(e))
            finally:
                _job_registry.finish(job_id)

        threading.Thread(target=_run, name="lib2-retag", daemon=True).start()
        return jsonify({"success": True, "started": True, "job_id": job_id})

    # -- refresh & scan (re-read tags into DB + bust artwork cache) -----------

    @app.route("/api/library/v2/<entity>/<int:eid>/refresh", methods=["POST"])
    def lib2_refresh(entity, eid):
        guard = _guard()
        if guard:
            return guard
        if entity not in ("artists", "albums"):
            return jsonify({"success": False, "error": "Unsupported entity"}), 400
        db = get_database()
        conn = db._get_connection()
        try:
            # The entity must exist — an unknown id must be a 404, not a scan
            # whose empty scope silently widens to the whole library.
            table = "lib2_albums" if entity == "albums" else "lib2_artists"
            exists = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (eid,)).fetchone()
            if not exists:
                return jsonify({"success": False,
                                "error": f"{entity[:-1].capitalize()} {eid} not found"}), 404
            # Collect the album ids in scope, then bust their cached artwork so the
            # next artwork request re-resolves from the (possibly retagged) files.
            if entity == "albums":
                album_ids = [eid]
            else:
                album_ids = [r["id"] for r in conn.execute(
                    """SELECT al.id FROM lib2_album_artists aa JOIN lib2_albums al ON al.id=aa.album_id
                       WHERE aa.artist_id=?""", (eid,))]
            # Bust full image AND thumbnail — the thumb wins the serve fast
            # path, so leaving it behind would pin the stale cover in lists.
            for aid in album_ids:
                _delete_artwork_files(db, "album", aid)
            if entity == "artists":
                _delete_artwork_files(db, "artist", eid)
        finally:
            conn.close()
        # Probe the files in scope so quality evaluation runs against measured
        # sample-rate/bit-depth instead of format-based fallbacks.
        scan_stats = {}
        try:
            from core.library2.scan import rescan_files
            scan_stats = rescan_files(db, album_ids=album_ids)
        except Exception as e:  # noqa: BLE001
            logger.debug("file rescan failed (%s %s): %s", entity, eid, e)
        return jsonify({"success": True, "refreshed_albums": len(album_ids),
                        "scan": scan_stats})

    # -- importer -------------------------------------------------------------

    @app.route("/api/library/v2/import", methods=["POST"])
    def lib2_import():
        guard = _guard()
        if guard:
            return guard
        reset = bool((request.json or {}).get("reset")) if request.is_json else False
        with _import_lock:
            if _import_state["running"]:
                return jsonify({"success": False, "error": "Import already running"}), 409
            _import_state.update(running=True, stage="starting", current=0, total=0,
                                 stats=None, error=None, finished_at=None)

        # Resolve the active profile OUTSIDE the thread (request context).
        active_profile = _profile()

        def _run():
            from core.library2.importer import import_legacy_library
            import time as _t

            def _progress(stage, current, total):
                _import_state.update(stage=stage, current=current, total=total)

            try:
                stats = import_legacy_library(get_database(), reset=reset, progress=_progress,
                                              profile_id=active_profile)
                _import_state.update(stats=stats, stage="tracklists")

                # Resolve missing-track titles before artwork: cached tracklists
                # can immediately become real, monitorable rows, while
                # artwork/provider lookup can be slow.
                try:
                    from core.library2.completeness import precache_tracklists
                    precache_tracklists(get_database(), config_manager, progress=_progress)
                except Exception as e:  # noqa: BLE001
                    logger.debug("tracklist precache failed: %s", e)

                _import_state.update(stage="artwork")
                try:
                    from core.library2.artwork import precache_all_artwork
                    precache_all_artwork(get_database(), config_manager)
                except Exception as e:  # noqa: BLE001
                    logger.debug("artwork precache failed: %s", e)

                _import_state.update(stage="done")
            except Exception as e:  # noqa: BLE001
                logger.error("Library v2 import failed: %s", e, exc_info=True)
                _import_state.update(error=str(e), stage="failed")
            finally:
                _import_state.update(running=False, finished_at=_t.time())

        threading.Thread(target=_run, name="lib2-import", daemon=True).start()
        return jsonify({"success": True, "started": True})

    @app.route("/api/library/v2/import/status")
    def lib2_import_status():
        guard = _guard()
        if guard:
            return guard
        return jsonify({"success": True, **_import_state})

    logger.info("Library v2 routes registered (/api/library/v2/*)")


__all__ = ["register_library_v2_routes"]
