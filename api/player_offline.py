"""player downloads: what a title, season, album or playlist is as files,
getting them ready, and each profile's devices and what's on them
(scrap/DOWNLOADS_CONTRACT.md).

    GET    /api/player/offline/plan                      qualities and sizes for a group
    POST   /api/player/offline/prepare                   get an item ready (original or a copy)
    GET    /api/player/offline/prepare/<id>              how it's going
    GET    /api/player/offline/file/<id>                 the file (Range, ETag, a clean name)
    POST   /api/player/offline/devices/report            a device's whole truth
    GET    /api/player/offline/devices                   this profile's devices and their items
    POST   /api/player/offline/wishes                    add to / remove from a device
    POST   /api/player/offline/rules                     a smart rule for a device
    DELETE /api/player/offline/rules/<rule_id>
    GET    /api/player/offline/devices/<id>/wanted       what a device should add and remove
    POST   /api/player/offline/sync                      plays, positions and watched marks made offline
    GET    /api/player/offline/settings                  the admin's three settings (PUT: admin)

every route is the caller's profile only: a device id is looked up under
that profile, so one profile never sees or changes another's devices. the
admin's per-profile switch (player_download_permissions) stops a member
getting anything new; an admin always may. never a path from a client.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request, send_file

from core.connect.device_access import device_playback
from core.permissions import self_only
from core.player import offline_catalog as C
from core.player import offline_jobs as J
from core.player import offline_store as S
from core.player import offline_wanted as W
from core.player.playable import Refused
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("player.offline")

bp = Blueprint("player_offline", __name__)

RULE_TYPES = ("show", "playlist", "podcast")
KEEP_NEXT = (1, 2, 3, 5, 10)
MAX_EVENTS = 2000
MIME = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp3": "audio/mpeg"}


def create_blueprint():
    return bp


def player_db():
    from database.player_database import get_player_database
    return get_player_database()


def _profile():
    pid = get_current_profile_id()
    return int(pid) if pid is not None else None


def _is_admin() -> bool:
    """browser admins and paired devices on an admin profile (api/player.py)"""
    return bool(getattr(g, "is_admin", False) or getattr(g, "perms_source", None) == "admin")


def _err(status, code, message):
    return jsonify({"success": False, "error": code, "message": message}), status


def _refused(e: Refused):
    return _err(e.status, e.code, e.message)


def _signed_out():
    return _err(401, "no_profile", "Pick a profile first.")


def _disabled():
    return _err(403, "downloads_disabled", "Downloads have been switched off for your profile by an admin.")


def _may_download(pid: int) -> bool:
    return _is_admin() or S.downloads_allowed(player_db(), pid)


def _settings() -> dict:
    return J._settings(player_db())


def _may_convert() -> bool:
    return _is_admin() or _settings()["allow_conversion_for_members"]


def _iso(epoch) -> str | None:
    if not epoch:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))


def _epoch(text) -> float | None:
    try:
        value = str(text or "").strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _device_or_404(pid: int, device_id):
    try:
        dev_id = S.clean_device_id(device_id)
    except S.ReportError as e:
        return None, _err(400, "bad_device", str(e))
    dev = S.device(player_db(), pid, dev_id)
    if dev is None:
        return None, _err(404, "no_device", "That device hasn't checked in on this profile.")
    return dev, None


# ── plan ─────────────────────────────────────────────────────────────────

@bp.route("/api/player/offline/plan", methods=["GET"])
@device_playback
@self_only
def offline_plan():
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_download(pid):
        return _disabled()
    gtype = (request.args.get("type") or "").strip().lower()
    gid = (request.args.get("id") or "").strip()
    try:
        count = max(1, min(C.PODCAST_MAX, int(request.args.get("count") or C.PODCAST_DEFAULT)))
    except ValueError:
        count = C.PODCAST_DEFAULT
    try:
        header, items = C.expand(gtype, gid, count=count)
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("offline plan failed for %s %s", gtype, gid)
        return _err(500, "failed", "Couldn't work out that download.")
    db = player_db()
    dev_items, dev, rule = {}, None, None
    device_id = request.args.get("device")
    if device_id:
        dev = S.device(db, pid, device_id)
        if dev is not None:
            dev_items = {C.key(i["item_type"], i["item_id"]): i for i in S.items(db, pid, device_id)}
            if gtype in RULE_TYPES:
                rule = next((S.rule_public(r) for r in S.rules(db, pid, device_id)
                             if r["type"] == gtype and r["target_id"] == gid), None)
    state = W.watch_state(db, pid, [(i["item_type"], i["item_id"]) for i in items])
    kind = C.kind_of(C.GROUP_ITEM[gtype])
    may_convert = _may_convert()
    ids = C.offered(kind, items) if may_convert else ["original"]
    out_items = []
    for i in items:
        k = C.key(i["item_type"], i["item_id"])
        held = dev_items.get(k)
        watched, pos = state.get(k, (False, 0.0))
        out_items.append({**{f: i.get(f) for f in ("item_type", "item_id", "title", "subtitle", "artwork",
                                                    "duration_s", "size_bytes", "source")},
                          "on_device": bool(held and held["state"] == "downloaded"),
                          "device_state": held["state"] if held else None,
                          "watched": watched, "position_s": pos})
    return jsonify({"success": True, "kind": kind, "group": {"type": gtype, "id": gid}, **header,
                    "items": out_items, "qualities": C.qualities(kind, items, ids),
                    "conversion_off": not may_convert, "rule": rule,
                    "free_bytes": dev.get("free_bytes") if dev else None,
                    "limit_bytes": dev.get("limit_bytes") if dev else None})


# ── prepare and the file ─────────────────────────────────────────────────

@bp.route("/api/player/offline/prepare", methods=["POST"])
@device_playback
@self_only
def offline_prepare():
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_download(pid):
        return _disabled()
    body = request.get_json(silent=True) or {}
    item_type = str(body.get("item_type") or "").strip().lower()
    item_id = str(body.get("item_id") or "").strip()
    quality = str(body.get("quality") or "original").strip().lower()
    if not item_id or len(item_id) > 400:
        return _err(400, "bad_id", "That isn't an item id.")
    try:
        job = J.prepare(player_db(), item_type, item_id, quality, may_convert=_may_convert())
    except Refused as e:
        return _refused(e)
    except J.ConversionOff:
        return _err(403, "conversion_off", "Smaller copies are switched off. Download the original.")
    except Exception:  # noqa: BLE001
        logger.exception("offline prepare failed for %s %s", item_type, item_id)
        return _err(500, "failed", "Couldn't get that ready.")
    return jsonify({"success": True, **job})


@bp.route("/api/player/offline/prepare/<prep_id>", methods=["GET"])
@device_playback
@self_only
def offline_prepare_status(prep_id):
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_download(pid):
        return _disabled()
    db = player_db()
    row = S.prepared(db, prep_id)
    if row is None:
        return _err(404, "not_found", "Nothing is being got ready under that id. Ask again.")
    try:
        _authorize(row)
        job = J.status(db, prep_id)
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("offline status failed for %s", prep_id)
        return _err(500, "failed", "Couldn't check on that.")
    return jsonify({"success": True, **job})


def _authorize(row):
    """the caller may play the item this copy is of (raises Refused). the
    source, for an original served straight from it"""
    if row["item_type"] == "podcast_episode":
        C.podcast_episode(row["item_id"])
        return None
    from core.player.playable import resolve
    return resolve(row["item_type"], row["item_id"])


@bp.route("/api/player/offline/file/<prep_id>", methods=["GET"])
@device_playback
@self_only
def offline_file(prep_id):
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_download(pid):
        return _disabled()
    db = player_db()
    row = S.prepared(db, prep_id)
    if row is None:
        return _err(404, "not_found", "That download isn't ready. Ask again.")
    if row["status"] != "ready":
        return _err(409, "not_ready", "That isn't ready yet.")
    try:
        source = _authorize(row)
    except Refused as e:
        return _refused(e)
    except Exception:  # noqa: BLE001
        logger.exception("offline file check failed for %s", prep_id)
        return _err(500, "failed", "Couldn't open that.")
    J.touch(db, prep_id)
    name = row.get("filename") or "download"
    import os
    ext = os.path.splitext(name)[1].lower()
    try:
        if row.get("path"):
            return send_file(row["path"], mimetype=MIME.get(ext, "application/octet-stream"), conditional=True,
                             etag=True, as_attachment=True, download_name=name, max_age=0)
        if source is not None and source.mode == "file":
            return send_file(source.path, mimetype=source.mime, conditional=True, etag=True,
                             as_attachment=True, download_name=name, max_age=0)
    except OSError:
        logger.exception("offline file couldn't open %s", prep_id)
        return _err(404, "no_file", "That file couldn't be opened.")
    if source is not None and source.mode == "relay":
        from urllib.parse import quote

        from core.player.relay import relay
        out = relay(source.url, request.headers.get("Range"), source.mime)
        if not isinstance(out, tuple):
            out.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
        return out
    return _err(404, "no_file", "That file couldn't be opened.")


# ── devices ──────────────────────────────────────────────────────────────

@bp.route("/api/player/offline/devices/report", methods=["POST"])
@device_playback
@self_only
def offline_report():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True)
    paired = None
    if g.get("device_auth") and g.get("device"):
        paired = g.device.get("id")
    db = player_db()
    try:
        out = S.report(db, pid, body, paired_id=paired, describe=C.describe)
    except S.NotYourDevice:
        return _err(403, "not_your_device", "That device id belongs to another paired device.")
    except S.ReportError as e:
        return _err(400, "bad_report", str(e))
    except Exception:  # noqa: BLE001
        logger.exception("offline report failed")
        return _err(500, "failed", "Couldn't save that.")
    _mirror_log(db, pid, out, body)
    return jsonify({"success": True, "device_id": out["device_id"], "items": len(out["items"])})


def _mirror_log(db, pid, out, body):
    """keep the older downloads log (the admin's Users & Downloads) in step"""
    from core.player import downloads
    try:
        dev_id = out["device_id"]
        name = str((body or {}).get("device_name") or "Device")
        rows = {C.key(i["item_type"], i["item_id"]): i for i in S.items(db, pid, dev_id)}
        for t, i in out["added"]:
            r = rows.get(C.key(t, i)) or {}
            downloads.log_download(db, pid, item_type="podcast" if t == "podcast_episode" else t, item_id=i,
                                   title=r.get("title") or i, subtitle=r.get("subtitle"), artwork=r.get("artwork"),
                                   device_id=dev_id, device_name=name, quality=r.get("quality") or "original",
                                   size_bytes=r.get("size_bytes") or 0)
        for t, i in out["gone"]:
            downloads.mark_removed(db, pid, item_type="podcast" if t == "podcast_episode" else t, item_id=i,
                                   device_id=dev_id)
    except Exception:  # noqa: BLE001 - the old log is a mirror, never a failed report
        logger.warning("offline: keeping the downloads log in step failed", exc_info=True)


def _wish_public(w):
    return {"wish_id": int(w["id"]), "action": w["action"], "type": w["type"], "id": w["target_id"],
            "quality": w["quality"], "title": w.get("title"), "subtitle": w.get("subtitle"),
            "artwork": w.get("artwork")}


def _device_public(db, pid, dev):
    items = S.items(db, pid, dev["device_id"])
    state = W.watch_state(db, pid, [(i["item_type"], i["item_id"]) for i in items])
    out_items = []
    for i in items:
        watched, pos = state.get(C.key(i["item_type"], i["item_id"]), (False, 0.0))
        out_items.append({"item_type": i["item_type"], "item_id": i["item_id"], "quality": i["quality"],
                          "size_bytes": i["size_bytes"], "state": i["state"], "progress": i["progress"],
                          "title": i.get("title") or i["item_id"], "subtitle": i.get("subtitle"),
                          "artwork": i.get("artwork"), "duration_s": i.get("duration_s"),
                          "group": i.get("group"), "kind": C.kind_of(i["item_type"]),
                          "watched": watched, "position_s": pos})
    _regroup_playlists(db, pid, dev["device_id"], out_items)
    return {"device_id": dev["device_id"], "device_name": dev["device_name"], "platform": dev["platform"],
            "last_seen": _iso(dev["last_seen"]), "free_bytes": dev["free_bytes"],
            "limit_bytes": dev["limit_bytes"], "used_bytes": dev["used_bytes"],
            "wifi_only": bool(dev["wifi_only"]), "items": out_items,
            "rules": [S.rule_public(r) for r in S.rules(db, pid, dev["device_id"])],
            "wishes": [_wish_public(w) for w in S.wishes(db, pid, dev["device_id"], pending=True)]}


def _regroup_playlists(db, pid, device_id, items):
    """a song a playlist rule or wish on this device holds shows under it"""
    lists = [(r["target_id"], r.get("title"), r.get("artwork"), r.get("members_json"))
             for r in S.rules(db, pid, device_id) if r["type"] == "playlist"]
    if not lists:
        return
    import json
    for target, title, art, members in lists:
        try:
            ids = {str(x) for x in json.loads(members or "[]")}
        except (TypeError, ValueError):
            continue
        group = {"type": "playlist", "id": target, "title": title or "Playlist", "subtitle": "Playlist",
                 "artwork": art}
        for i in items:
            if i["item_type"] == "track" and i["item_id"] in ids:
                i["group"] = group


@bp.route("/api/player/offline/devices", methods=["GET"])
@device_playback
@self_only
def offline_devices():
    pid = _profile()
    if pid is None:
        return _signed_out()
    db = player_db()
    try:
        out = [_device_public(db, pid, d) for d in S.devices(db, pid)]
    except Exception:  # noqa: BLE001
        logger.exception("offline devices failed")
        return _err(500, "failed", "Couldn't load your devices.")
    return jsonify({"success": True, "devices": out})


# ── wishes and rules ─────────────────────────────────────────────────────

def _target(body):
    """(type, id, quality) checked, or raises Refused"""
    ttype = str(body.get("type") or "").strip().lower()
    tid = str(body.get("id") or "").strip()
    quality = str(body.get("quality") or "original").strip().lower()
    if ttype not in C.GROUP_TYPES and ttype != "podcast_episode":
        raise Refused(400, "bad_type", "That can't be downloaded.")
    if not tid or len(tid) > 400:
        raise Refused(400, "bad_id", "That isn't an id.")
    item_kind = C.GROUP_ITEM.get(ttype, ttype)
    if not C.valid_quality(item_kind, quality):
        raise Refused(400, "bad_quality", "That isn't one of the qualities.")
    return ttype, tid, quality


def _meta(ttype, tid):
    if ttype == "podcast_episode":
        ep = C.podcast_episode(tid)
        return {"title": ep["title"], "subtitle": ep["subtitle"], "artwork": ep["artwork"]}
    header, _items = C.expand(ttype, tid)
    return header


@bp.route("/api/player/offline/wishes", methods=["POST"])
@device_playback
@self_only
def offline_wishes():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    if action not in ("add", "remove"):
        return _err(400, "bad_action", "action is add or remove")
    if action == "add" and not _may_download(pid):
        return _disabled()
    dev, bad = _device_or_404(pid, body.get("device_id"))
    if bad:
        return bad
    try:
        ttype, tid, quality = _target(body)
        if action == "add":
            if quality != "original" and not _may_convert():
                return _err(403, "conversion_off", "Smaller copies are switched off. Pick the original.")
            meta = _meta(ttype, tid)
        else:
            try:
                meta = _meta(ttype, tid)
            except Refused:
                meta = {}
    except Refused as e:
        return _refused(e)
    db = player_db()
    S.wish(db, pid, dev["device_id"], action, ttype, tid, quality, meta)
    return jsonify({"success": True, "wishes": [_wish_public(w) for w in S.wishes(db, pid, dev["device_id"], pending=True)]})


@bp.route("/api/player/offline/rules", methods=["POST"])
@device_playback
@self_only
def offline_rule_put():
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_download(pid):
        return _disabled()
    body = request.get_json(silent=True) or {}
    dev, bad = _device_or_404(pid, body.get("device_id"))
    if bad:
        return bad
    rtype = str(body.get("type") or "").strip().lower()
    if rtype not in RULE_TYPES:
        return _err(400, "bad_type", "A smart rule is for a show, a playlist or a podcast.")
    keep = body.get("keep_next", 3)
    if isinstance(keep, bool) or not isinstance(keep, int) or keep not in KEEP_NEXT:
        return _err(400, "bad_keep_next", "keep_next is 1, 3 or 5")
    for flag in ("delete_watched", "follow"):
        if flag in body and not isinstance(body[flag], bool):
            return _err(400, f"bad_{flag}", f"{flag} is true or false")
    try:
        _t, tid, quality = _target({**body, "type": rtype})
        if quality != "original" and not _may_convert():
            return _err(403, "conversion_off", "Smaller copies are switched off. Pick the original.")
        meta = _meta(rtype, tid)
    except Refused as e:
        return _refused(e)
    row = S.put_rule(player_db(), pid, dev["device_id"], rtype, tid, quality, keep,
                     bool(body.get("delete_watched", False)), bool(body.get("follow", True)), meta)
    return jsonify({"success": True, "rule": S.rule_public(row)})


@bp.route("/api/player/offline/rules/<int:rule_id>", methods=["DELETE"])
@device_playback
@self_only
def offline_rule_delete(rule_id):
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not S.delete_rule(player_db(), pid, rule_id):
        return _err(404, "not_found", "No such smart rule.")
    return jsonify({"success": True, "removed": True})


@bp.route("/api/player/offline/devices/<device_id>/wanted", methods=["GET"])
@device_playback
@self_only
def offline_wanted(device_id):
    pid = _profile()
    if pid is None:
        return _signed_out()
    dev, bad = _device_or_404(pid, device_id)
    if bad:
        return bad
    try:
        out = W.wanted(player_db(), pid, dev["device_id"], allowed=_may_download(pid))
    except Exception:  # noqa: BLE001
        logger.exception("offline wanted failed")
        return _err(500, "failed", "Couldn't work out what that device needs.")
    return jsonify({"success": True, **out})


# ── what happened offline ────────────────────────────────────────────────

def _progress_row(db, pid, item_type, item_id):
    rows = S._read(db, "SELECT updated_at FROM watch_progress WHERE profile_id = ? AND item_type = ? "
                       "AND item_id = ?", (pid, item_type, item_id))
    return float(rows[0][0]) if rows else None


def _record_track_play(pid, item_id, at_epoch, duration_s):
    from core.playback.record_play import record_play
    from database.music_database import get_database
    meta = C.describe([("track", item_id)]).get(C.key("track", item_id)) or {}
    if not meta:
        return
    artist, _, album = str(meta.get("subtitle") or "").partition(" · ")
    record_play(get_database(), pid, {"id": item_id, "lib2_track_id": int(item_id), "title": meta.get("title"),
                                      "artist": artist, "album": album},
                played_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(at_epoch)),
                duration_ms=int((duration_s or meta.get("duration_s") or 0) * 1000), eligible=True)


@bp.route("/api/player/offline/sync", methods=["POST"])
@device_playback
@self_only
def offline_sync():
    from core.player import progress
    from core.player import watched as watched_mod
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        dev_id = S.clean_device_id(body.get("device_id"))
    except S.ReportError as e:
        return _err(400, "bad_device", str(e))
    events = body.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        return _err(400, "bad_events", f"events is a list of at most {MAX_EVENTS}")
    db = player_db()
    applied = skipped = plays = 0
    for ev in events:
        if not isinstance(ev, dict):
            skipped += 1
            continue
        kind = str(ev.get("kind") or "").lower()
        itype = str(ev.get("item_type") or "").lower()
        raw_id = str(ev.get("item_id") or "").strip()
        at = _epoch(ev.get("at"))
        if kind not in ("progress", "watched", "play") or itype not in C.ITEM_KINDS or not raw_id or at is None:
            skipped += 1
            continue
        at = min(at, time.time())
        iid = W._progress_id(itype, raw_id)
        try:
            if kind == "play":
                if S.play_once(db, pid, dev_id, itype, raw_id, _iso(at)):
                    plays += 1
                    if itype == "track" and raw_id.isdigit():
                        _record_track_play(pid, raw_id, at, ev.get("duration_s"))
                else:
                    skipped += 1
                continue
            known = _progress_row(db, pid, itype, iid)
            if known is not None and known >= at:
                skipped += 1
                continue
            if kind == "progress":
                progress.save(db, pid, itype, iid, ev.get("position_s") or 0, ev.get("duration_s"), now=at)
            else:
                progress.finish(db, pid, itype, iid, ev.get("duration_s"), now=at)
                watched_mod.mark(itype, iid)
            applied += 1
        except progress.ProgressError:
            skipped += 1
        except Exception:  # noqa: BLE001 - one bad event never loses the rest
            logger.warning("offline sync event failed", exc_info=True)
            skipped += 1
    return jsonify({"success": True, "applied": applied, "skipped": skipped, "plays": plays})


# ── the admin's settings ─────────────────────────────────────────────────

@bp.route("/api/player/offline/settings", methods=["GET"])
@device_playback
@self_only
def offline_settings_get():
    pid = _profile()
    if pid is None:
        return _signed_out()
    db = player_db()
    return jsonify({"success": True, "settings": _settings(), "is_admin": _is_admin(),
                    "allow_downloads": _may_download(pid), "can_convert": _may_convert(),
                    "cache_used_bytes": J.cache_used(db)})


@bp.route("/api/player/offline/settings", methods=["PUT"])
@device_playback
@self_only
def offline_settings_put():
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _is_admin():
        return _err(403, "forbidden", "Only an admin can change that.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return _err(400, "bad_settings", "expected one or more settings")
    try:
        S.put_settings(player_db(), body)
    except ValueError as e:
        return _err(400, "bad_settings", str(e))
    db = player_db()
    J.pump(db)
    J.evict(db)
    return jsonify({"success": True, "settings": _settings()})
