"""a video's audio and subtitle tracks for the player, and its subtitles.

    GET /api/player/tracks/<item_type>/<item_id>?part=     {audio, subtitles, chapters, markers}
    GET /api/player/markers/<item_type>/<item_id>?part=    {markers}
    GET /api/player/subtitle/<item_type>/<item_id>?part=&sub=s0|x0
                                                           WebVTT
    GET /api/player/chapters/<item_type>/<item_id>?part=   {chapters}
    GET /api/player/chapter-thumb/<item_type>/<item_id>/<n>?part=&v=
                                                           a chapter's picture

the same play check as the bytes (core/player/playable.resolve). audio
tracks are numbered among the file's audio, subtitles ``s<n>`` (in the
file, among its subtitles) or ``x<n>`` (a file beside it). an image
subtitle can't be WebVTT: the player burns it in through the converter.

a chapter carries ``thumb``, its picture's url, once the background worker
has made it (core/player/chapter_thumbs.py), else null. the chapters route
is for a detail page: it never reads the file, only what the worker and the
player already know, so a page of cards costs nothing.
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, request, send_file

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
        return jsonify({"success": True, "audio": [], "subtitles": [], "unavailable": "no ffmpeg",
                        "markers": _markers(item_type, source, [])})
    out = K.public(found[0])
    found_markers = _markers(item_type, source, out.get("chapters") or [])
    if out.get("chapters"):
        out["chapters"] = _with_thumbs(item_type, item_id, source, out["chapters"])
    return jsonify({"success": True, **out, "markers": found_markers})


def _thumb_key(item_type, source):
    """the chapter pictures' key for a resolved movie or episode, or None"""
    from core.player import chapter_thumbs as C
    file_id = source.extra.get("file_id")
    if item_type not in ("movie", "episode") or not file_id:
        return None
    from api.video import get_video_db
    return C.key_for_media_file(get_video_db(), file_id)


def _with_thumbs(item_type, item_id, source, chapters):
    from core.player import chapter_thumbs as C
    try:
        manifest = C.read_manifest(_thumb_key(item_type, source))
    except Exception:  # noqa: BLE001 - chapters without pictures still work
        logger.debug("chapter pictures for %s %s", item_type, item_id, exc_info=True)
        manifest = None
    return C.attach(chapters, manifest, item_type=item_type, item_id=item_id,
                    file_id=source.extra.get("file_id"))


@bp.route("/api/player/chapters/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def chapters(item_type, item_id):
    """a movie's or an episode's chapters with their pictures, for its
    detail page. from the pictures' manifest, else from a probe the player
    already made; never reads the file"""
    from core.player import chapter_thumbs as C
    from core.player import tracks as K
    from core.player.playable import Refused
    try:
        source = _resolve(item_type, item_id)
    except Refused as e:
        return _refused(e)
    file_id = source.extra.get("file_id")
    key = _thumb_key(item_type, source)
    manifest = C.read_manifest(key)
    found = C.from_manifest(manifest, item_type=item_type, item_id=item_id, file_id=file_id)
    if not found and source.mode == "file" and source.path:
        size = (source.extra.get("file") or {}).get("size_bytes")
        probed = K.cached(K.file_key(source.path, None)) or K.cached_like(
            os.path.basename(source.path), size)
        if probed and probed.get("chapters"):
            found = C.attach(probed["chapters"], manifest, item_type=item_type, item_id=item_id,
                             file_id=file_id)
    return jsonify({"success": True, "chapters": found, "file_id": file_id})


@bp.route("/api/player/chapter-thumb/<item_type>/<path:item_id>/<int:index>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def chapter_thumb(item_type, item_id, index):
    """one chapter's picture. the same play check as the video itself, so a
    profile never sees a frame of something it can't watch. 404 until the
    worker has made it"""
    from core.player import chapter_thumbs as C
    from core.player.playable import Refused
    try:
        source = _resolve(item_type, item_id)
    except Refused as e:
        return _refused(e)
    path = C.thumb_path(_thumb_key(item_type, source), index)
    if path is None:
        return jsonify({"success": False, "error": "not_found",
                        "message": "That chapter has no picture yet."}), 404
    res = send_file(str(path), mimetype="image/webp" if path.suffix == ".webp" else "image/jpeg",
                    conditional=True, max_age=0)
    # a picture is made once; a remade one has a new ?v=, so it never goes stale
    res.headers["Cache-Control"] = ("private, max-age=31536000, immutable" if request.args.get("v")
                                    else "private, max-age=3600")
    return res


def _markers(item_type, source, chapters):
    """skip markers beside the chapters (core/video/markers): intro,
    credits, recap, from the best source each. a home video or a youtube
    clip has only what its chapters say."""
    from core.video.markers import merge, service
    duration = source.duration
    if not duration:
        duration = (source.extra.get("file") or {}).get("runtime_seconds")
    if item_type not in ("movie", "episode") or not source.extra.get("file_id"):
        return [merge.public(m) for m in merge.best(merge.from_chapters(chapters, duration), (), duration)]
    from api.video import get_video_db
    return service.player_markers(get_video_db(), source.extra["file_id"], chapters, duration,
                                  source.path if source.mode == "file" else None)


@bp.route("/api/player/markers/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def markers(item_type, item_id):
    """only the skip markers, for a client that reads tracks elsewhere. the
    chapters count when the file was already probed (a play reads them)."""
    from core.player import tracks as K
    from core.player.playable import Refused
    try:
        source = _resolve(item_type, item_id)
    except Refused as e:
        return _refused(e)
    origin = source.path if source.mode == "file" else source.url
    key = K.file_key(source.path, ("media-file", source.extra.get("file_id"),
                                   origin if source.mode == "file" else None))
    probed = K.cached(key) or {}
    return jsonify({"success": True, "markers": _markers(item_type, source, probed.get("chapters") or [])})


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
