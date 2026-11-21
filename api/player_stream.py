"""the player's playback routes: how to play an item, and its bytes.

every request goes through core/player/playable.resolve, the one check for
the player's own session and a paired device's token alike.

    GET /api/player/playable/<item_type>/<item_id>   how to play it (and how
                                                      long finding it took)
    GET /api/player/stream/<item_type>/<item_id>     its bytes, Range/206
    GET /api/player/transcode/<item_type>/<item_id>  a video converted for a
                                                      browser as it streams
                                                      (?t= start, ?q= quality)
    GET .../transcode/<item_type>/<item_id>/start    where it really starts
                                                      (copied video: a keyframe)

``?part=`` picks a video's file version (a media file id) or an audiobook's
part (0 based). item types: track, movie, episode, youtube, audiobook.
podcasts don't come here: their episodes stream from the feed's host
through the podcast side's checked audio proxy.
"""

from __future__ import annotations

from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_file

from core.connect.device_access import device_playback
from core.permissions import requires_any
from utils.logging_config import get_logger

logger = get_logger("player.stream")

bp = Blueprint("player_stream", __name__)

# any section that has something to play; the item's own section is checked in resolve
SECTIONS = ("music.access", "movies.access", "tv.access", "books.access")


def create_blueprint():
    return bp


def _refused(e):
    return jsonify({"success": False, "error": e.code, "message": e.message}), e.status


VIDEO_TYPES = ("movie", "episode", "youtube")


def _transcode_url(item_type: str, item_id: str, part, quality: str) -> str:
    args = [f"q={quote(quality, safe='')}"]
    if part not in (None, ""):
        args.append(f"part={quote(str(part), safe='')}")
    return f"/api/player/transcode/{item_type}/{quote(item_id, safe='')}?" + "&".join(args)


def _plan_for(source, quality: str, audio: int = 0, burn=None, delay_ms: int = 0):
    """the plan for this source. a picked audio track other than the first,
    or a subtitle to burn in, reads the file's tracks for the right codec."""
    from core.player import transcode as T
    info = source.private
    audio_codec = info.get("audio_codec")
    if audio or burn is not None:
        from api.player_tracks import source_tracks
        found = source_tracks(source)
        tracks = found[0]["audio"] if found else []
        if audio < len(tracks):
            audio_codec = tracks[audio]["codec"]
    return T.plan(info.get("video_codec"), audio_codec, quality=quality,
                  hdr=info.get("dynamic_range"), height=info.get("height"), audio=audio, burn=burn,
                  delay_ms=delay_ms)


def _int_arg(name):
    try:
        value = request.args.get(name)
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _stream_url(item_type: str, item_id: str, part) -> str:
    url = f"/api/player/stream/{item_type}/{quote(item_id, safe='')}"
    return f"{url}?part={quote(str(part), safe='')}" if part not in (None, "") else url


@bp.route("/api/player/playable/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def playable(item_type, item_id):
    import time

    from core.player.playable import Refused, resolve
    part = request.args.get("part")
    started = time.monotonic()
    try:
        source = resolve(item_type, item_id, part)
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("playable failed for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't get that ready to play."}), 500
    out = source.public()
    # how long finding the bytes took (a sleeping drive spinning up shows here),
    # and where they come from: this box's disk, or the media server
    out["took_ms"] = round((time.monotonic() - started) * 1000)
    out.setdefault("via", "disk" if source.mode == "file" else source.extra.get("via") or "media server")
    if out["mode"] == "stream":
        out["url"] = _stream_url(item_type, item_id, part if part is not None else source.extra.get("part"))
    if item_type in VIDEO_TYPES:
        _offer_transcode(out, source, item_type, item_id, part, request.args.get("q") or "original",
                         _int_arg("audio") or 0, _int_arg("burn"), _int_arg("delay") or 0)
    return jsonify({"success": True, **out})


def _offer_transcode(out, source, item_type, item_id, part, quality, audio=0, burn=None, delay_ms=0):
    """a video a browser can't play as it is, or asked for smaller: the
    converted stream's url, and what's converted (copy what plays, convert
    the rest). no ffmpeg: the player says so, as it did before."""
    from core.player import transcode as T
    p = _plan_for(source, quality, audio, burn, delay_ms)
    verdict = (source.verdict or {}).get("verdict")
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        if verdict == "no":
            out["transcode_unavailable"] = "SoulSync can't find ffmpeg, so it can't convert this file."
        return
    file_part = source.extra.get("file_id") if part in (None, "") else part
    url = _transcode_url(item_type, item_id, file_part, p.quality)
    if audio:
        url += f"&audio={int(audio)}"
    if burn is not None:
        url += f"&burn={int(burn)}"
    if p.delay_ms:
        url += f"&delay={p.delay_ms}"
    # the converter's address for this file, to switch audio track or burn in
    # a subtitle later (the player adds &audio= / &burn= / &t=)
    out["transcode_url"] = _transcode_url(item_type, item_id, file_part, "original")
    # only a file a browser can't play, or a smaller quality asked for, is
    # converted. a 'maybe' plays as it is, with the converted stream named as
    # the fallback the player switches to if the browser can't after all
    # another audio track, or pictures drawn in: always the converter (a
    # browser plays a file's first audio track only)
    if verdict != "no" and quality == "original" and not audio and burn is None and not p.delay_ms:
        out["fallback_url"] = url
        return
    out["url"] = url
    out["mime"] = T.MIME
    out["transcode"] = p.public(T.encoder(ffmpeg)[1])


@bp.route("/api/player/stream/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def stream(item_type, item_id):
    from core.player.playable import Refused, resolve
    from core.player.relay import relay
    try:
        source = resolve(item_type, item_id, request.args.get("part"))
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("stream failed for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed"}), 500
    if source.mode == "file":
        try:
            # conditional: Range requests get 206, so the player can seek
            return send_file(source.path, mimetype=source.mime, conditional=True, as_attachment=False)
        except OSError:
            logger.exception("stream couldn't open %s", source.path)
            return jsonify({"success": False, "error": "no_file", "message": "That file couldn't be opened."}), 404
    if source.mode == "relay":
        return relay(source.url, request.headers.get("Range"), source.mime)
    return jsonify({"success": False, "error": "external", "message": "That plays from its own address."}), 400


@bp.route("/api/player/transcode/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def transcode(item_type, item_id):
    """a video converted for a browser as it plays: fragmented mp4 from ffmpeg,
    from ``?t=`` seconds (a seek starts a new one). same check as everything
    else; the plan copies what a browser plays and converts the rest."""
    from flask import Response

    from core.player import transcode as T
    from core.player.playable import Refused, resolve
    if item_type not in VIDEO_TYPES:
        return jsonify({"success": False, "error": "not_video", "message": "Only video is converted."}), 400
    try:
        start = max(0.0, float(request.args.get("t") or 0))
    except ValueError:
        start = 0.0
    try:
        source = resolve(item_type, item_id, request.args.get("part"))
    except Refused as e:
        return _refused(e)
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        return jsonify({"success": False, "error": "no_ffmpeg",
                        "message": "SoulSync can't find ffmpeg, so it can't convert this file."}), 503
    p = _plan_for(source, request.args.get("q") or "original", _int_arg("audio") or 0, _int_arg("burn"),
                  _int_arg("delay") or 0)
    origin = source.path if source.mode == "file" else source.url
    cmd = T.command(ffmpeg, origin, p, start, T.encoder(ffmpeg), T.has_filter(ffmpeg, "zscale"))
    try:
        body = T.stream(cmd)
    except T.Busy:
        return jsonify({"success": False, "error": "busy",
                        "message": "The server is already converting as many videos as it can. Try again in a moment."}), 503
    except OSError:
        logger.exception("couldn't start ffmpeg for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't start converting this file."}), 500
    return Response(body, mimetype=T.MIME, direct_passthrough=True,
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@bp.route("/api/player/transcode/<item_type>/<path:item_id>/start", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def transcode_start(item_type, item_id):
    """where a converted stream asked to start at ``?t=`` really starts:
    copying video can only begin at a keyframe, so the player asks first:
    {start} is where the stream begins (its clock), {seek} the ``t`` to
    request so ffmpeg lands exactly there."""
    from core.player import transcode as T
    from core.player.playable import Refused, resolve
    try:
        t = max(0.0, float(request.args.get("t") or 0))
    except ValueError:
        t = 0.0
    try:
        source = resolve(item_type, item_id, request.args.get("part"))
    except Refused as e:
        return _refused(e)
    ffmpeg = T.ffmpeg_path()
    p = _plan_for(source, request.args.get("q") or "original", _int_arg("audio") or 0, _int_arg("burn"),
                  _int_arg("delay") or 0)
    if not ffmpeg or not p.copy_video:
        # converting the video starts frame-exact: no snapping needed
        return jsonify({"success": True, "start": t, "seek": t})
    origin = source.path if source.mode == "file" else source.url
    start, seek = T.seek_point(T.ffprobe_for(ffmpeg), origin, t)
    return jsonify({"success": True, "start": start, "seek": seek})
