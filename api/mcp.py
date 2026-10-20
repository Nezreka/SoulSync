"""mcp endpoint at /mcp. an ai client connects with a soulsync api key.

off until turned on in settings. an admin key gets every tool, a read key
only the ones a read key could call on /api/v1 anyway. each api tool call is
replayed through the app with the same key, so nothing here can do more than
that key already could.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import Blueprint, Response, current_app, g, jsonify, request

from core.mcp import protocol
from core.mcp.tools import API_TOOLS, build_request, check_args, custom_tools
from utils.logging_config import get_logger

logger = get_logger("api.mcp")

bp = Blueprint("mcp", __name__)

MCP_PATH = "/mcp"

get_database = None
config_manager = None
version = "dev"

INSTRUCTIONS = (
    "SoulSync manages a music and video library and downloads what's missing. "
    "To get an album: find_album, then request_album with the id it gives. "
    "To get one track: request_track with \"Artist - Title\". "
    "The search_library_* tools say what is already owned, check those first. "
    "Downloads run in the background, list_downloads shows progress."
)


def configure(*, get_database, config_manager, version="dev"):
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager
    globals()["version"] = version


def create_blueprint():
    return bp


def enabled() -> bool:
    try:
        return bool(config_manager.get("mcp.enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _origin_ok() -> bool:
    """browsers send Origin, ai clients don't. a page on another site must not
    be able to drive this with a key it found (dns rebinding)."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        return urlsplit(origin).netloc.lower() == (request.host or "").lower()
    except ValueError:
        return False


# custom tools

def _find_album(args, _ctx):
    from core.metadata.registry import get_musicbrainz_client

    found = get_musicbrainz_client().search_albums(str(args["query"]), limit=int(args.get("limit") or 10))
    return {"albums": [{
        "album_id": a.id,
        "title": a.name,
        "artists": list(a.artists or []),
        "release_date": a.release_date,
        "tracks": a.total_tracks,
        "type": a.album_type,
        "secondary_types": list(getattr(a, "secondary_types", None) or []),
        "disambiguation": getattr(a, "disambiguation", None) or "",
    } for a in found or []]}


def _request_album(args, _ctx):
    from core.metadata.registry import get_musicbrainz_client
    from core.wishlist.album_request import queue_album_tracks

    album = get_musicbrainz_client().get_album(str(args["album_id"]), include_tracks=True)
    tracks = (album or {}).get("tracks") or []
    if not tracks:
        raise ToolError("couldn't find that album or its tracklist on MusicBrainz")
    artists = album.get("artists") or [{}]
    artist = {"id": "", "name": (artists[0] or {}).get("name") or ""}
    queued = queue_album_tracks(
        album, artist, profile_id=int(getattr(g, "profile_id", None) or 1), get_database=get_database,
        active_server=config_manager.get_active_media_server(), logger=logger, requested_via="mcp")
    return {"album": album.get("name"), "artist": artist["name"], "tracks": len(tracks), "queued": queued,
            "note": "owned and already-wishlisted tracks aren't queued again. they download on the next "
                    "wishlist run, or call process_wishlist."}


class ToolError(Exception):
    """a tool failing in a way the ai should read, not a server bug."""


TOOLS = API_TOOLS + custom_tools(_find_album, _request_album)


def visible_tools(scope):
    from api.auth import SCOPE_ADMIN, read_key_may_access

    if scope == SCOPE_ADMIN:
        return list(TOOLS)
    return [t for t in TOOLS
            if (t.read_ok if t.handler else read_key_may_access(t.method, t.sample_path()))]


def _call_api(tool, args, raw_key):
    method, path, query, body = build_request(tool, args)
    resp = current_app.test_client().open(
        path, method=method, query_string=query, json=body,
        headers={"Authorization": f"Bearer {raw_key}"},
        environ_base={"REMOTE_ADDR": request.remote_addr or "127.0.0.1"})
    data = resp.get_json(silent=True)
    if not isinstance(data, dict):
        return protocol.tool_result(f"soulsync answered {resp.status_code} with no json", is_error=True)
    if resp.status_code >= 400 or data.get("success") is False:
        err = data.get("error") or {}
        message = err.get("message") if isinstance(err, dict) else str(err)
        return protocol.tool_result(f"{message or 'failed'} (http {resp.status_code})", is_error=True)
    out = {"data": data.get("data", data)}
    if data.get("pagination"):
        out["pagination"] = data["pagination"]
    return protocol.tool_result(out)


def _caller():
    """(raw key, scope) of a valid key, or None."""
    from api.auth import _request_api_key, key_scope, matched_api_key

    record = matched_api_key()
    if record is None:
        return None
    return _request_api_key(), key_scope(record)


@bp.route(MCP_PATH, methods=["GET", "DELETE"])
def mcp_other():
    if not enabled():
        return jsonify({"error": "not found"}), 404
    # no sse stream and no sessions here, post is the whole transport
    return Response(status=405, headers={"Allow": "POST"})


@bp.route(MCP_PATH, methods=["POST"])
def mcp_post():
    if not enabled():
        return jsonify({"error": "not found"}), 404
    if not _origin_ok():
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "origin not allowed"}}), 403
    caller = _caller()
    if caller is None:
        return Response('{"error": "a SoulSync API key is required"}', status=401,
                        mimetype="application/json",
                        headers={"WWW-Authenticate": 'Bearer realm="SoulSync"'})
    raw_key, scope = caller

    def call(tool, args):
        problem = check_args(tool, args)
        if problem:
            return protocol.tool_result(problem, is_error=True)
        try:
            if tool.handler:
                return protocol.tool_result(tool.handler(args, None))
            return _call_api(tool, args, raw_key)
        except ToolError as e:
            return protocol.tool_result(str(e), is_error=True)
        except Exception as e:  # noqa: BLE001 - the ai gets the message, the log gets the trace
            logger.exception("mcp tool %s failed", tool.name)
            return protocol.tool_result(f"{tool.name} failed: {e}", is_error=True)

    server = protocol.Server(name="soulsync", version=version, instructions=INSTRUCTIONS,
                             tools=lambda: visible_tools(scope), call=call)
    status, body = server.handle(request.get_data(), request.headers)
    if body is None:
        return Response(status=status)
    return jsonify(body), status
