"""Video library mapping endpoints.

GET  /api/video/libraries -> discover the active server's Movies/TV libraries
                             + the user's current selection.
POST /api/video/libraries -> save {movies, tv} (library titles) for the active
                             server. The scanner then reads only those.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.libraries")


def register_routes(bp):
    @bp.route("/libraries", methods=["GET"])
    def video_libraries():
        from . import get_video_db
        try:
            from core.video.sources import list_video_libraries, resolve_video_server
            libs = list_video_libraries() or {"server": None, "movies": [], "tv": []}
            server = libs.get("server") or resolve_video_server()
            libs["selected"] = (get_video_db().get_library_selection(server)
                                if server else {"movies": None, "tv": None})
            return jsonify(libs)
        except Exception:
            logger.exception("Failed to list video libraries")
            return jsonify({"error": "Failed to list video libraries"}), 500

    @bp.route("/server", methods=["GET"])
    def video_server_status():
        """Which server the video side uses + which of Plex/Jellyfin are configured
        (so the UI can show a picker, or a 'connect a server' message)."""
        try:
            from core.video.sources import (resolve_video_server,
                                             video_plex_config, video_jellyfin_config)
            plex = bool(video_plex_config().get("base_url"))
            jelly = bool(video_jellyfin_config().get("base_url"))
            return jsonify({"server": resolve_video_server(), "plex": plex, "jellyfin": jelly})
        except Exception:
            logger.exception("video server status failed")
            return jsonify({"server": None, "plex": False, "jellyfin": False})

    @bp.route("/server", methods=["POST"])
    def video_server_set():
        """Set the explicit video-side server pick (only meaningful when both Plex
        and Jellyfin are configured)."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        choice = body.get("server")
        if choice not in ("plex", "jellyfin"):
            return jsonify({"error": "bad server"}), 400
        get_video_db().set_setting("video_server", choice)
        return jsonify({"status": "saved", "server": choice})

    @bp.route("/server-config", methods=["GET"])
    def video_server_config_get():
        """The video side's OWN server connection — its stored creds when set, else
        the values INHERITED (read-only) from music. 'inherited' flags tell the UI a
        field is a placeholder it can override; tokens/keys are returned masked."""
        try:
            from core.video.sources import video_plex_config, video_jellyfin_config
            p, j = video_plex_config(), video_jellyfin_config()

            def mask(v):
                v = v or ""
                return ("•" * 12) if v else ""
            return jsonify({
                "plex": {"base_url": p.get("base_url") or "", "token": mask(p.get("token")),
                         "has_token": bool(p.get("token")), "inherited": p.get("source") == "music"},
                "jellyfin": {"base_url": j.get("base_url") or "", "api_key": mask(j.get("api_key")),
                             "has_key": bool(j.get("api_key")), "inherited": j.get("source") == "music"},
            })
        except Exception:
            logger.exception("video server-config get failed")
            return jsonify({"plex": {}, "jellyfin": {}})

    @bp.route("/server-config", methods=["POST"])
    def video_server_config_set():
        """Save the video side's OWN Plex/Jellyfin creds to video.db (NEVER the music
        config). An empty/blank field clears that override → video falls back to
        inheriting music's value. A masked token (all •) is left untouched."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        db = get_video_db()

        def is_mask(v):
            return bool(v) and set(str(v)) == {"•"}

        def put(key, val):
            if is_mask(val):
                return  # unchanged masked secret — keep what's stored
            db.set_setting(key, (val or "").strip())

        plex = body.get("plex") or {}
        jelly = body.get("jellyfin") or {}
        if "base_url" in plex:
            put("video_plex_url", plex.get("base_url"))
        if "token" in plex:
            put("video_plex_token", plex.get("token"))
        if "base_url" in jelly:
            put("video_jellyfin_url", jelly.get("base_url"))
        if "api_key" in jelly:
            put("video_jellyfin_key", jelly.get("api_key"))
        return jsonify({"status": "saved"})

    @bp.route("/server-config/test", methods=["POST"])
    def video_server_config_test():
        """Test the video side's effective connection for one server, using its OWN
        stored/inherited creds (so it verifies exactly what the video scan will use)."""
        body = request.get_json(silent=True) or {}
        which = body.get("server")
        if which not in ("plex", "jellyfin"):
            return jsonify({"success": False, "error": "bad server"}), 400
        try:
            if which == "plex":
                from core.video.sources import video_plex_config, PLEX_SCAN_TIMEOUT
                cfg = video_plex_config()
                if not cfg.get("base_url") or not cfg.get("token"):
                    return jsonify({"success": False, "error": "Plex URL/token not set"})
                from plexapi.server import PlexServer
                srv = PlexServer(cfg["base_url"], cfg["token"], timeout=PLEX_SCAN_TIMEOUT)
                return jsonify({"success": True, "message": "Connected to " + (srv.friendlyName or "Plex")})
            from core.video.sources import video_jellyfin_config, video_jellyfin_test
            ok, message = video_jellyfin_test(video_jellyfin_config())
            if ok:
                return jsonify({"success": True, "message": message})
            return jsonify({"success": False, "error": message})
        except Exception as e:
            return jsonify({"success": False, "error": str(e) or "connection failed"})

    @bp.route("/jellyfin/users", methods=["GET"])
    def video_jellyfin_users():
        """List the Jellyfin server's users so the video side can pick one (its
        libraries are scoped to that user) — mirrors the music user picker. Uses
        video's own effective Jellyfin creds."""
        from . import get_video_db
        try:
            from core.video.sources import video_jellyfin_config
            cfg = video_jellyfin_config()
            base = (cfg.get("base_url") or "").rstrip("/")
            key = cfg.get("api_key") or ""
            if not base or not key:
                return jsonify({"success": False, "users": []})
            import requests
            r = requests.get(base + "/Users", headers={"X-Emby-Token": key}, timeout=8)
            if r.status_code != 200:
                return jsonify({"success": False, "users": [], "error": "HTTP %d" % r.status_code})
            users = r.json() or []
            out = [{"id": u.get("Id"), "name": u.get("Name"),
                    "admin": bool((u.get("Policy") or {}).get("IsAdministrator"))}
                   for u in users if u.get("Id")]
            selected = get_video_db().get_setting("video_jellyfin_user") or ""
            return jsonify({"success": True, "users": out, "selected": selected})
        except Exception as e:
            return jsonify({"success": False, "users": [], "error": str(e)})

    @bp.route("/jellyfin/user", methods=["POST"])
    def video_jellyfin_user_set():
        """Persist the chosen Jellyfin user (its Id) for the video side."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        get_video_db().set_setting("video_jellyfin_user", (body.get("user") or "").strip())
        return jsonify({"status": "saved"})

    @bp.route("/libraries", methods=["POST"])
    def save_video_libraries():
        from . import get_video_db
        try:
            from core.video.sources import resolve_video_server
            body = request.get_json(silent=True) or {}
            server = resolve_video_server()
            if not server:
                return jsonify({"error": "no video server"}), 400
            get_video_db().set_library_selection(server, body.get("movies"), body.get("tv"))
            return jsonify({"status": "saved", "server": server})
        except Exception:
            logger.exception("Failed to save video library selection")
            return jsonify({"error": "Failed to save selection"}), 500
