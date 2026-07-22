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
_AVAILABLE = {"rooms": None, "at": 0.0}   # /rooms/available cache (big list, 5-min TTL)


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


def _extra_rooms() -> list:
    """Extra Soulseek rooms the admin joined (beyond the community room).
    Persisted in config because slskd forgets its rooms on restart — the
    room hydrate re-joins on demand, same as the home room."""
    try:
        rooms = _config_get("soulseek.chat_rooms", []) or []
    except Exception:
        return []
    out = []
    for r in rooms if isinstance(rooms, list) else []:
        r = str(r or "").strip()
        if r and r != _room_name() and r not in out:
            out.append(r)
    return out


def _resolve_room(requested) -> str | None:
    """Map a client-supplied room name to a room we serve: the home room
    (default) or a joined extra room. Unknown names → None (404) — the API
    never joins arbitrary rooms just because a request named one."""
    requested = str(requested or "").strip()
    if not requested or requested == _room_name():
        return _room_name()
    return requested if requested in _extra_rooms() else None


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


def _unwrap_room_messages(messages):
    """Decode SoulSync envelopes in a room message list. Envelope messages get
    their text swapped for the payload + rich=True; reply refs are validated
    and attached. REACTION carriers (empty-text envelopes with 're') are
    pulled OUT of the visible list into a {target_key: {emoji: [users]}} map.
    Returns (messages, reactions_map)."""
    from core import chat_codec
    out = []
    reactions: dict = {}
    for m in (messages or []):
        m = dict(m)
        dec = chat_codec.decode(m.get("message"))
        if dec is not None:
            react = chat_codec.reaction_of(dec)
            if react:
                by_emoji = reactions.setdefault(react["k"], {})
                users = by_emoji.setdefault(react["e"], [])
                u = str(m.get("username") or "")
                if u and u not in users:
                    users.append(u)
                continue                     # carriers never render as messages
            m["message"] = dec["t"]
            m["rich"] = True
            r = chat_codec.reply_of(dec)
            if r:
                m["reply"] = r
        out.append(m)
    return out, reactions


def _attach_reactions(messages, reactions) -> list:
    """Stamp aggregated reactions onto their target messages (keyed by
    sender + text-hash — reactions live as long as slskd's room buffer)."""
    if not reactions:
        return messages
    from core import chat_codec
    for m in messages:
        key = chat_codec.react_key(m.get("username"), m.get("message"))
        agg = reactions.get(key)
        if agg:
            m["reactions"] = [{"e": e, "n": len(users), "users": users[:5]}
                              for e, users in agg.items()]
    return messages


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

    @bp.route("/api/chat/room/react", methods=["POST"])
    def chat_room_react():
        """Send a reaction: an empty-text envelope carrying {re:{k,e}} that
        SoulSync clients aggregate into chips (other clients see line noise).
        No protocol ids → the target key is sender + text-hash; reactions
        can't be un-sent and live as long as slskd's room buffer."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        if not _can_send():
            return jsonify({"error": "Chat sending is admin-only on this server"}), 403
        from core import chat_codec
        body = request.get_json(silent=True) or {}
        target_user = str(body.get("target_user") or "").strip()
        target_text = str(body.get("target_text") or "")
        react = chat_codec.reaction_of({"re": {
            "k": chat_codec.react_key(target_user, target_text) if target_user else "",
            "e": body.get("e")}})
        if not react:
            return jsonify({"error": "bad reaction"}), 400
        wrapped = chat_codec.encode("", {"re": react})
        room = _resolve_room(body.get("room"))
        if room is None:
            return jsonify({"error": "Not in that room"}), 404
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room '%s'" % room}), 502
            ok = _run_async(client.send_room_message(room, wrapped))
        except Exception as e:
            logger.exception("chat: react send failed")
            return jsonify({"error": str(e)}), 502
        if not ok:
            return jsonify({"error": "slskd rejected the reaction"}), 502
        return jsonify({"ok": True})

    @bp.route("/api/chat/user/<path:username>", methods=["GET"])
    def chat_user_card(username):
        """The user popover: presence + info card from slskd, best-effort
        per field (peers can be offline / refuse info)."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        out = {"username": username}
        try:
            st = _run_async(client.get_user_status(username))
            if isinstance(st, dict):
                out["status"] = st
        except Exception:
            logger.debug("chat: user status failed", exc_info=True)
        try:
            info = _run_async(client.get_user_info(username))
            if isinstance(info, dict):
                # primitives only — no nested blobs to the page
                out["info"] = {k: v for k, v in info.items()
                               if isinstance(v, (str, int, float, bool)) and k != "picture"}
        except Exception:
            logger.debug("chat: user info failed", exc_info=True)
        return jsonify(out)

    @bp.route("/api/chat/user/<path:username>/shares", methods=["GET"])
    def chat_user_shares(username):
        """Browse a peer's shares: their directory list (names + file counts).
        Files are fetched per-directory — big shares are tens of thousands of
        files and nobody needs them all at once."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        try:
            dirs = _run_async(client.browse_user_shares(username))
        except Exception as e:
            logger.exception("chat: browse failed for %r", username)
            return jsonify({"error": str(e)}), 502
        if dirs is None:
            return jsonify({"error": "%s is offline or not sharing right now" % username}), 502
        return jsonify({"username": username, "directories": dirs})

    @bp.route("/api/chat/user/<path:username>/shares/files", methods=["GET"])
    def chat_user_share_files(username):
        """One directory of a peer's share: [{filename, size}]."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        directory = str(request.args.get("dir") or "").strip()
        if not directory:
            return jsonify({"error": "dir required"}), 400
        try:
            files = _run_async(client.browse_user_directory(username, directory))
        except Exception as e:
            logger.exception("chat: directory browse failed for %r", username)
            return jsonify({"error": str(e)}), 502
        if files is None:
            return jsonify({"error": "Could not read that folder"}), 502
        out = []
        for f in files:
            if isinstance(f, dict) and f.get("filename"):
                try:
                    size = int(f.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                out.append({"filename": str(f["filename"]), "size": size})
        return jsonify({"username": username, "directory": directory, "files": out})

    @bp.route("/api/chat/user/<path:username>/download", methods=["POST"])
    def chat_user_download(username):
        """Queue files from a peer's share into the normal slskd download
        pipeline (they land in the downloads folder and import like any other
        grab). Gated on the profile's download permission."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        if not (bool(getattr(g, "is_admin", True)) or bool(getattr(g, "can_download", True))):
            return jsonify({"error": "Your profile can't start downloads"}), 403
        files = (request.get_json(silent=True) or {}).get("files")
        if not isinstance(files, list) or not files:
            return jsonify({"error": "files required"}), 400
        files = files[:100]   # one click, one sane batch
        queued = 0
        for f in files:
            if not (isinstance(f, dict) and f.get("filename")):
                continue
            try:
                size = int(f.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            try:
                if _run_async(client.download(username, str(f["filename"]), size)):
                    queued += 1
            except Exception:
                logger.debug("chat: enqueue failed for %r from %r",
                             f.get("filename"), username, exc_info=True)
        if not queued:
            return jsonify({"error": "slskd accepted none of the files"}), 502
        return jsonify({"ok": True, "queued": queued, "failed": len(files) - queued})

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

    @bp.route("/api/chat/rooms", methods=["GET"])
    def chat_rooms():
        """The rooms rail: home room + joined extras. Any profile can read;
        managing the set is admin-only (the account's room memberships are
        visible to the whole Soulseek network)."""
        return jsonify({
            "home": _room_name(),
            "rooms": [{"name": _room_name(), "home": True}] +
                     [{"name": r, "home": False} for r in _extra_rooms()],
            "can_manage": bool(getattr(g, "is_admin", True)),
        })

    @bp.route("/api/chat/rooms/available", methods=["GET"])
    def chat_rooms_available():
        """The room browser: every public Soulseek room with its user count.
        The full list is a few thousand rooms — cached 5 minutes; the page
        filters client-side."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        import time as _time
        now = _time.time()
        if _AVAILABLE["rooms"] is None or now - _AVAILABLE["at"] > 300:
            try:
                raw = _run_async(client.get_available_rooms()) or []
            except Exception as e:
                logger.exception("chat: available rooms failed")
                return jsonify({"error": str(e)}), 502
            rooms = []
            for r in raw:
                if isinstance(r, dict) and r.get("name"):
                    rooms.append({"name": str(r["name"]),
                                  "users": int(r.get("userCount") or r.get("users") or 0),
                                  "private": bool(r.get("isPrivate") or r.get("private"))})
            rooms.sort(key=lambda r: -r["users"])
            _AVAILABLE.update(rooms=rooms, at=now)
        joined = {_room_name(), *_extra_rooms()}
        return jsonify({"rooms": _AVAILABLE["rooms"],
                        "joined": sorted(joined),
                        "can_manage": bool(getattr(g, "is_admin", True))})

    @bp.route("/api/chat/rooms/join", methods=["POST"])
    def chat_rooms_join():
        """Admin joins a public room: persisted to config (slskd forgets rooms
        on restart; the hydrate re-joins on demand) + joined now."""
        if not bool(getattr(g, "is_admin", True)):
            return jsonify({"error": "Only the admin can join rooms — the app is one "
                                     "shared Soulseek account"}), 403
        if _config_set is None:
            return jsonify({"error": "settings backend not wired"}), 500
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        room = str((request.get_json(silent=True) or {}).get("room") or "").strip()[:64]
        if not room:
            return jsonify({"error": "room name required"}), 400
        if room != _room_name():
            rooms = _extra_rooms()
            if room not in rooms:
                _config_set("soulseek.chat_rooms", rooms + [room])
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room '%s'" % room}), 502
        except Exception as e:
            logger.exception("chat: room join failed")
            return jsonify({"error": str(e)}), 502
        return jsonify({"ok": True, "room": room})

    @bp.route("/api/chat/rooms/leave", methods=["POST"])
    def chat_rooms_leave():
        """Admin leaves an extra room (config + slskd). The home room is left
        via the auto-join setting, not here — one obvious path each."""
        if not bool(getattr(g, "is_admin", True)):
            return jsonify({"error": "Only the admin can leave rooms"}), 403
        if _config_set is None:
            return jsonify({"error": "settings backend not wired"}), 500
        room = str((request.get_json(silent=True) or {}).get("room") or "").strip()
        if not room or room == _room_name():
            return jsonify({"error": "Leave the community room by turning auto-join off "
                                     "in chat settings"}), 400
        rooms = _extra_rooms()
        if room not in rooms:
            return jsonify({"error": "Not in that room"}), 404
        _config_set("soulseek.chat_rooms", [r for r in rooms if r != room])
        client = _client()
        if client is not None:
            try:
                _run_async(client.leave_room(room))
            except Exception:
                logger.debug("chat: could not leave room %r", room, exc_info=True)
        return jsonify({"ok": True})

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
        """A joined room (?room=…, default the community room): ensure joined,
        then messages + user list."""
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        room = _resolve_room(request.args.get("room"))
        if room is None:
            return jsonify({"error": "Not in that room"}), 404
        # popwaffle9000: auto-join OFF must mean OUT. This endpoint used to
        # _ensure_joined unconditionally, so the page's 4s poll silently
        # re-joined within seconds of the settings cog walking you out —
        # "uncheck auto-join to leave" simply didn't work. With the opt-out
        # set, return a not-joined payload; the page renders a join gate,
        # and Join = the settings POST flipping auto_join back on.
        # (Home room only — extra rooms are explicit admin joins; leaving
        # them removes them from the rail entirely.)
        try:
            _auto_join = bool(_config_get("soulseek.chat_auto_join", True)) if _config_get else True
        except Exception:
            _auto_join = True
        if room == _room_name() and not _auto_join:
            return jsonify({"room": room, "joined": False, "messages": [],
                            "users": [], "can_send": _can_send()})
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room '%s' — is slskd connected "
                                         "to the Soulseek network?" % room}), 502
            messages = _run_async(client.get_room_messages(room))
            users = _run_async(client.get_room_users(room))
        except Exception as e:
            logger.exception("chat: room hydrate failed")
            return jsonify({"error": str(e)}), 502
        live, reactions = _unwrap_room_messages(messages)
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
        return jsonify({"room": room, "joined": True,
                        "messages": _attach_reactions(out, reactions),
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
        room = _resolve_room(request.args.get("room"))
        if room is None:
            return jsonify({"error": "Not in that room"}), 404
        msgs = db.get_chat_messages(room, before=before or None, limit=limit)
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
        room = _resolve_room(body.get("room"))
        if room is None:
            return jsonify({"error": "Not in that room"}), 404
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
