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
    GET|PUT /api/player/converting/settings          slots, GPU on/off/auto (admin)
    POST    /api/player/converting/test              find the GPU again, run a
                                                      real clip through it (admin)

``?part=`` picks a video's file version (a media file id) or an audiobook's
part (0 based). item types: track, movie, episode, youtube, home, audiobook.
podcasts don't come here: their episodes stream from the feed's host
through the podcast side's checked audio proxy.
"""

from __future__ import annotations

from urllib.parse import quote

from flask import Blueprint, g, jsonify, make_response, request, send_file

from core.connect.device_access import device_playback
from core.permissions import public, requires_any
from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("player.stream")

bp = Blueprint("player_stream", __name__)

# any section that has something to play; the item's own section is checked in resolve
SECTIONS = ("music.access", "movies.access", "tv.access", "books.access")


def create_blueprint():
    return bp


def _refused(e):
    return jsonify({"success": False, "error": e.code, "message": e.message}), e.status


def _admin_stopped(item_type, item_id):
    """an admin just stopped this profile's screen on this item (Server
    Activity): it isn't served again for a little while. None otherwise"""
    from core.player import presence
    from core.profile_context import get_current_profile_id
    try:
        message = presence.refused(get_current_profile_id(), item_type, item_id)
    except (TypeError, ValueError):
        return None
    if not message:
        return None
    return jsonify({"success": False, "error": "stopped", "message": message}), 403


VIDEO_TYPES = ("movie", "episode", "youtube", "home")


def _transcode_url(item_type: str, item_id: str, part, quality: str) -> str:
    args = [f"q={quote(quality, safe='')}"]
    if part not in (None, ""):
        args.append(f"part={quote(str(part), safe='')}")
    return f"/api/player/transcode/{item_type}/{quote(item_id, safe='')}?" + "&".join(args)


def _video_facts(source) -> dict:
    """the picture's codec, bit depth, profile and bitrate, from the file's
    header (probed once, shared with the tracks route). {} when unknown"""
    try:
        from api.player_tracks import source_tracks
        from core.player import tracks as K
        found = source_tracks(source)
        full = K.cached(found[3]) if found else None
    except Exception:  # noqa: BLE001 - no facts: the codec table alone decides
        logger.debug("couldn't read the picture's facts", exc_info=True)
        return {}
    if not full:
        return {}
    video = (full.get("video") or [{}])[0]
    out = {k: video.get(k) for k in ("codec", "bit_depth", "profile")}
    fmt = full.get("format") or {}
    bits = video.get("bitrate")
    if not bits and fmt.get("bitrate"):
        # mkv rarely says the picture's own bitrate: the file's, less its sound
        bits = fmt["bitrate"] - sum(a.get("bitrate") or 0 for a in full.get("audio") or [])
    if not bits:
        f = (source.extra or {}).get("file") or {}
        size, secs = f.get("size_bytes"), f.get("runtime_seconds") or source.duration
        if size and secs:
            bits = int(size) * 8 / float(secs)
    if bits and bits > 0:
        out["bitrate_kbps"] = int(bits / 1000)
    return {k: v for k, v in out.items() if v is not None}


CAST_COPYABLE = ({"h264"}, {"aac", "mp3"})


def _plan_for(source, quality: str, audio: int = 0, burn=None, delay_ms: int = 0, copyable=None):
    """the plan for this source. a picked audio track other than the first,
    or a subtitle to burn in, reads the file's tracks for the right codec.
    a picture that would be copied is checked first: 10-bit h264 and a
    bitrate over the quality's ceiling are converted."""
    from core.player import transcode as T
    from core.video.direct_play import _norm, stream_verdicts
    info = source.private
    audio_codec = info.get("audio_codec")
    if audio or burn is not None:
        from api.player_tracks import source_tracks
        found = source_tracks(source)
        tracks = found[0]["audio"] if found else []
        if audio < len(tracks):
            audio_codec = tracks[audio]["codec"]
    vcodec = info.get("video_codec")
    copy_candidate = (_norm(vcodec) in copyable[0]) if copyable else stream_verdicts(vcodec, None)["video"] == "yes"
    facts = _video_facts(source) if copy_candidate and burn is None else {}
    return T.plan(vcodec, audio_codec, quality=quality,
                  hdr=info.get("dynamic_range"), height=info.get("height"), audio=audio, burn=burn,
                  delay_ms=delay_ms, codec_facts=facts, copyable=copyable)


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
    stopped = _admin_stopped(item_type, item_id)
    if stopped:
        return stopped
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
    from core.player.hwaccel import enc_for
    out["transcode"] = p.public(enc_for(T.encoder(ffmpeg)).label)


@bp.route("/api/player/stream/<item_type>/<path:item_id>", methods=["GET"])
@requires_any(*SECTIONS)
@device_playback
def stream(item_type, item_id):
    from core.player.playable import Refused, resolve
    from core.player.relay import relay
    stopped = _admin_stopped(item_type, item_id)
    if stopped:
        return stopped
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


_CAST_TOKEN_TTL = 6 * 3600  # 6 hours, scoped to one (profile_id, item_type, item_id)


def _cast_secret() -> bytes:
    import hashlib
    from flask import current_app
    key = current_app.secret_key or "soulsync-cast-fallback"
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hashlib.sha256(b"soulsync:cast-token:v1:" + key).digest()


def _sign_cast_token(profile_id: int, item_type: str, item_id: str, part: str | None) -> str:
    import base64
    import hashlib
    import hmac
    import json
    import time
    payload = json.dumps(
        {
            "p": int(profile_id),
            "t": item_type,
            "i": str(item_id),
            "r": part or "",
            "e": int(time.time()) + _CAST_TOKEN_TTL,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    sig = hmac.new(_cast_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{b64}.{sig}"


def _verify_cast_token(token: str, item_type: str, item_id: str) -> tuple[int, str | None] | None:
    import base64
    import hashlib
    import hmac
    import json
    import time
    if not token or "." not in token:
        return None
    b64, sig = token.rsplit(".", 1)
    expected = hmac.new(_cast_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        pad = "=" * (-len(b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(b64 + pad).decode("utf-8"))
        if int(data.get("e", 0)) < int(time.time()):
            return None
        if data.get("t") != item_type or str(data.get("i")) != str(item_id):
            return None
        pid = int(data["p"])
        part = data.get("r") or None
        return pid, part
    except Exception:  # noqa: BLE001
        return None


def _add_cast_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
    return resp


_CAST_NATIVE_VIDEO_MIMES = {"video/mp4"}
_CAST_PROBE_CACHE: dict[str, tuple[str, str, float | None]] = {}


def _probe_cast_media(source) -> tuple[str, str, float | None]:
    """Return (video_codec, audio_codec, duration_seconds) via ffprobe, cached per file path."""
    if source.mode != "file" or not source.path:
        v = str((source.private or {}).get("video_codec") or (source.verdict or {}).get("video") or "").lower()
        a = str((source.private or {}).get("audio_codec") or (source.verdict or {}).get("audio") or "").lower()
        return v, a, source.duration
    hit = _CAST_PROBE_CACHE.get(source.path)
    if hit is not None:
        return hit
    v_codec, a_codec = "", ""
    dur: float | None = source.duration
    try:
        import json as _json
        import subprocess
        from core.player import transcode as T
        ff = T.ffmpeg_path()
        fp = T.ffprobe_for(ff) if ff else None
        if fp:
            out = subprocess.check_output(
                [
                    fp, "-v", "error",
                    "-show_entries", "format=duration:stream=codec_type,codec_name",
                    "-of", "json",
                    source.path,
                ],
                text=True, errors="replace",
                timeout=8,
            )
            data = _json.loads(out)
            raw_dur = (data.get("format") or {}).get("duration")
            if raw_dur is not None:
                try:
                    d = float(raw_dur)
                    if d > 0:
                        dur = d
                except (TypeError, ValueError):
                    pass
            for st in data.get("streams") or []:
                ctype = str(st.get("codec_type") or "").lower()
                cname = str(st.get("codec_name") or "").lower()
                if ctype == "video" and not v_codec:
                    v_codec = cname
                elif ctype == "audio" and not a_codec:
                    a_codec = cname
    except Exception as exc:  # noqa: BLE001
        logger.debug("ffprobe cast media probe failed for %s: %s", source.path, exc)
    if len(_CAST_PROBE_CACHE) > 500:
        _CAST_PROBE_CACHE.clear()
    _CAST_PROBE_CACHE[source.path] = (v_codec, a_codec, dur)
    return v_codec, a_codec, dur


def _cast_plan(source):
    """what a chromecast gets: h264 (8 bit) with aac or mp3 copied, the rest
    converted, HDR tone mapped, 1080p at most. the same plan() as everyone"""
    v_codec, a_codec, _ = _probe_cast_media(source)
    private = dict(source.private or {})
    private["video_codec"] = private.get("video_codec") or v_codec
    private["audio_codec"] = private.get("audio_codec") or a_codec
    from dataclasses import replace
    return _plan_for(replace(source, private=private), "cast", copyable=CAST_COPYABLE)


def _cast_needs_transcode(item_type: str, source) -> bool:
    if item_type not in VIDEO_TYPES:
        return False
    mime = (source.mime or "").lower()
    if mime not in _CAST_NATIVE_VIDEO_MIMES:
        return True
    # Most Chromecasts only decode 8-bit H.264 + AAC/MP3 reliably in MP4; AV1/HEVC/Opus/Hi10P cause
    # black screen or silence
    return _cast_plan(source).needed


def _cast_lan_http_origin() -> str | None:
    """Plain HTTP LAN origin (http://<lan_ip>:<http_port>) for Chromecast.

    Chromecast's Chromium engine refuses self-signed HTTPS certs (like SoulSync's
    Connect port 8443), so casting on the same LAN uses SoulSync's plain HTTP
    listener (default port 8008).
    """
    try:
        from core.connect.addresses import _http_port
        from core.connect.upnp import primary_lan_ip
        ip = primary_lan_ip()
        if not ip:
            return None
        return f"http://{ip}:{_http_port()}"
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/player/cast-token", methods=["POST"])
@requires_any(*SECTIONS)
@device_playback
def create_cast_token():
    """Mint a short-lived, item-scoped signed stream URL for Chromecast Styled Media Receiver."""
    from core.player.playable import Refused, resolve
    body = request.get_json(silent=True) or {}
    item_type = str(body.get("item_type") or "").strip()
    item_id = str(body.get("item_id") or "").strip()
    part = body.get("part")
    part_str = str(part) if part not in (None, "") else None
    if not item_type or not item_id:
        return jsonify({"success": False, "error": "item_type and item_id required"}), 400
    try:
        source = resolve(item_type, item_id, part_str)
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("cast-token resolve failed for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed"}), 500
    pid = int(getattr(g, "profile_id", 1) or 1)
    ct = _sign_cast_token(pid, item_type, item_id, part_str)
    lan_origin = _cast_lan_http_origin()
    _, _, dur_sec = _probe_cast_media(source)
    needs_tx = _cast_needs_transcode(item_type, source)
    from core.player import transcode as T
    use_hls = bool(needs_tx and dur_sec and dur_sec > 0 and T.ffmpeg_path())
    if use_hls:
        stream_path = f"/api/player/cast-hls/{item_type}/{quote(item_id, safe='')}/index.m3u8?ct={quote(ct, safe='')}"
        content_type = "application/x-mpegURL"
    else:
        stream_path = f"/api/player/cast-stream/{item_type}/{quote(item_id, safe='')}?ct={quote(ct, safe='')}"
        content_type = "video/mp4" if needs_tx else (source.mime or "application/octet-stream")
    return jsonify({
        "success": True,
        "token": ct,
        "stream_path": stream_path,
        "stream_url": f"{lan_origin}{stream_path}" if lan_origin else None,
        "artwork_base_url": lan_origin,
        "content_type": content_type,
        "duration_ms": int(dur_sec * 1000) if dur_sec and dur_sec > 0 else 0,
        "transcoded": needs_tx,
        "hls": use_hls,
        "expires_in": _CAST_TOKEN_TTL,
    })


def _resolve_cast_source(item_type: str, item_id: str):
    """Verify signed cast token, hydrate profile context, and resolve playable source."""
    verified = _verify_cast_token(request.args.get("ct", ""), item_type, item_id)
    if verified is None:
        return None, None, (_add_cast_cors(jsonify({"success": False, "error": "invalid_cast_token"})), 401)
    pid, part = verified
    from core import permission_store
    from database.music_database import get_database
    ctx = permission_store.context_for(get_database(), pid)
    g.profile_id = pid
    g.is_admin = bool(ctx.get("is_admin", False))
    g.perms = ctx.get("perms")
    g.perms_source = ctx.get("source", "none")
    from core.player.playable import Refused, resolve
    try:
        source = resolve(item_type, item_id, part)
    except Refused as e:
        return None, None, (_add_cast_cors(_refused(e)[0]), e.status)
    except Exception:  # noqa: BLE001
        logger.exception("cast source resolve failed for %s %s", item_type, item_id)
        return None, None, (_add_cast_cors(jsonify({"success": False, "error": "failed"})), 500)
    return pid, source, None


@bp.route("/api/player/cast-hls/<item_type>/<path:item_id>/index.m3u8", methods=["GET", "OPTIONS"])
@public
def cast_hls_playlist(item_type, item_id):
    """Serve a deterministic VOD HLS playlist with exact duration for Chromecast."""
    if request.method == "OPTIONS":
        return _add_cast_cors(make_response("", 204))
    pid, source, err = _resolve_cast_source(item_type, item_id)
    if err is not None:
        return err
    _, _, dur_sec = _probe_cast_media(source)
    if not dur_sec or dur_sec <= 0:
        return _add_cast_cors(jsonify({"success": False, "error": "unknown_duration"})), 404
    from core.player.hls_cast import render_vod_playlist
    ct = request.args.get("ct", "")
    body = render_vod_playlist(dur_sec, query_string=f"ct={quote(ct, safe='')}")
    resp = make_response(body, 200)
    resp.headers["Content-Type"] = "application/x-mpegURL"
    resp.headers["Cache-Control"] = "no-store"
    return _add_cast_cors(resp)


@bp.route("/api/player/cast-hls/<item_type>/<path:item_id>/seg/<int:seg_idx>.ts", methods=["GET", "OPTIONS"])
@public
def cast_hls_segment(item_type, item_id, seg_idx):
    """Serve one 6-second MPEG-TS segment from a rolling ffmpeg HLS session."""
    if request.method == "OPTIONS":
        return _add_cast_cors(make_response("", 204))
    if seg_idx < 0:
        return _add_cast_cors(jsonify({"success": False, "error": "bad_segment"})), 400
    pid, source, err = _resolve_cast_source(item_type, item_id)
    if err is not None:
        return err
    from core.player import transcode as T
    from core.player.hls_cast import get_segment_bytes
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        return _add_cast_cors(jsonify({"success": False, "error": "no_ffmpeg"})), 503
    p = _cast_plan(source)
    origin = source.path if source.mode == "file" else source.url
    session_key = f"cast-hls:{pid}:{item_type}:{item_id}"
    data = get_segment_bytes(
        session_key=session_key,
        seg_idx=seg_idx,
        ffmpeg=ffmpeg,
        source_origin=origin,
        plan=p,
        enc=T.encoder(ffmpeg),
        can_tonemap=T.has_filter(ffmpeg, "zscale"),
    )
    if not data:
        return _add_cast_cors(jsonify({"success": False, "error": "segment_unavailable"})), 504
    resp = make_response(data, 200)
    resp.headers["Content-Type"] = "video/mp2t"
    resp.headers["Cache-Control"] = "public, max-age=300"
    return _add_cast_cors(resp)


@bp.route("/api/player/cast-stream/<item_type>/<path:item_id>", methods=["GET", "OPTIONS"])
@public
def cast_stream(item_type, item_id):
    """Stream one media item to a Chromecast receiver using a short-lived signed cast token."""
    if request.method == "OPTIONS":
        return _add_cast_cors(make_response("", 204))
    verified = _verify_cast_token(request.args.get("ct", ""), item_type, item_id)
    if verified is None:
        return _add_cast_cors(jsonify({"success": False, "error": "invalid_cast_token"})), 401
    pid, part = verified
    from core import permission_store
    from database.music_database import get_database
    ctx = permission_store.context_for(get_database(), pid)
    g.profile_id = pid
    g.is_admin = bool(ctx.get("is_admin", False))
    g.perms = ctx.get("perms")
    g.perms_source = ctx.get("source", "none")
    from core.player.playable import Refused, resolve
    from core.player.relay import relay
    try:
        source = resolve(item_type, item_id, part)
    except Refused as e:
        return _add_cast_cors(_refused(e)[0]), e.status
    except Exception:  # noqa: BLE001
        logger.exception("cast-stream failed for %s %s", item_type, item_id)
        return _add_cast_cors(jsonify({"success": False, "error": "failed"})), 500
    if _cast_needs_transcode(item_type, source):
        from flask import Response
        from core.player import transcode as T
        ffmpeg = T.ffmpeg_path()
        if ffmpeg:
            try:
                start_sec = max(0.0, float(request.args.get("t") or 0))
            except ValueError:
                start_sec = 0.0
            p = _cast_plan(source)
            origin = source.path if source.mode == "file" else source.url
            job = T.build(ffmpeg, origin, p, start_sec, T.encoder(ffmpeg), T.has_filter(ffmpeg, "zscale"))
            try:
                body = T.stream(job, owner=f"cast-{pid}")
            except T.Busy:
                # the raw file is what the cast couldn't play in the first place
                return _add_cast_cors(jsonify({"success": False, "error": "busy",
                                               "message": "The server is converting as many videos as it can."})), 503
            except OSError:
                logger.exception("cast transcode couldn't start for %s %s", item_type, item_id)
                return _add_cast_cors(jsonify({"success": False, "error": "failed"})), 500
            resp = Response(
                body,
                mimetype=T.MIME,
                direct_passthrough=True,
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )
            return _add_cast_cors(resp)
    if source.mode == "file":
        try:
            resp = send_file(source.path, mimetype=source.mime, conditional=True, as_attachment=False)
            return _add_cast_cors(resp)
        except OSError:
            return _add_cast_cors(jsonify({"success": False, "error": "no_file"})), 404
    if source.mode == "relay":
        resp = relay(source.url, request.headers.get("Range"), source.mime)
        return _add_cast_cors(resp)
    return _add_cast_cors(jsonify({"success": False, "error": "external"})), 400


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
    stopped = _admin_stopped(item_type, item_id)
    if stopped:
        return stopped
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
    job = T.build(ffmpeg, origin, p, start, T.encoder(ffmpeg), T.has_filter(ffmpeg, "zscale"))
    try:
        body = T.stream(job, owner=_screen())
    except T.Busy:
        return jsonify({"success": False, "error": "busy",
                        "message": "The server is already converting as many videos as it can. Try again in a moment."}), 503
    except OSError:
        logger.exception("couldn't start ffmpeg for %s %s", item_type, item_id)
        return jsonify({"success": False, "error": "failed", "message": "Couldn't start converting this file."}), 500
    return Response(body, mimetype=T.MIME, direct_passthrough=True,
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _screen():
    """which screen is asking: this profile's player tab (?ps=). its new
    conversion stops its last one"""
    import re
    ps = request.args.get("ps") or ""
    if not re.fullmatch(r"[A-Za-z0-9-]{6,64}", ps):
        return None
    from core.profile_context import get_current_profile_id
    return f"{get_current_profile_id()}:{ps}"


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


# ── converting: the admin's settings and the GPU test ──────────────────

def _converting_status(**more):
    from core.player import hwaccel
    from core.player import transcode as T
    out = hwaccel.status(T.ffmpeg_path())
    out["in_use"] = getattr(T._slots, "in_use", None)
    return jsonify({"success": True, **out, **more})


@bp.route("/api/player/converting/settings", methods=["GET"])
@admin_only
def converting_settings():
    """slots, GPU mode and device, what's in use, what was tried and why
    anything is off"""
    return _converting_status()


@bp.route("/api/player/converting/settings", methods=["PUT"])
@admin_only
def converting_settings_put():
    from core.player import hwaccel
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify({"success": False, "error": "bad_settings", "message": "expected one or more settings"}), 400
    try:
        hwaccel.save_settings({k: body[k] for k in ("slots", "gpu", "device") if k in body})
    except ValueError as e:
        return jsonify({"success": False, "error": "bad_settings", "message": str(e)}), 400
    return _converting_status()


@bp.route("/api/player/converting/test", methods=["POST"])
@admin_only
def converting_test():
    """find the GPU again (anything marked broken gets another go) and run
    the whole pipeline once on a real clip: decode, scale, encode"""
    from core.player import hwaccel
    from core.player import transcode as T
    ffmpeg = T.ffmpeg_path()
    if not ffmpeg:
        return jsonify({"success": False, "error": "no_ffmpeg",
                        "message": "SoulSync can't find ffmpeg, so it can't convert anything."}), 503
    hwaccel.forget()
    enc = hwaccel.detect(ffmpeg)
    try:
        result = hwaccel.full_test(ffmpeg, enc)
        if not result["ok"] and result.get("hw_decode"):
            # decoding on the card failed: decode on the CPU, encode on the card
            hwaccel.mark_failed(ffmpeg, enc, ("h264", 8), result.get("reason") or "")
            again = hwaccel.full_test(ffmpeg, enc)
            if again["ok"]:
                result = {**again, "note": "Decoding on the CPU: " + (result.get("reason") or "")}
        if not result["ok"] and enc.key != "cpu":
            # the one-frame test passed but the real pipeline didn't: off until tested again
            reason = result.get("reason") or ""
            hwaccel.mark_failed(ffmpeg, enc, None, reason, tester=lambda *_: (False, reason))
            hwaccel.detect(ffmpeg)
    except Exception as e:  # noqa: BLE001 - a test that can't run says so
        logger.exception("converting test failed to run")
        result = {"ok": False, "reason": str(e)[:200]}
    result["label"] = enc.label
    return _converting_status(test=result)
