"""Soulseek chat API — rooms + private messages, proxied through slskd.

Side-neutral (the Chat page is mounted in BOTH the music and video sidebars),
so paths are absolute /api/chat/* and the blueprint registers with no prefix —
deliberately NOT under /api/video, whose permission gate would 403 music-only
profiles.

Permission model: any signed-in profile can READ (the browser never sees the
slskd API key — everything proxies through here). SENDING speaks as the one
shared Soulseek account, so it's admin-only unless the admin opts members in
via ``soulseek.chat_member_send``.

The community room (``soulseek.chat_room``, default 'soulsync') is auto-joined
on demand: slskd room joins don't survive its restarts, so the room hydrate
re-joins whenever slskd reports us absent — idempotent, one extra call only
when actually needed.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("chat.api")

_MAX_MESSAGE_LEN = 1000

# Host-injected callables (configure() below) — avoids circular imports with
# web_server, same pattern as core/enrichment/api.py.
_client_getter = None      # () -> SoulseekClient | None (configured or None)
_run_async = None          # coroutine -> result (the shared slskd event loop)
_config_get = None         # (key, default) -> value


def configure(*, client_getter, run_async, config_get) -> None:
    global _client_getter, _run_async, _config_get
    _client_getter = client_getter
    _run_async = run_async
    _config_get = config_get


def _client():
    try:
        c = _client_getter() if _client_getter else None
    except Exception:
        return None
    return c if (c is not None and getattr(c, "base_url", None)) else None


def _room_name() -> str:
    try:
        return str(_config_get("soulseek.chat_room", "soulsync") or "soulsync")
    except Exception:
        return "soulsync"


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


def create_blueprint() -> Blueprint:
    bp = Blueprint("chat_api", __name__)

    @bp.route("/api/chat/status", methods=["GET"])
    def chat_status():
        """Cheap page hydrate: is chat usable, which room, may I send."""
        client = _client()
        return jsonify({
            "configured": client is not None,
            "room": _room_name(),
            "can_send": _can_send(),
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
        return jsonify({"room": room, "messages": messages or [],
                        "users": users or [], "can_send": _can_send()})

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
        room = _room_name()
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room '%s'" % room}), 502
            ok = _run_async(client.send_room_message(room, msg))
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
