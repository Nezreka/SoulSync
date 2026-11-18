"""the help sheet's server side: health, report a problem, the log files.

GET       /api/help/status                 errors count for the ? button's dot (admin; a member gets {})
GET       /api/help/health                 every health finding (?fresh=1 skips the 30 s cache)
GET|POST  /api/help/bundle                 the diagnostics bundle for Report a problem (?download=1 as a file)
GET       /api/help/logs                   every log file, grouped with its backups
GET       /api/help/logs/download          one file, or one log (+ backups as a zip); ?raw=1 keeps secrets
GET       /api/help/logs/download-all      every log as one zip with a README

admin only, like /api/debug-info and the logs tab. the shapes are written up
in scrap/HELP_API.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.help import bundle as help_bundle
from core.help import health as help_health
from core.help import logs as help_logs
from core import profile_context
from core.permissions import signed_in
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.help")

bp = Blueprint("help_api", __name__)

_state: dict = {"log_path": None, "version": None}


# ---------------------------------------------------------------- wiring
def _download_probe(get_orchestrator: Callable[[], Any]) -> Callable[[str], Any]:
    def probe(source: str):
        orch = get_orchestrator()
        client = orch.client(source) if orch is not None else None
        if client is None:
            return "missing"
        try:
            if hasattr(client, "is_configured") and not client.is_configured():
                return "unconfigured"
        except Exception:  # noqa: BLE001 - a broken config check is "not set up"
            return "unconfigured"
        from utils.async_helpers import run_async
        return bool(run_async(client.check_connection(), timeout=5))
    return probe


def _download_sources(get_orchestrator: Callable[[], Any]) -> Callable[[], list]:
    def sources():
        orch = get_orchestrator()
        if orch is None:
            return []
        mode = getattr(orch, "mode", "hybrid")
        if mode != "hybrid":
            return [mode]
        return list(orch._resolve_source_chain())
    return sources


def _download_failures(get_orchestrator: Callable[[], Any]) -> Callable[[], list]:
    def failures():
        orch = get_orchestrator()
        if orch is None:
            return ["ALL (orchestrator failed to initialize)"]
        return list(getattr(orch, "_init_failures", []) or [])
    return failures


def _review_counts(config_get, get_database):
    def counts():
        import os

        from core.imports.paths import docker_resolve_path
        from core.imports.quarantine import count_quarantine_entries
        qdir = os.path.join(docker_resolve_path(config_get("soulseek.download_path", "./downloads")),
                            "ss_quarantine")
        unverified = get_database().count_library_history_unverified(
            exclude_download_sources=("acoustid_scan",))
        return {"quarantine": count_quarantine_entries(qdir), "unverified": unverified}
    return counts


def _open_import_paths(get_database):
    def paths():
        from contextlib import closing

        from core.acquisition.imports import list_open_imports
        with closing(get_database()._get_connection()) as conn:
            return [r.output_path for r in list_open_imports(conn)]
    return paths


def _checklist():
    """the dashboard card's checklist, through the onboarding blueprint's own
    helpers so the slskd shares check keeps its back-off"""
    from api import onboarding
    from core.onboarding import checklist
    if onboarding.config_manager is None:
        return None
    get = onboarding.config_manager.get
    shared = False
    if checklist.uses_soulseek(get):
        try:
            shared = onboarding._slskd_shares_now()
        except Exception:  # noqa: BLE001 - slskd down is the download check's business
            shared = False
    return checklist.build(get, profile_count=onboarding._profile_count(),
                           tmdb_key=onboarding._tmdb_key(), slskd_shared=shared)


def _video_db():
    from api.video import get_video_db
    return get_video_db()


def _video_settings():
    from core.video import organization
    return organization.load(_video_db())


def configure(*, config_manager_=None, get_database_=None,
              media_server_engine_getter: Optional[Callable[[], Any]] = None,
              download_orchestrator_getter: Optional[Callable[[], Any]] = None,
              update_info: Optional[Callable[[], dict]] = None,
              log_path: Optional[str] = None, version: Optional[str] = None):
    """called once from web_server with the live objects"""
    _state["log_path"] = str(log_path) if log_path else None
    _state["version"] = version
    providers: dict = {"video_db": _video_db, "video_settings": _video_settings,
                       "checklist": _checklist, "update_info": update_info}
    if config_manager_ is not None:
        providers["config_get"] = config_manager_.get
    if get_database_ is not None:
        get = config_manager_.get if config_manager_ is not None else None
        if get is not None:
            providers["review_counts"] = _review_counts(get, get_database_)
        providers["automations"] = lambda: get_database_().get_all_automations()
        providers["open_import_paths"] = _open_import_paths(get_database_)
    if media_server_engine_getter is not None:
        def media_probe():
            engine = media_server_engine_getter()
            return bool(engine.is_connected()) if engine is not None else False
        providers["media_server_probe"] = media_probe
    if download_orchestrator_getter is not None:
        providers["download_probe"] = _download_probe(download_orchestrator_getter)
        providers["download_sources"] = _download_sources(download_orchestrator_getter)
        providers["download_failures"] = _download_failures(download_orchestrator_getter)
    help_health.configure(**providers)
    if version:
        help_bundle.configure(version=lambda: version)
    if log_path:
        app_file = Path(log_path)
        help_bundle.configure(app_log_files=lambda: [app_file] + [
            app_file.with_name(f"{app_file.name}.{n}") for n in (1, 2, 3)])


# ---------------------------------------------------------------- helpers
def _app_file() -> Path:
    return Path(_state["log_path"]) if _state.get("log_path") else help_logs.app_log_file()


def _version() -> str:
    return str(_state.get("version") or "unknown").split("+")[0]


def _flag(name: str) -> bool:
    return str(request.args.get(name, "")).lower() in ("1", "true", "yes")


def _attachment(gen, name: str, mimetype: str) -> Response:
    resp = Response(stream_with_context(gen), mimetype=mimetype)
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _no_such_log():
    return jsonify({"success": False, "error": "No such log"}), 404


# ---------------------------------------------------------------- routes
@bp.route("/api/help/status", methods=["GET"])
@signed_in
def help_status():
    # every profile's ? button asks; only an admin gets an answer
    if not profile_context.is_admin_request():
        return jsonify({})
    return jsonify(help_health.status_snapshot())


@bp.route("/api/help/health", methods=["GET"])
@admin_only
def help_health_route():
    try:
        return jsonify(help_health.run_health(fresh=_flag("fresh")))
    except Exception:  # noqa: BLE001
        logger.exception("help health failed")
        return jsonify({"success": False, "error": "Health couldn't run, the app log says why"}), 500


@bp.route("/api/help/bundle", methods=["GET", "POST"])
@admin_only
def help_bundle_route():
    if request.method == "POST":
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
    else:
        body = request.args.to_dict()
    try:
        lines = int(body.get("lines") or 200)
    except (TypeError, ValueError):
        lines = 200
    data = help_bundle.build(parts=body.get("parts"), note=str(body.get("note") or ""),
                             where=str(body.get("where") or ""), lines=lines)
    if str(body.get("download") or request.args.get("download") or "").lower() in ("1", "true", "yes"):
        resp = Response(data["text"], mimetype="text/plain; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="{data["filename"]}"'
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return jsonify(data)


@bp.route("/api/help/logs", methods=["GET"])
@admin_only
def help_logs_list():
    app_file = _app_file()
    logs = help_logs.list_logs(app_file.parent, app_file)
    return jsonify({
        "log_dir": str(app_file.parent.resolve()),
        "zip_name": help_logs.zip_name(_version()),
        "total_bytes": sum(g["bytes"] for g in logs),
        "logs": [{k: v for k, v in g.items() if k != "base"} for g in logs],
    })


@bp.route("/api/help/logs/download", methods=["GET"])
@admin_only
def help_logs_download():
    app_file = _app_file()
    directory = app_file.parent
    logs = help_logs.list_logs(directory, app_file)
    group, file = help_logs.find(request.args.get("name", ""), logs)
    if group is None:
        return _no_such_log()
    raw = _flag("raw")
    if file is None and _flag("with_backups"):
        members = []
        for f in group["files"]:
            path = help_logs.safe_path(directory, f["name"])
            if path is not None:
                members.append((f["name"], path))
        if not members:
            return _no_such_log()
        readme = help_logs.readme_text([group], version=_version(),
                                       platform_line=help_logs.platform_line(), raw=raw)
        return _attachment(help_logs.iter_zip(members, raw=raw, readme=readme),
                           help_logs.zip_name(_version(), group["name"]), "application/zip")
    if file is None:
        file = next((f for f in group["files"] if not f["backup"]), None)
        if file is None:
            return _no_such_log()
    path = help_logs.safe_path(directory, file["name"])
    if path is None:
        return _no_such_log()
    name = file["name"] if file["name"].endswith(".log") else file["name"] + ".txt"
    return _attachment(help_logs.iter_file(path, raw=raw), name, "text/plain; charset=utf-8")


@bp.route("/api/help/logs/download-all", methods=["GET"])
@admin_only
def help_logs_download_all():
    app_file = _app_file()
    directory = app_file.parent
    logs = help_logs.list_logs(directory, app_file)
    raw = _flag("raw")
    members = []
    for group in logs:
        for f in group["files"]:
            path = help_logs.safe_path(directory, f["name"])
            if path is not None:
                members.append((f["name"], path))
    readme = help_logs.readme_text(logs, version=_version(),
                                   platform_line=help_logs.platform_line(), raw=raw)
    return _attachment(help_logs.iter_zip(members, raw=raw, readme=readme),
                       help_logs.zip_name(_version()), "application/zip")


def create_blueprint():
    return bp
