"""a video's audio and subtitle tracks for the player, and its subtitles.

    GET /api/player/tracks/<item_type>/<item_id>?part=     {audio, subtitles}
    GET /api/player/subtitle/<item_type>/<item_id>?part=&sub=s0|x0
                                                           WebVTT
    PUT /api/player/subtitle-delay/<item_type>/<item_id>?part=&sub=x0
        {"delay_ms": -1200}                                {delay_ms}

each subtitle in the tracks answer carries this profile's saved ``delay_ms``
for that subtitle file (the web player and the app both use it), ``ai`` for
one whisper made, and ``low_confidence`` when whisper wasn't sure.

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

VIDEO_TYPES = ("movie", "episode", "youtube", "home")
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


def _video_key(source) -> str:
    """what a saved delay is filed under: the file on this box, or the
    media server's file id for a relayed stream"""
    if source.mode == "file" and source.path:
        return str(source.path)
    return "media-file:%s" % (source.extra.get("file_id") or source.url or "")


def _sub_key(sub) -> tuple:
    """(key, signature) of one subtitle: a sidecar by its name (and size +
    mtime, so a replaced file starts at 0), a track in the file by its id"""
    import os
    from core.player import tracks as K
    if sub.get("external") and sub.get("path"):
        return os.path.basename(sub["path"]), K.sidecar_sig(sub["path"])
    return str(sub.get("id")), ""


def _profile():
    from core.profile_context import get_current_profile_id
    try:
        return int(get_current_profile_id() or 0) or None
    except (TypeError, ValueError):
        return None


def _decorate(source, subs) -> None:
    """this profile's delay per subtitle, and the ai confidence flag"""
    pid = _profile()
    try:
        from core.video.subtitles.ai import guards
        from api.video import get_video_db
        db = get_video_db()
    except Exception:  # noqa: BLE001 - extras, never a failed tracks answer
        return
    try:
        saved = db.subtitle_delays_for(pid, _video_key(source)) if pid else {}
    except Exception:  # noqa: BLE001
        saved = {}
    for sub in subs:
        key, sig = _sub_key(sub)
        d, at_sig = saved.get(key, (0, ""))
        sub["delay_ms"] = 0 if (sig and at_sig and sig != at_sig) else d
        sub.setdefault("ai", False)
        if sub.get("ai") and sub.get("path"):
            try:
                job = db.ai_job_for_output(sub["path"])
            except Exception:  # noqa: BLE001
                job = None
            conf = (job or {}).get("confidence")
            sub["low_confidence"] = conf is not None and float(conf) < guards.LOW_CONFIDENCE


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
    subs = [dict(x) for x in found[0]["subtitles"]]
    _decorate(source, subs)
    return jsonify({"success": True, **K.public(dict(found[0], subtitles=subs))})


@bp.route("/api/player/subtitle-delay/<item_type>/<path:item_id>", methods=["PUT"])
@requires_any(*SECTIONS)
@device_playback
def subtitle_delay(item_type, item_id):
    """save this profile's delay for one subtitle of one file (0 clears)"""
    from core.player.playable import Refused
    try:
        source = _resolve(item_type, item_id)
    except Refused as e:
        return _refused(e)
    pid = _profile()
    if not pid:
        return jsonify({"success": False, "error": "no_profile", "message": "Pick a profile first."}), 400
    body = request.get_json(silent=True) or {}
    try:
        delay = int(round(float(body.get("delay_ms"))))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "bad_delay", "message": "delay_ms must be a number."}), 400
    found = source_tracks(source)
    if found is None:
        return jsonify({"success": False, "error": "no_ffmpeg", "message": "SoulSync can't find ffmpeg."}), 503
    want = request.args.get("sub") or ""
    pick = next((x for x in found[0]["subtitles"] if x["id"] == want), None)
    if pick is None:
        return jsonify({"success": False, "error": "not_found", "message": "There's no such subtitle."}), 404
    key, sig = _sub_key(pick)
    from api.video import get_video_db
    stored = get_video_db().subtitle_delay_set(pid, _video_key(source), key, delay, sig)
    return jsonify({"success": True, "delay_ms": stored})


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
        # a sidecar is kept by path + size + mtime: a resync, an upgrade or
        # a manual replace shows at once instead of the old cached WebVTT
        cache_key = K.sidecar_key(pick["path"]) if pick["external"] else key
        if pick["external"]:
            text = K.to_vtt(ffmpeg, pick["path"], None, cache_key)
        else:
            text = K.to_vtt(ffmpeg, origin, int(pick["id"][1:]), key)
    except Exception:  # noqa: BLE001
        logger.exception("subtitle %s of %s %s failed", want, item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't read that subtitle."}), 500
    import hashlib
    etag = hashlib.sha1(repr((cache_key, want)).encode()).hexdigest()[:20]
    if request.if_none_match and etag in request.if_none_match:
        return Response(status=304, headers={"ETag": f'"{etag}"', "Cache-Control": "private, no-cache"})
    # no-cache: the browser asks again each time (a 304 while the file is the
    # same), so a rewritten subtitle is never served stale from its cache
    return Response(text, mimetype="text/vtt", headers={"Cache-Control": "private, no-cache", "ETag": f'"{etag}"'})


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
