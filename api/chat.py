"""Soulseek chat API — rooms + private messages, proxied through slskd.

Side-neutral (the Chat page is mounted in BOTH the music and video sidebars),
so paths are absolute /api/chat/* and the blueprint registers with no prefix —
deliberately NOT under /api/video, whose permission gate would 403 music-only
profiles.

Permission model: any signed-in profile can READ (the browser never sees the
slskd API key — everything proxies through here). SENDING speaks as the one
shared Soulseek account, so it's admin-only unless the admin opts members in
via ``soulseek.chat_member_send``.

The community room (``soulseek.chat_room``, default 'SoulSync' — Soulseek room
names are CASE-SENSITIVE and that's the real community room) is auto-joined
on demand: slskd room joins don't survive its restarts, so the room hydrate
re-joins whenever slskd reports us absent — idempotent, one extra call only
when actually needed.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("chat.api")

_MAX_MESSAGE_LEN = 1000
_INGEST_AT: dict = {}      # room -> last full-buffer archive ingest (epoch)
_SELF = {"name": "", "at": 0.0}   # our slskd username, cached (network call)


def _self_username(client) -> str:
    import time as _time
    now = _time.time()
    if _SELF["name"] and now - _SELF["at"] < 300:
        return _SELF["name"]
    try:
        info = _run_async(client.get_session_info()) or {}
        name = str(info.get("username") or "")
        if name:
            _SELF.update(name=name, at=now)
        return name
    except Exception:
        return _SELF["name"]

# Host-injected callables (configure() below) — avoids circular imports with
# web_server, same pattern as core/enrichment/api.py.
_client_getter = None      # () -> SoulseekClient | None (configured or None)
_run_async = None          # coroutine -> result (the shared slskd event loop)
_config_get = None         # (key, default) -> value
_config_set = None         # (key, value) -> None
_db_getter = None          # () -> MusicDatabase (the chat archive lives there)


def configure(*, client_getter, run_async, config_get, config_set=None,
              db_getter=None) -> None:
    global _client_getter, _run_async, _config_get, _config_set, _db_getter
    _client_getter = client_getter
    _run_async = run_async
    _config_get = config_get
    _config_set = config_set
    _db_getter = db_getter


def _db():
    try:
        return _db_getter() if _db_getter else None
    except Exception:
        return None


def _client():
    try:
        c = _client_getter() if _client_getter else None
    except Exception:
        return None
    return c if (c is not None and getattr(c, "base_url", None)) else None


def _room_name() -> str:
    try:
        return str(_config_get("soulseek.chat_room", "SoulSync") or "SoulSync")
    except Exception:
        return "SoulSync"


def _can_send() -> bool:
    if bool(getattr(g, "is_admin", True)):
        return True
    try:
        return bool(_config_get("soulseek.chat_member_send", False))
    except Exception:
        return False


def _clean_message(payload) -> str | None:
    msg = str((payload or {}).get("message") or "").strip()
    if not msg:
        return None
    return msg[:_MAX_MESSAGE_LEN]


def _ensure_joined(client, room: str) -> bool:
    """True when slskd is in ``room`` (joining now if needed)."""
    joined = _run_async(client.get_joined_rooms())
    if room in (joined or []):
        return True
    ok = _run_async(client.join_room(room))
    if not ok:
        logger.warning("chat: could not join room %r", room)
    return bool(ok)


def _unwrap_room_messages(messages) -> list:
    """Decode SoulSync envelopes in a room message list. Envelope messages get
    their text swapped for the payload + rich=True (the page renders those with
    the markdown subset); everything else passes through untouched and renders
    as escaped plaintext like always."""
    from core import chat_codec
    out = []
    for m in (messages or []):
        m = dict(m)
        dec = chat_codec.decode(m.get("message"))
        if dec is not None:
            m["message"] = dec["t"]
            m["rich"] = True
            r = chat_codec.reply_of(dec)
            if r:
                m["reply"] = r
        out.append(m)
    return out


def _gif_fetch(url: str, params: dict) -> dict:
    """One seam for the Tenor HTTP call (monkeypatched in tests)."""
    import requests
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def create_blueprint() -> Blueprint:
    bp = Blueprint("chat_api", __name__)

    @bp.route("/api/chat/settings", methods=["GET"])
    def chat_settings_get():
        """The chat settings for the cog modal (admin-only — these are
        server-wide). The GIPHY key is never echoed back, only whether one
        is configured."""
        if not bool(getattr(g, "is_admin", True)):
            return jsonify({"error": "Admin access required"}), 403
        def _cfg(key, default):
            try:
                return _config_get(key, default)
            except Exception:
                return default
        return jsonify({
            "room": str(_cfg("soulseek.chat_room", "SoulSync") or "SoulSync"),
            "member_send": bool(_cfg("soulseek.chat_member_send", False)),
            "auto_join": bool(_cfg("soulseek.chat_auto_join", True)),
            "auto_prove": bool(_cfg("soulseek.chat_auto_prove", True)),
            "giphy_key_set": bool(_cfg("soulseek.chat_giphy_key", "")),
        })

    @bp.route("/api/chat/settings", methods=["POST"])
    def chat_settings_set():
        if not bool(getattr(g, "is_admin", True)):
            return jsonify({"error": "Admin access required"}), 403
        if _config_set is None:
            return jsonify({"error": "settings backend not wired"}), 500
        body = request.get_json(silent=True) or {}
        old_room = _room_name()
        try:
            old_auto_join = bool(_config_get("soulseek.chat_auto_join", True))
        except Exception:
            old_auto_join = True
        if "room" in body:
            room = str(body.get("room") or "").strip()[:64]
            _config_set("soulseek.chat_room", room or "SoulSync")
        for key, cfg in (("member_send", "soulseek.chat_member_send"),
                         ("auto_join", "soulseek.chat_auto_join"),
                         ("auto_prove", "soulseek.chat_auto_prove")):
            if key in body:
                _config_set(cfg, bool(body.get(key)))
        if "giphy_key" in body:
            # present = intentional: a value sets it, empty string clears it
            _config_set("soulseek.chat_giphy_key", str(body.get("giphy_key") or "").strip())
        # Renaming the room: walk slskd out of the old one, best-effort —
        # otherwise the account sits in both forever. Same for turning
        # auto-join OFF: an opt-out that leaves you sitting in the room until
        # slskd restarts isn't an opt-out (the page can still join on open).
        new_room = _room_name()
        leave = []
        if new_room != old_room:
            leave.append(old_room)
        if old_auto_join and "auto_join" in body and not bool(body.get("auto_join")):
            leave.append(new_room)
        if leave:
            client = _client()
            if client is not None:
                for r in leave:
                    try:
                        _run_async(client.leave_room(r))
                    except Exception:
                        logger.debug("chat: could not leave room %r", r, exc_info=True)
        return chat_settings_get()

    @bp.route("/api/chat/gifs", methods=["GET"])
    def chat_gifs():
        """GIF search (GIPHY — Tenor's API was shut down June 2026), proxied so
        the API key never reaches the browser. Key: ``soulseek.chat_giphy_key``
        (free at developers.giphy.com). Sending a picked GIF is just sending
        its URL — the renderer auto-embeds trusted GIF CDNs."""
        try:
            key = str(_config_get("soulseek.chat_giphy_key", "") or "")
        except Exception:
            key = ""
        if not key:
            return jsonify({"error": "No GIPHY API key — add soulseek.chat_giphy_key "
                                     "(free at developers.giphy.com) to enable GIF search"}), 503
        q = str(request.args.get("q") or "").strip()[:100]
        if not q:
            return jsonify({"error": "empty query"}), 400
        try:
            data = _gif_fetch("https://api.giphy.com/v1/gifs/search", {
                "q": q, "api_key": key, "limit": 24, "rating": "pg-13",
            })
        except Exception as e:
            logger.exception("chat: gif search failed")
            return jsonify({"error": str(e)}), 502
        gifs = []
        for res in (data.get("data") or []):
            imgs = res.get("images") or {}
            full = (imgs.get("original") or {}).get("url")
            tiny = ((imgs.get("fixed_width_small") or {}).get("url")
                    or (imgs.get("preview_gif") or {}).get("url") or full)
            if full:
                gifs.append({"url": full, "preview": tiny})
        return jsonify({"gifs": gifs})

    @bp.route("/api/chat/status", methods=["GET"])
    def chat_status():
        """Cheap page hydrate: is chat usable, which room, may I send."""
        client = _client()
        return jsonify({
            "configured": client is not None,
            "room": _room_name(),
            "can_send": _can_send(),
            "is_admin": bool(getattr(g, "is_admin", True)),   # shows the settings cog
            # our slskd account name — the page needs it for @mention highlights
            "username": _self_username(client) if client is not None else "",
        })

    @bp.route("/api/chat/room", methods=["GET"])
    def chat_room():
        """The community room: ensure joined, then messages + user list."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        room = _room_name()
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room '%s' — is slskd connected "
                                         "to the Soulseek network?" % room}), 502
            messages = _run_async(client.get_room_messages(room))
            users = _run_async(client.get_room_users(room))
        except Exception as e:
            logger.exception("chat: room hydrate failed")
            return jsonify({"error": str(e)}), 502
        live = _unwrap_room_messages(messages)
        # Archive-first (chatbic P2): slskd forgets the room on restart, the
        # archive doesn't. Top it up from the live buffer (idempotent), then
        # serve the archive tail; live is only the fallback when the archive
        # is unavailable. The top-up is THROTTLED — the page polls this every
        # 4s and re-ingesting the whole slskd buffer each tick is the request
        # flood all over again; the push loop archives the deltas in between.
        db = _db()
        out = live
        if db is not None:
            try:
                import time as _time
                now = _time.time()
                if now - _INGEST_AT.get(room, 0) > 60:
                    _INGEST_AT[room] = now
                    db.add_chat_messages(room, live)
                arch = db.get_chat_messages(room, limit=100)
                if arch:
                    out = arch
            except Exception:
                logger.debug("chat: archive unavailable, serving live buffer", exc_info=True)
        return jsonify({"room": room, "messages": out,
                        "users": users or [], "can_send": _can_send()})

    @bp.route("/api/chat/room/history", methods=["GET"])
    def chat_room_history():
        """Scrollback: a page of archived messages strictly OLDER than
        ``before`` (a timestamp), oldest-first within the page."""
        db = _db()
        if db is None:
            return jsonify({"messages": [], "done": True})
        before = str(request.args.get("before") or "").strip()
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 200))
        except (TypeError, ValueError):
            limit = 100
        msgs = db.get_chat_messages(_room_name(), before=before or None, limit=limit)
        return jsonify({"messages": msgs, "done": len(msgs) < limit})

    @bp.route("/api/chat/room/message", methods=["POST"])
    def chat_room_send():
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        if not _can_send():
            return jsonify({"error": "Chat sending is admin-only on this server"}), 403
        msg = _clean_message(request.get_json(silent=True))
        if not msg:
            return jsonify({"error": "empty message"}), 400
        # Room messages ride the SoulSync envelope (rich format; other clients
        # see line noise). PMs are NEVER encoded — they must stay readable to
        # non-SoulSync users (and the ProveIt bots need literal plaintext).
        from core import chat_codec
        body = request.get_json(silent=True) or {}
        extra = None
        rep = chat_codec.reply_of({"r": body.get("reply")})
        if rep:
            extra = {"r": rep}
        wrapped = chat_codec.encode(msg, extra)
        if wrapped is None:
            return jsonify({"error": "message too long for Soulseek chat"}), 400
        room = _room_name()
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room '%s'" % room}), 502
            ok = _run_async(client.send_room_message(room, wrapped))
        except Exception as e:
            logger.exception("chat: room send failed")
            return jsonify({"error": str(e)}), 502
        if not ok:
            return jsonify({"error": "slskd rejected the message"}), 502
        return jsonify({"ok": True})

    @bp.route("/api/chat/conversations", methods=["GET"])
    def chat_conversations():
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        try:
            convos = _run_async(client.get_conversations())
        except Exception as e:
            logger.exception("chat: conversations list failed")
            return jsonify({"error": str(e)}), 502
        return jsonify({"conversations": convos or [], "can_send": _can_send()})

    @bp.route("/api/chat/conversations/<path:username>", methods=["GET"])
    def chat_conversation(username):
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        try:
            convo = _run_async(client.get_conversation(username))
            # Reading a conversation marks it read (clears slskd's unread flag).
            # Best-effort: an ack hiccup must not hide the messages.
            try:
                _run_async(client.acknowledge_conversation(username))
            except Exception:
                logger.debug("chat: acknowledge failed for %r", username, exc_info=True)
        except Exception as e:
            logger.exception("chat: conversation fetch failed")
            return jsonify({"error": str(e)}), 502
        # slskd version drift: object-with-.messages vs a bare message list.
        if isinstance(convo, list):
            messages = convo
        elif isinstance(convo, dict):
            messages = convo.get("messages") or []
        else:
            messages = []
        return jsonify({"username": username, "messages": messages,
                        "can_send": _can_send()})

    @bp.route("/api/chat/conversations/<path:username>", methods=["POST"])
    def chat_conversation_send(username):
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        if not _can_send():
            return jsonify({"error": "Chat sending is admin-only on this server"}), 403
        msg = _clean_message(request.get_json(silent=True))
        if not msg:
            return jsonify({"error": "empty message"}), 400
        try:
            ok = _run_async(client.send_private_message(username, msg))
        except Exception as e:
            logger.exception("chat: PM send failed")
            return jsonify({"error": str(e)}), 502
        if not ok:
            return jsonify({"error": "slskd rejected the message"}), 502
        return jsonify({"ok": True})

    return bp
