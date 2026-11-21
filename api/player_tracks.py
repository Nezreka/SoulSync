"""a video's audio and subtitle tracks for the player, and its subtitles.

    GET /api/player/tracks/<item_type>/<item_id>?part=     {audio, subtitles}
    GET /api/player/subtitle/<item_type>/<item_id>?part=&sub=s0|x0
                                                           WebVTT

the same play check as the bytes (core/player/playable.resolve). audio
tracks are numbered among the file's audio, subtitles ``s<n>`` (in the
file, among its subtitles) or ``x<n>`` (a file beside it). an image
subtitle can't be WebVTT: the player burns it in through the converter.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from core.connect.device_access import device_playback
from core.permissions import requires_any
from utils.logging_config import get_logger

logger = get_logger("player.tracks")

bp = Blueprint("player_tracks", __name__)

VIDEO_TYPES = ("movie", "episode", "youtube")
SECTIONS = ("movies.access", "tv.access")


def create_blueprint():
    return bp


def _refused(e):
    return jsonify({"success": False, "error": e.code, "message": e.message}), e.status


def source_tracks(source):
    """(tracks, origin, ffmpeg) for a resolved video, or None without ffmpeg"""
    from core.player import tracks as K
    from core.player import transcode as T
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        return None
    origin = source.path if source.mode == "file" else source.url
    key = K.file_key(source.path, ("media-file", source.extra.get("file_id"), origin if source.mode == "file" else None))
    return K.all_tracks(T.ffprobe_for(ffmpeg), origin, key, source.path), origin, ffmpeg, key


def _resolve(item_type, item_id):
    from core.player.playable import resolve
    if item_type not in VIDEO_TYPES:
        from core.player.playable import Refused
        raise Refused(400, "not_video", "Only video has tracks.")
    return resolve(item_type, item_id, request.args.get("part"))


@bp.route("/api/player/tracks/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def tracks(item_type, item_id):
    from core.player import tracks as K
    from core.player.playable import Refused
    try:
        source = _resolve(item_type, item_id)
    except Refused as e:
        return _refused(e)
    found = source_tracks(source)
    if found is None:
        return jsonify({"success": True, "audio": [], "subtitles": [], "unavailable": "no ffmpeg"})
    return jsonify({"success": True, **K.public(found[0])})


@bp.route("/api/player/subtitle/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def subtitle(item_type, item_id):
    from core.player import tracks as K
    from core.player.playable import Refused
    try:
        source = _resolve(item_type, item_id)
    except Refused as e:
        return _refused(e)
    found = source_tracks(source)
    if found is None:
        return jsonify({"success": False, "error": "no_ffmpeg",
                        "message": "SoulSync can't find ffmpeg, so it can't read subtitles."}), 503
    all_tracks, origin, ffmpeg, key = found
    want = request.args.get("sub") or ""
    pick = next((s for s in all_tracks["subtitles"] if s["id"] == want), None)
    if pick is None:
        return jsonify({"success": False, "error": "not_found", "message": "There's no such subtitle."}), 404
    if pick["kind"] != "text":
        return jsonify({"success": False, "error": "image",
                        "message": "This subtitle is pictures; the player draws it in instead."}), 400
    try:
        if pick["external"]:
            text = K.to_vtt(ffmpeg, pick["path"], None, ("sidecar", pick["path"]))
        else:
            text = K.to_vtt(ffmpeg, origin, int(pick["id"][1:]), key)
    except Exception:  # noqa: BLE001
        logger.exception("subtitle %s of %s %s failed", want, item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't read that subtitle."}), 500
    return Response(text, mimetype="text/vtt", headers={"Cache-Control": "private, max-age=3600"})


@bp.route("/api/player/lyrics/track/<int:track_id>", methods=["GET"])
@requires_any("music.access")
@device_playback
def lyrics(track_id):
    """a library track's lyrics: synced (LRC) and plain, the same answer as
    the music side's lyrics route (its local sidecar and tags first, then
    LRCLib). asked by track id: the server finds the file itself, so a
    client never sends a path. a track this profile can't play has none."""
    from api.stats import lyrics_answer
    from core.player.playable import Refused, resolve
    try:
        source = resolve("track", str(track_id))
    except Refused as e:
        return jsonify({"success": False, "error": e.code, "message": e.message}), e.status
    from database.music_database import get_database
    with get_database()._get_connection() as conn:
        row = conn.execute(
            "SELECT t.title, COALESCE(NULLIF(t.track_artist, ''), ar.name), al.title, t.duration "
            "FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
            "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE t.id = ?",
            (int(track_id),)).fetchone()
    if not row:
        return jsonify({"success": False, "error": "not_found", "message": "No such track."}), 404
    title, artist, album, duration_ms = row
    local = source.path if source.mode == "file" else None
    try:
        out, status = lyrics_answer(title or "", artist or "", album or None,
                                    int((duration_ms or 0) / 1000) or None, local)
    except Exception:  # noqa: BLE001
        logger.exception("player lyrics failed for track %s", track_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't get the lyrics."}), 500
    return jsonify(out), status
