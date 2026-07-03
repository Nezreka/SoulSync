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

from utils.logging_config import get_logger

logger = get_logger("api.library_v2")

# In-process import job state (single library, single job at a time).
_import_lock = threading.Lock()
_import_state: Dict[str, Any] = {"running": False, "stage": None, "current": 0,
                                 "total": 0, "stats": None, "error": None,
                                 "finished_at": None}

# Bulk monitor / upgrade-scan job state (background; tracklist resolution can
# hit metadata providers once per release, so these must not block a request).
_job_lock = threading.Lock()
_job_state: Dict[str, Any] = {"running": False, "kind": None, "current": 0,
                              "total": 0, "result": None, "error": None,
                              "finished_at": None}

_MONITOR_TABLES = {"artists": "lib2_artists", "albums": "lib2_albums", "tracks": "lib2_tracks"}
_PROFILE_TABLES = {"artists": "lib2_artists", "albums": "lib2_albums", "tracks": "lib2_tracks"}


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
                               profile_id_getter: Optional[Callable[[], int]] = None) -> None:
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
        return None

    def _conn():
        return get_database()._get_connection()

    def _profile() -> int:
        try:
            return int(profile_id_getter()) if profile_id_getter else 1
        except Exception:
            return 1

    # -- read endpoints -------------------------------------------------------

    @app.route("/api/library/v2/enabled")
    def lib2_enabled():
        return jsonify({"success": True, "enabled": _enabled()})

    @app.route("/api/library/v2/artists")
    def lib2_list_artists():
        guard = _guard()
        if guard:
            return guard
        from core.library2 import queries as Q
        search = request.args.get("search", "")
        sort = request.args.get("sort", "name")
        monitored = request.args.get("monitored", "all")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 75))
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
        for entry in data.get("albums", []) + data.get("singles", []):
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
        # Slow path: resolve + cache (opens a DB connection).
        conn = db._get_connection()
        try:
            path = build_artwork(db, conn, config_manager, kind, eid, force=force)
        finally:
            conn.close()
        if not path:
            return "", 404
        target = thumb_file(db, kind, eid) if want_thumb else artwork_file(db, kind, eid)
        return _send_art(target if target.exists() else artwork_file(db, kind, eid))

    # -- monitoring (mirrors watchlist / wishlist) ----------------------------

    def _mirror_artist_watchlist(db, conn, artist_id: int, monitored: bool) -> None:
        row = conn.execute(
            "SELECT name, spotify_id, musicbrainz_id FROM lib2_artists WHERE id=?", (artist_id,)
        ).fetchone()
        if not row:
            return
        ext = row["spotify_id"] or row["musicbrainz_id"]
        if not ext:
            return  # no external id → stays lib2-local only
        source = "spotify" if row["spotify_id"] else "musicbrainz"
        try:
            if monitored:
                db.add_artist_to_watchlist(ext, row["name"], _profile(), source)
            else:
                db.remove_artist_from_watchlist(ext, _profile())
        except Exception as e:  # noqa: BLE001
            logger.debug("watchlist mirror failed (artist %s): %s", artist_id, e)

    def _wishlist_profile_id() -> int:
        """The legacy wishlist/watchlist profile is the active SoulSync user profile."""
        return _profile()

    def _track_wishlist_payload(conn, track_id: int) -> Optional[Dict[str, Any]]:
        t = conn.execute(
            """SELECT t.id AS track_id, t.spotify_id, t.title, t.track_number,
                      t.disc_number, t.duration, t.quality_profile_id,
                      al.id AS album_id, al.title album_title, al.spotify_id album_spotify,
                      al.track_count, al.expected_track_count, al.album_type,
                      qp.name AS quality_profile_name, qp.upgrade_policy,
                      qp.upgrade_cutoff_index, qp.ranked_targets,
                      EXISTS(SELECT 1 FROM lib2_track_files tf
                             WHERE tf.track_id = t.id AND tf.path IS NOT NULL AND tf.path <> '') has_file
               FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
               LEFT JOIN quality_profiles qp ON qp.id = t.quality_profile_id
               WHERE t.id = ?""",
            (track_id,),
        ).fetchone()
        if not t:
            return None
        artists = [r["name"] for r in conn.execute(
            """SELECT ar.name FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id = ta.artist_id
               WHERE ta.track_id = ? ORDER BY ta.position""", (track_id,))]
        source_track_id = t["spotify_id"] or f"lib2-track:{t['track_id']}"
        source_album_id = t["album_spotify"] or f"lib2-album:{t['album_id']}"
        file_row = conn.execute(
            "SELECT * FROM lib2_track_files WHERE track_id = ? ORDER BY id LIMIT 1",
            (track_id,),
        ).fetchone()
        file_info = dict(file_row) if file_row else None
        profile_info = {
            "id": t["quality_profile_id"],
            "name": t["quality_profile_name"] or "",
            "upgrade_policy": t["upgrade_policy"] or "acceptable",
            "upgrade_cutoff_index": t["upgrade_cutoff_index"] or 0,
            "ranked_targets": t["ranked_targets"] or "[]",
        }

        from core.library2.quality_eval import is_upgrade_policy
        should_queue = not bool(t["has_file"])
        if t["has_file"] and is_upgrade_policy(profile_info["upgrade_policy"]):
            try:
                from core.library2.quality_eval import evaluate_file, profile_targets
                targets, upgrade_policy, cutoff = profile_targets(profile_info)
                should_queue = bool(evaluate_file(
                    file_info, targets, upgrade_policy, cutoff)["upgrade_candidate"])
            except Exception as e:  # noqa: BLE001
                logger.debug("quality-profile upgrade check failed (track %s): %s", track_id, e)
                should_queue = False

        return {
            "id": source_track_id, "name": t["title"],
            "provider": "spotify" if t["spotify_id"] else "library_v2",
            "source": "library_v2",
            "artists": [{"name": n} for n in artists],
            "album": {
                "name": t["album_title"],
                "id": source_album_id,
                "total_tracks": t["expected_track_count"] or t["track_count"] or 1,
                "album_type": t["album_type"],
            },
            "track_number": t["track_number"],
            "disc_number": t["disc_number"],
            "duration_ms": t["duration"],
            "quality_profile_id": t["quality_profile_id"],
            "quality_profile": profile_info,
            "_album_type": t["album_type"],
            "_has_file": bool(t["has_file"]),
            "_should_queue": should_queue,
            "_source_album_id": source_album_id,
            "_source_info": {
                "source": "library_v2",
                "lib2_track_id": t["track_id"],
                "lib2_album_id": t["album_id"],
                "quality_profile_id": t["quality_profile_id"],
                "quality_profile_name": profile_info["name"],
                "upgrade_policy": profile_info["upgrade_policy"],
                "upgrade_check": bool(t["has_file"]),
            },
        }

    def _mirror_tracks_wishlist(db, conn, track_ids: List[int], monitored: bool) -> int:
        mirrored = 0
        profile_id = _wishlist_profile_id()
        for tid in track_ids:
            payload = _track_wishlist_payload(conn, tid)
            if not payload:
                continue
            stype = "single" if payload.pop("_album_type", "") == "single" else "album"
            should_queue = bool(payload.pop("_should_queue", False))
            source_album_id = payload.pop("_source_album_id", "")
            source_info = payload.pop("_source_info", {})
            payload.pop("_has_file", None)
            try:
                if monitored:
                    if not should_queue:
                        continue
                    # quality_profile_id is the app-wide profile the download/
                    # import pipeline resolves live (load_profile_by_id) — THIS
                    # is what makes "this artist must satisfy profile X" reach
                    # the actual search/import decisions.
                    ok = db.add_to_wishlist(payload, source_type=stype,
                                            source_info=source_info,
                                            user_initiated=True,
                                            profile_id=profile_id,
                                            quality_profile_id=payload.get("quality_profile_id"))
                else:
                    ok = db.remove_from_wishlist(payload["id"], profile_id)
                    if source_album_id:
                        ok = db.remove_from_wishlist(
                            f"{payload['id']}::{source_album_id}", profile_id
                        ) or ok
                if ok:
                    mirrored += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("wishlist mirror failed (track %s): %s", tid, e)
        return mirrored

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
            track_ids: List[int] = []
            if entity == "albums":
                track_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM lib2_tracks WHERE album_id=?", (eid,))]
                cur.execute("UPDATE lib2_tracks SET monitored=? WHERE album_id=?",
                            (1 if monitored else 0, eid))
            elif entity == "tracks":
                track_ids = [eid]
            # Commit the lib2 flag FIRST so the write lock is released before the
            # watchlist/wishlist mirror opens its own connections (avoids SQLite
            # "database is locked" from nested writers).
            conn.commit()
            # Mirror to the existing watchlist / wishlist systems (reads via conn,
            # writes via db.* on their own connections).
            mirrored = 0
            if entity == "artists":
                _mirror_artist_watchlist(db, conn, eid, monitored)
            elif track_ids:
                mirrored = _mirror_tracks_wishlist(db, conn, track_ids, monitored)
        finally:
            conn.close()
        return jsonify({"success": True, "monitored": monitored, "mirrored": mirrored})

    @app.route("/api/library/v2/<entity>/<int:eid>/quality-profile", methods=["POST"])
    def lib2_set_quality_profile(entity, eid):
        guard = _guard()
        if guard:
            return guard
        table = _PROFILE_TABLES.get(entity)
        if not table:
            return jsonify({"success": False, "error": "Unknown entity"}), 400
        profile_id = int((request.json or {}).get("quality_profile_id") or 0)
        cascade = bool((request.json or {}).get("cascade", True))
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
            if is_upgrade_policy(profile["upgrade_policy"]):
                if entity == "artists":
                    auto_monitor_track_ids = [r["id"] for r in conn.execute(
                        "SELECT id FROM lib2_tracks "
                        "WHERE album_id IN (SELECT id FROM lib2_albums WHERE primary_artist_id=?)",
                        (eid,),
                    )]
                    cur.execute(
                        "UPDATE lib2_tracks SET monitored=1 "
                        "WHERE album_id IN (SELECT id FROM lib2_albums WHERE primary_artist_id=?)",
                        (eid,),
                    )
                    auto_monitored = cur.rowcount
                elif entity == "albums":
                    auto_monitor_track_ids = [r["id"] for r in conn.execute(
                        "SELECT id FROM lib2_tracks WHERE album_id=?",
                        (eid,),
                    )]
                    cur.execute("UPDATE lib2_tracks SET monitored=1 WHERE album_id=?", (eid,))
                    auto_monitored = cur.rowcount
                elif entity == "tracks":
                    auto_monitor_track_ids = [eid]
                    cur.execute("UPDATE lib2_tracks SET monitored=1 WHERE id=?", (eid,))
                    auto_monitored = cur.rowcount
            conn.commit()
            mirrored = 0
            if auto_monitor_track_ids:
                mirrored = _mirror_tracks_wishlist(db, conn, auto_monitor_track_ids, True)
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
        try:
            from core.library2.discography import expand_artist_discography
            stats = expand_artist_discography(get_database(), artist_id)
        except ValueError:
            return jsonify({"success": False, "error": "Artist not found"}), 404
        except Exception as e:  # noqa: BLE001
            logger.error("Discography refresh failed (artist %s): %s", artist_id, e)
            return jsonify({"success": False, "error": str(e)}), 500
        return jsonify({"success": True, **stats})

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
        with _job_lock:
            if _job_state["running"]:
                return jsonify({"success": False, "error": "A bulk job is already running"}), 409
            _job_state.update(running=True, kind=f"monitor:{scope}", current=0,
                              total=0, result=None, error=None, finished_at=None)

        def _run():
            import time as _t
            db = get_database()
            try:
                conn = db._get_connection()
                try:
                    albums = [r["id"] for r in conn.execute(
                        f"""SELECT al.id FROM lib2_album_artists aa
                            JOIN lib2_albums al ON al.id = aa.album_id
                           WHERE aa.artist_id = ? AND {type_filter}""", (artist_id,))]
                    _job_state.update(total=len(albums))
                    mirrored = 0
                    for i, album_id in enumerate(albums):
                        _job_state.update(current=i)
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
                        track_ids = _bulk_track_ids_for_albums(conn, [album_id])
                        if track_ids:
                            marks = ",".join("?" for _ in track_ids)
                            conn.execute(
                                f"UPDATE lib2_tracks SET monitored=? WHERE id IN ({marks})",
                                [1 if monitored else 0, *track_ids])
                        conn.commit()
                        if track_ids:
                            mirrored += _mirror_tracks_wishlist(db, conn, track_ids, monitored)
                    _job_state.update(result={"albums": len(albums), "mirrored": mirrored})
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                logger.error("Bulk monitor failed (artist %s): %s", artist_id, e, exc_info=True)
                _job_state.update(error=str(e))
            finally:
                _job_state.update(running=False, finished_at=_t.time())

        threading.Thread(target=_run, name="lib2-bulk-monitor", daemon=True).start()
        return jsonify({"success": True, "started": True})

    @app.route("/api/library/v2/jobs/status")
    def lib2_job_status():
        guard = _guard()
        if guard:
            return guard
        return jsonify({"success": True, **_job_state})

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

    def _unmonitor_tracks_and_delete(db, conn, *, artist_id: Optional[int] = None,
                                     album_ids: Optional[List[int]] = None) -> Dict[str, int]:
        """Shared delete path: unmirror wishlist entries, then delete lib2 rows.

        NEVER touches files on disk — this removes library entries only,
        exactly like Lidarr's 'delete artist' without the delete-files box.
        """
        if album_ids is None:
            album_ids = [r["id"] for r in conn.execute(
                """SELECT al.id FROM lib2_album_artists aa
                   JOIN lib2_albums al ON al.id = aa.album_id WHERE aa.artist_id=?""",
                (artist_id,))]
        track_ids = _bulk_track_ids_for_albums(conn, album_ids)
        # Pull monitored tracks out of the wishlist BEFORE their rows vanish
        # (the payload builder needs the rows to compute the wishlist keys).
        unmirrored = _mirror_tracks_wishlist(db, conn, track_ids, False) if track_ids else 0
        removed_albums = 0
        for aid_ in album_ids:
            conn.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (aid_,))
            conn.execute(
                "DELETE FROM lib2_track_artists WHERE track_id IN "
                "(SELECT id FROM lib2_tracks WHERE album_id=?)", (aid_,))
            conn.execute(
                "DELETE FROM lib2_track_files WHERE track_id IN "
                "(SELECT id FROM lib2_tracks WHERE album_id=?)", (aid_,))
            conn.execute("DELETE FROM lib2_tracks WHERE album_id=?", (aid_,))
            conn.execute("DELETE FROM lib2_albums WHERE id=?", (aid_,))
            removed_albums += 1
        return {"albums": removed_albums, "tracks": len(track_ids), "unmirrored": unmirrored}

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
            _mirror_artist_watchlist(db, conn, artist_id, False)
            stats = _unmonitor_tracks_and_delete(db, conn, artist_id=artist_id)
            conn.execute("DELETE FROM lib2_artists WHERE id=?", (artist_id,))
            conn.commit()
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
        finally:
            conn.close()
        return jsonify({"success": True, **stats})

    @app.route("/api/library/v2/artists/<int:artist_id>/history")
    def lib2_artist_history(artist_id):
        """Recent download/import provenance for this artist (Lidarr's History
        tab), read from the existing ``track_downloads`` table by artist name."""
        guard = _guard()
        if guard:
            return guard
        limit = min(int(request.args.get("limit", 50)), 200)
        conn = _conn()
        try:
            artist = conn.execute(
                "SELECT name FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()
            if not artist:
                return jsonify({"success": False, "error": "Artist not found"}), 404
            try:
                rows = conn.execute(
                    """SELECT track_title, track_album, source_service, source_username,
                              audio_quality, bit_depth, sample_rate, bitrate,
                              file_path, status, created_at
                       FROM track_downloads
                       WHERE lower(track_artist) = lower(?)
                       ORDER BY id DESC LIMIT ?""",
                    (artist["name"], limit),
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
        with _job_lock:
            if _job_state["running"]:
                return jsonify({"success": False, "error": "A bulk job is already running"}), 409
            _job_state.update(running=True, kind="upgrade-scan", current=0,
                              total=0, result=None, error=None, finished_at=None)

        def _run():
            import time as _t
            db = get_database()
            try:
                conn = db._get_connection()
                try:
                    rows = conn.execute(
                        """SELECT t.id FROM lib2_tracks t
                           JOIN quality_profiles qp ON qp.id = t.quality_profile_id
                          WHERE t.monitored = 1
                            AND qp.upgrade_policy IN ('until_top', 'until_cutoff')
                            AND EXISTS (SELECT 1 FROM lib2_track_files tf
                                        WHERE tf.track_id = t.id
                                          AND tf.path IS NOT NULL AND tf.path <> '')"""
                    ).fetchall()
                    track_ids = [r["id"] for r in rows]
                    _job_state.update(total=len(track_ids))
                    # _mirror_tracks_wishlist re-checks upgrade_candidate per
                    # track and only queues genuine upgrade candidates.
                    queued = _mirror_tracks_wishlist(db, conn, track_ids, True)
                    _job_state.update(result={"checked": len(track_ids), "queued": queued})
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                logger.error("Upgrade scan failed: %s", e, exc_info=True)
                _job_state.update(error=str(e))
            finally:
                _job_state.update(running=False, finished_at=_t.time())

        threading.Thread(target=_run, name="lib2-upgrade-scan", daemon=True).start()
        return jsonify({"success": True, "started": True})

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
            # Collect the album ids in scope, then bust their cached artwork so the
            # next artwork request re-resolves from the (possibly retagged) files.
            if entity == "albums":
                album_ids = [eid]
            else:
                album_ids = [r["id"] for r in conn.execute(
                    """SELECT al.id FROM lib2_album_artists aa JOIN lib2_albums al ON al.id=aa.album_id
                       WHERE aa.artist_id=?""", (eid,))]
            from core.library2.artwork import artwork_file
            for aid in album_ids:
                f = artwork_file(db, "album", aid)
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass
            if entity == "artists":
                af = artwork_file(db, "artist", eid)
                if af.exists():
                    try:
                        af.unlink()
                    except OSError:
                        pass
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

        def _run():
            from core.library2.importer import import_legacy_library
            import time as _t

            def _progress(stage, current, total):
                _import_state.update(stage=stage, current=current, total=total)

            try:
                stats = import_legacy_library(get_database(), reset=reset, progress=_progress)
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
