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

import re as _re

from flask import Blueprint, g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("chat.api")

_MAX_MESSAGE_LEN = 1000
# Preset chat avatars live at webui/static/avatar/1.png .. <AVATAR_COUNT>.png.
# The id is only ever an INDEX into that fixed set — remote input must never
# reach a filesystem path.
AVATAR_COUNT = 100
# Avatars only their owner may wear. The picker hides them from everyone else,
# but the envelope is client-controlled, so ownership is ALSO enforced on send
# here and on render in chat.js — a forged id can't put someone else's face on
# your name. Keys are avatar ids, values the slskd username (compared casefold).
RESERVED_AVATARS = {100: "boulderbadgedad"}


def _avatar_allowed(av: int, username: str) -> bool:
    """True unless `av` is reserved for someone other than `username`."""
    owner = RESERVED_AVATARS.get(int(av or 0))
    if owner is None:
        return True
    return str(username or "").strip().casefold() == owner
_INGEST_AT: dict = {}      # room -> last full-buffer archive ingest (epoch)
_SELF = {"name": "", "at": 0.0}   # our slskd username, cached (network call)
_AVAILABLE = {"rooms": None, "at": 0.0}   # /rooms/available cache (big list, 5-min TTL)


def _self_username(client) -> str:
    """Our own Soulseek name, cached — several probes over the network.

    A known name is held for the full TTL; a miss is retried much sooner so an
    slskd that was still logging in resolves on the next page load instead of
    making the user wait out five minutes. Failures keep the last known good
    name rather than blanking it.
    """
    import time as _time
    now = _time.time()
    ttl = 300 if _SELF["name"] else 45
    if _SELF["at"] and now - _SELF["at"] < ttl:
        return _SELF["name"]
    try:
        name = str(_run_async(client.get_soulseek_username()) or "").strip()
    except Exception as e:
        logger.warning(f"Could not resolve our Soulseek username: {e}")
        _SELF.update(at=now)
        return _SELF["name"]
    if name:
        _SELF.update(name=name, at=now)
        return name
    logger.warning("slskd did not report a Soulseek username; self-mention "
                   "highlighting and reserved avatars stay off")
    _SELF.update(at=now)
    return _SELF["name"]

# Host-injected callables (configure() below) — avoids circular imports with
# web_server, same pattern as core/enrichment/api.py.
_client_getter = None      # () -> SoulseekClient | None (configured or None)
_run_async = None          # coroutine -> result (the shared slskd event loop)
_youtube_search = None     # (query, max_results) -> [YouTubeSearchResult] | None
_config_get = None         # (key, default) -> value
_config_set = None         # (key, value) -> None
_db_getter = None          # () -> MusicDatabase (the chat archive lives there)


def configure(*, client_getter, run_async, config_get, config_set=None,
              db_getter=None, youtube_search=None) -> None:
    global _client_getter, _run_async, _config_get, _config_set, _db_getter, \
        _youtube_search
    _client_getter = client_getter
    _run_async = run_async
    _config_get = config_get
    _config_set = config_set
    _db_getter = db_getter
    _youtube_search = youtube_search


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
    PROTOCOL carriers (envelopes with 'p' — jukebox votes, beacons, pins) are
    pulled out into an event list: machine coordination, never rendered. Most
    are live-only; the Arcade's gm.* carriers are the exception and DO get
    archived (see chat_game_carriers) because a game is durable state that
    happens to travel as messages. Returns (messages, reactions_map,
    protocol_events)."""
    from core import chat_codec
    out = []
    reactions: dict = {}
    protocol: list = []
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
            proto = chat_codec.protocol_of(dec)
            if proto:
                protocol.append({"username": str(m.get("username") or ""),
                                 "timestamp": m.get("timestamp"),
                                 "p": proto})
                # PURE carriers (empty text) vanish like reaction carriers.
                # A message with BOTH text and 'p' (piggybacked state, e.g.
                # now-playing) must still render — swallowing it would let
                # any client vanish its own text from SoulSync views.
                if not dec.get("t"):
                    continue
            m["message"] = dec["t"]
            m["rich"] = True
            # Virtual channel tag (Discord-style channels over the one room).
            # Unknown/absent is fine — the client falls back to #general so a
            # message is never invisible.
            _c = dec.get("c")
            if isinstance(_c, str) and _c.strip():
                m["chan"] = _c.strip()[:24]
            # Thread membership: `th` is the parent message key (user|timestamp),
            # `tn` the display name carried so the sidebar still has a title when
            # the parent has scrolled out of the loaded archive.
            # Preset avatar id (webui/static/avatar/<n>.png). Bounded int only —
            # it indexes a fixed set, it is NEVER used to build a path.
            try:
                _av = int(dec.get("av"))
                if 1 <= _av <= AVATAR_COUNT:
                    m["av"] = _av
            except (TypeError, ValueError):
                pass
            _th = dec.get("th")
            if isinstance(_th, str) and _th.strip():
                m["th"] = _th.strip()[:160]
                _tn = dec.get("tn")
                if isinstance(_tn, str) and _tn.strip():
                    m["tn"] = _tn.strip()[:80]
            r = chat_codec.reply_of(dec)
            if r:
                m["reply"] = r
            f = chat_codec.file_of(dec)
            if f:
                m["file"] = f
            # A shared overlay template. The definition rides its own envelope
            # key (protocol_of would reject layers-of-objects), and the card
            # carries the asset refs it depends on so the reader is told what
            # will be missing BEFORE they import rather than after.
            ov = chat_codec.overlay_of(dec)
            if ov:
                m["overlay"] = {"n": ov["n"],
                                "layers": len(ov["d"].get("layers") or []),
                                "assets": chat_codec.overlay_assets(ov["d"]),
                                "d": ov["d"]}
            # Edit carrier: the client fold replaces the target's displayed
            # text and keeps the history; the carrier itself stays a real
            # message (Soulseek can't unsend, so hiding it would lie).
            e2 = chat_codec.edit_of(dec)
            if e2:
                m["ed"] = e2
        out.append(m)
    return out, reactions, protocol


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


def _library_like(text) -> str:
    """A ``LIKE ... ESCAPE '\\'`` needle for a typed search box.

    Accents fold (``unidecode_lower`` on the column side), and the LIKE
    metacharacters are escaped: in a search box ``%`` and ``_`` are letters
    someone typed, not a request to match everything.
    """
    from core.text.normalize import normalize_for_comparison
    folded = normalize_for_comparison(str(text or ''))
    escaped = folded.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f"%{escaped}%"


def _resolve_track_path(db, track_id):
    """A library track's on-disk path. The path lives on the track's primary
    FILE row (ADR-03), not on the track, and it is stored as the MEDIA SERVER
    sees it (e.g. Plex's ``/mnt/musicBackup/...``), which the SoulSync process
    usually can't open directly — so we hand it to the shared library
    resolver, the same one the repair/import flows use. It maps the stored
    path onto SoulSync's actual mounts via ``library.music_paths`` +
    transfer/download roots (suffix-matching). None when unreachable."""
    conn = None
    try:
        from core.library2.track_files import primary_file_row
        conn = db._get_connection()
        row = primary_file_row(conn, int(track_id))
    except Exception:
        return None
    finally:
        if conn:
            conn.close()
    fp = row["path"] if row else None
    if not fp:
        return None
    try:
        from core.settings import config_manager
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(str(fp), config_manager=config_manager)
    except Exception:
        logger.debug("chat: library path resolve failed", exc_info=True)
        return None


def _filepost_upload(api_key, name, stream, expiry=None):
    """One seam for the filepost.dev HTTP call (monkeypatched in tests)."""
    import requests
    data = {}
    if expiry:
        data["expires_in"] = expiry
    r = requests.post("https://filepost.dev/v1/upload",
                      headers={"X-API-Key": api_key},
                      files={"file": (name, stream)},
                      data=data, timeout=120)
    r.raise_for_status()
    return r.json()


_AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav",
               ".wma", ".alac", ".aiff", ".ape")


def _is_safe_filepost_url(url: str) -> bool:
    """SSRF guard: only https links on filepost.dev (or a subdomain) may be
    fetched server-side. Everything else is refused before any network call."""
    try:
        from urllib.parse import urlparse
        p = urlparse(str(url or ""))
        host = (p.hostname or "").lower()
        return p.scheme == "https" and (host == "filepost.dev" or host.endswith(".filepost.dev"))
    except Exception:
        return False


def _fetch_url_to_file(url, dest_path, max_bytes):
    """One seam for the file fetch (monkeypatched in tests). Streams the URL
    to a hidden temp file next to dest, enforcing the byte cap mid-stream, then
    atomically renames into place — a partial download is never left where the
    auto-importer would pick it up. Raises on any failure / oversize."""
    import os as _os
    import requests
    tmp = dest_path + ".part"
    got = 0
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                got += len(chunk)
                if got > max_bytes:
                    fh.close()
                    _os.remove(tmp)
                    raise ValueError("file exceeds the size cap")
                fh.write(chunk)
    _os.replace(tmp, dest_path)
    return got


_YT_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_URL_RES = (
    _re.compile(r"(?:youtube\.com/watch\?(?:[^#\s]*&)?v=)([A-Za-z0-9_-]{11})"),
    _re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})"),
    _re.compile(r"(?:youtube\.com/(?:shorts|embed|live)/)([A-Za-z0-9_-]{11})"),
)


def _parse_youtube_id(q: str):
    """Extract an 11-char video id from a URL or a bare id; None otherwise."""
    q = (q or "").strip()
    if _YT_ID_RE.match(q):
        return q
    for rx in _YT_URL_RES:
        m = rx.search(q)
        if m:
            return m.group(1)
    return None


def _oembed_fetch(video_id):
    """One seam for the keyless YouTube oEmbed lookup (monkeypatched in tests).

    Returns {"title", "author_name"} or raises — never called with an
    unvalidated id (the caller regex-gates it first)."""
    import requests
    r = requests.get(
        "https://www.youtube.com/oembed",
        params={"url": f"https://www.youtube.com/watch?v={video_id}",
                "format": "json"},
        timeout=10)
    r.raise_for_status()
    return r.json()


def create_blueprint() -> Blueprint:
    bp = Blueprint("chat_api", __name__)

    # ── Arcade bank (play money) ─────────────────────────────────────────
    # Per profile, local, refilled every local midnight. It is NOT authoritative
    # for anything between players and is not meant to be: a balance only your
    # own machine can see cannot back a bet against somebody else. It exists so
    # the solo games (the slot machine) have stakes, where there is nobody to
    # defraud but yourself.
    _ARCADE_MAX_STAKE = 1000

    @bp.route("/api/chat/arcade/bank", methods=["GET"])
    def arcade_bank_get():
        db = _db()
        if db is None:
            return jsonify({"error": "Database unavailable"}), 503
        return jsonify(db.get_arcade_bank(int(getattr(g, "profile_id", 1) or 1)))

    @bp.route("/api/chat/arcade/bank", methods=["POST"])
    def arcade_bank_adjust():
        """Stake or collect. Bounded per call so a typo (or a bug) cannot mint
        a fortune in one request; going below zero is refused outright, which
        is the one rule a bank like this can actually enforce."""
        db = _db()
        if db is None:
            return jsonify({"error": "Database unavailable"}), 503
        data = request.json or {}
        try:
            delta = int(data.get("delta"))
        except (TypeError, ValueError, OverflowError):
            return jsonify({"error": "delta must be a whole number"}), 400
        if abs(delta) > _ARCADE_MAX_STAKE * 100:
            return jsonify({"error": "Amount out of range"}), 400
        out = db.adjust_arcade_bank(int(getattr(g, "profile_id", 1) or 1), delta)
        if out.get("refused"):
            return jsonify(dict(out, error="Not enough in the bank")), 400
        return jsonify(out)

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
            "filepost_key_set": bool(_cfg("soulseek.chat_filepost_key", "")),
            "filepost_expiry": str(_cfg("soulseek.chat_filepost_expiry", "") or ""),
            # Chosen preset avatar (0 = none). Server-side so it follows the
            # account across browsers rather than living in one localStorage.
            "avatar": int(_cfg("soulseek.chat_avatar", 0) or 0),
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
        if "avatar" in body:
            try:
                _av = int(body.get("avatar") or 0)
            except (TypeError, ValueError):
                _av = 0
            if not (1 <= _av <= AVATAR_COUNT):
                _av = 0
            elif not _avatar_allowed(_av, _self_username(_client())):
                _av = 0          # reserved for someone else — don't store it
            _config_set("soulseek.chat_avatar", _av)
        if "giphy_key" in body:
            # present = intentional: a value sets it, empty string clears it
            _config_set("soulseek.chat_giphy_key", str(body.get("giphy_key") or "").strip())
        if "filepost_key" in body:
            _config_set("soulseek.chat_filepost_key", str(body.get("filepost_key") or "").strip())
        if "filepost_expiry" in body:
            exp = str(body.get("filepost_expiry") or "").strip()
            if exp in ("", "24h", "7d", "30d"):
                _config_set("soulseek.chat_filepost_expiry", exp)
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
        # OUR history with this peer — the card no other Soulseek client has:
        # download count, success rate, last pull, bytes moved. Plus the local
        # private note. Both best-effort; the card renders without them.
        try:
            db = _db()
            if db is not None:
                out["history"] = db.get_user_download_stats(username)
                out["note"] = db.get_chat_user_note(username)
        except Exception:
            logger.debug("chat: user history/note failed", exc_info=True)
        return jsonify(out)

    @bp.route("/api/chat/user/<path:username>/note", methods=["POST"])
    def chat_user_note_set(username):
        """Save the local, private note for a user ('' clears it)."""
        db = _db()
        if db is None:
            return jsonify({"error": "database unavailable"}), 503
        payload = request.get_json(silent=True) or {}
        note = str(payload.get("note") or "")[:2000]
        if not db.set_chat_user_note(username, note):
            return jsonify({"error": "could not save note"}), 500
        return jsonify({"ok": True, "note": note.strip()})

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
            # Not necessarily "offline": most browse failures are slskd being
            # unable to open a direct/indirect peer connection (NAT/firewall).
            # Retries often succeed once the route is warmed up.
            return jsonify({"error": "Couldn't connect to %s — they may be "
                            "offline, or their client couldn't accept a "
                            "connection. Trying again often works." % username,
                            "retryable": True}), 502
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

    @bp.route("/api/chat/files/library-search", methods=["GET"])
    def chat_files_library_search():
        """Pick-a-track-from-your-library search for the share flow: title or
        artist match, only tracks with a stored file path, 20 max."""
        db = _db()
        if db is None:
            return jsonify({"tracks": []})
        query = str(request.args.get("q") or "").strip()
        if len(query) < 2:
            return jsonify({"tracks": []})
        conn = None
        try:
            from core.library2.track_files import primary_order
            conn = db._get_connection()
            rows = conn.execute(
                f"""SELECT t.id, t.title, tf.path, tf.size,
                          COALESCE(credited.name, album_artist.name, '') AS artist,
                          COALESCE(al.title, '') AS album
                   FROM lib2_tracks t
                   JOIN lib2_albums al ON al.id = t.album_id
                   JOIN lib2_track_files tf ON tf.id = (
                        SELECT f.id FROM lib2_track_files f
                         WHERE f.track_id = t.id
                           AND COALESCE(f.file_state, 'active') <> 'deleted'
                           AND COALESCE(f.path, '') <> ''
                         ORDER BY {primary_order('f')} LIMIT 1)
                   LEFT JOIN lib2_artists album_artist
                          ON album_artist.id = al.primary_artist_id
                   LEFT JOIN lib2_artists credited ON credited.id = (
                        SELECT ta.artist_id FROM lib2_track_artists ta
                         WHERE ta.track_id = t.id
                         ORDER BY CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,
                                  ta.position, ta.artist_id LIMIT 1)
                   WHERE unidecode_lower(t.title) LIKE :like ESCAPE '\\'
                      OR unidecode_lower(COALESCE(credited.name, '')) LIKE :like ESCAPE '\\'
                      OR unidecode_lower(COALESCE(album_artist.name, '')) LIKE :like ESCAPE '\\'
                   ORDER BY t.title LIMIT 20""",
                {"like": _library_like(query)}).fetchall()
            return jsonify({"tracks": [
                {"id": r["id"], "title": r["title"], "artist": r["artist"],
                 "album": r["album"], "size": r["size"]}
                for r in rows]})
        except Exception as e:
            logger.debug("chat: library search failed: %s", e)
            return jsonify({"tracks": []})
        finally:
            if conn:
                conn.close()

    @bp.route("/api/chat/files/upload", methods=["POST"])
    def chat_files_upload():
        """Upload to filepost.dev and hand back the CDN link + metadata.

        Two sources: a browser file (multipart 'file') or a LIBRARY track
        (json {track_id} — the file path resolves server-side, so sharing a
        track is search → click, no filesystem browsing). Requires the
        user's filepost API key (Settings → chat cog); size-capped at 50MB
        (filepost free tier). Same gate as talking — this SENDS content."""
        if not _can_send():
            return jsonify({"error": "Sending is disabled for this profile"}), 403
        try:
            api_key = str(_config_get("soulseek.chat_filepost_key", "") or "").strip()
        except Exception:
            api_key = ""
        if not api_key:
            return jsonify({"error": "No filepost.dev API key configured "
                                     "(chat settings cog)"}), 503

        max_bytes = 50 * 1024 * 1024
        name = None
        stream = None
        size = None
        if "file" in request.files:
            # REVIEW CATCH: the cap must hold server-side for browser uploads
            # too — content_length covers the whole multipart body (slightly
            # over the file size, never under), so this rejects before any
            # bytes stream to filepost or hang the worker.
            if (request.content_length or 0) > max_bytes + 1024 * 1024:
                return jsonify({"error": "File is too big — filepost.dev caps "
                                         "uploads at 50 MB"}), 413
            fs = request.files["file"]
            name = str(fs.filename or "file")[:200]
            stream = fs.stream
        else:
            body = request.get_json(silent=True) or {}
            track_id = body.get("track_id")
            if not track_id:
                return jsonify({"error": "No file or track_id given"}), 400
            db = _db()
            path = _resolve_track_path(db, track_id) if db is not None else None
            if not path:
                return jsonify({"error": "Can't reach that track's file — it's "
                                         "stored at your media server's path. Add "
                                         "where SoulSync sees your music under "
                                         "Settings → Library → Music Paths."}), 404
            import os as _os
            size = _os.path.getsize(path)
            if size > max_bytes:
                return jsonify({"error": "File is %.0f MB — filepost.dev caps "
                                         "uploads at 50 MB" % (size / 1048576)}), 413
            name = _os.path.basename(path)
            stream = open(path, "rb")

        try:
            expiry = str(_config_get("soulseek.chat_filepost_expiry", "") or "").strip()
        except Exception:
            expiry = ""
        try:
            result = _filepost_upload(api_key, name, stream, expiry or None)
        except Exception as e:
            logger.exception("chat: filepost upload failed")
            return jsonify({"error": "Upload failed: %s" % e}), 502
        finally:
            try:
                if stream is not None and hasattr(stream, "close"):
                    stream.close()
            except Exception:
                logger.debug("chat: upload stream close failed", exc_info=True)
        url = None
        if isinstance(result, dict):
            url = result.get("url") or result.get("cdn_url") or result.get("link")
            if not url and isinstance(result.get("file"), dict):
                url = result["file"].get("url")
        if not url:
            return jsonify({"error": "filepost.dev returned no URL"}), 502
        import mimetypes as _mt
        mime = _mt.guess_type(name)[0] or ""
        return jsonify({"ok": True, "url": url, "name": name,
                        "size": size, "mime": mime})

    @bp.route("/api/chat/files/import", methods=["POST"])
    def chat_files_import():
        """Save a shared audio file into YOUR library: fetch the filepost link
        into the auto-import staging folder, where the import pipeline picks it
        up (automatically if Auto-Import is on, else it's waiting on the Import
        page). Audio only — video needs library matching, a separate flow.
        Admin-only: it writes a file into your library's intake folder."""
        import os as _os
        if not bool(getattr(g, "is_admin", True)):
            return jsonify({"error": "Saving to the library is admin-only"}), 403
        body = request.get_json(silent=True) or {}
        url = str(body.get("url") or "").strip()
        name = _os.path.basename(str(body.get("name") or "").strip())
        mime = str(body.get("mime") or "").lower()

        if not _is_safe_filepost_url(url):
            return jsonify({"error": "Only filepost.dev links can be saved"}), 400
        is_audio = mime.startswith("audio/") or name.lower().endswith(_AUDIO_EXTS)
        if not is_audio:
            return jsonify({"error": "Only audio files can be saved to your "
                                     "library right now"}), 400
        if not name or name in (".", ".."):
            name = "shared-track"
            for ext in _AUDIO_EXTS:
                if mime.endswith(ext.lstrip(".")):
                    name += ext
                    break

        try:
            from core.imports.paths import docker_resolve_path
            staging = docker_resolve_path(
                str(_config_get("import.staging_path", "./Staging") or "./Staging"))
            _os.makedirs(staging, exist_ok=True)
        except Exception as e:
            logger.exception("chat: staging dir unavailable")
            return jsonify({"error": "Couldn't reach the import staging folder: %s" % e}), 500

        dest = _os.path.join(staging, name)
        if _os.path.exists(dest):
            return jsonify({"error": "A file named %r is already waiting to "
                                     "import" % name}), 409

        # 60MB ceiling — a little above the 50MB filepost upload cap, room for
        # the encoding overhead a re-hosted file can carry.
        max_bytes = 60 * 1024 * 1024
        try:
            got = _fetch_url_to_file(url, dest, max_bytes)
        except Exception as e:
            logger.warning("chat: library import fetch failed: %s", e)
            return jsonify({"error": "Couldn't fetch that file (%s)" % e}), 502

        auto = bool(_config_get("auto_import.enabled", False))
        logger.info("chat: saved shared file to staging (%s, %d bytes, auto=%s)",
                    name, got, auto)
        return jsonify({"ok": True, "name": name, "bytes": got, "auto_import": auto})

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
            # timeout=5: the chat page hydrates this every 4s, and the app has
            # 8 request threads total — an unbounded wait on a slow slskd let
            # one hung hydrate pin a slot indefinitely (perf sweep, Aug 2026).
            # A miss 502s and the next poll simply tries again.
            messages = _run_async(client.get_room_messages(room), timeout=5)
            users = _run_async(client.get_room_users(room), timeout=5)
        except Exception as e:
            logger.exception("chat: room hydrate failed")
            return jsonify({"error": str(e)}), 502
        live, reactions, protocol_events = _unwrap_room_messages(messages)
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
                    # The WRITE is throttled with the messages; the read below
                    # is not. Reactions are carriers, so the message archive
                    # never held them and a reaction died with slskd's buffer.
                    db.add_chat_reactions(room, reactions)
                # Merged on EVERY hydrate, never on the 60s tick alone: this
                # page polls every 4s, so folding stored reactions in only when
                # the throttle opens would make old chips appear for one poll
                # and vanish for the next fourteen.
                for _k, _by in (db.get_chat_reactions(room) or {}).items():
                    _live = reactions.setdefault(_k, {})
                    for _e, _users in _by.items():
                        _cur = _live.setdefault(_e, [])
                        for _u in _users:
                            if _u not in _cur:
                                _cur.append(_u)
                # Game carriers ride every hydrate rather than the 60s throttle:
                # they are rare (usually none at all), the natural-key UNIQUE
                # makes repeats free, and losing one loses a move.
                db.add_chat_game_carriers(room, protocol_events)
                arch = db.get_chat_messages(room, limit=100)
                if arch:
                    out = arch
            except Exception:
                logger.debug("chat: archive unavailable, serving live buffer", exc_info=True)
        # Games outlive slskd's buffer: replay the archived gm.* carriers
        # ahead of the live feed so a match survives a restart even when nobody
        # in the room is still holding it. Asking the room is always the first
        # move (see gm.sync); this is the backstop for a cold room. Deduped on
        # the same natural key the client uses, and ordered oldest-first
        # because the fold depends on stream order.
        feed = protocol_events
        if db is not None:
            try:
                seen = {(e.get("username"), e.get("timestamp"),
                         (e.get("p") or {}).get("k")) for e in protocol_events}
                revived = [e for e in db.get_chat_game_carriers(room)
                           if (e["username"], e["timestamp"], e["p"].get("k")) not in seen]
                if revived:
                    feed = revived + protocol_events
            except Exception:
                logger.debug("chat: game carrier replay unavailable", exc_info=True)
        return jsonify({"room": room, "joined": True,
                        "messages": _attach_reactions(out, reactions),
                        "users": users or [], "can_send": _can_send(),
                        # machine coordination events (jukebox/polls/beacons)
                        # from the live buffer, plus replayed game carriers
                        "protocol": feed})

    @bp.route("/api/chat/room/protocol", methods=["POST"])
    def chat_room_protocol_send():
        """Send a PROTOCOL carrier into the room: an empty-text envelope whose
        'p' object coordinates SoulSync clients (a vote, a beacon, a pin).
        Vanilla clients see one line of envelope noise; SoulSync clients
        intercept it before render. Same send gate as talking — a client that
        can't speak can't emit machine chatter either."""
        from core import chat_codec
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        if not _can_send():
            return jsonify({"error": "Sending is disabled for this profile"}), 403
        payload = request.get_json(silent=True) or {}
        proto = chat_codec.protocol_of({"p": payload.get("p")})
        if proto is None:
            return jsonify({"error": "Malformed protocol payload"}), 400
        room = _resolve_room(payload.get("room"))
        if room is None:
            return jsonify({"error": "Not in that room"}), 404
        # NOTE: the avatar rides INSIDE the protocol payload for carriers (the
        # 'hello' beacon sends {k:'hello', av:N}) — an envelope-level field
        # would be dropped here, since pure carriers never reach the message
        # unwrap path that reads it.
        encoded = chat_codec.encode("", {"p": proto})
        if encoded is None:
            return jsonify({"error": "Protocol payload too large"}), 400
        try:
            if not _ensure_joined(client, room):
                return jsonify({"error": "Could not join room"}), 502
            ok = _run_async(client.send_room_message(room, encoded))
        except Exception as e:
            logger.exception("chat: protocol send failed")
            return jsonify({"error": str(e)}), 502
        if not ok:
            return jsonify({"error": "slskd refused the message"}), 502
        return jsonify({"ok": True})

    @bp.route("/api/chat/jukebox/resolve", methods=["POST"])
    def chat_jukebox_resolve():
        """Turn a user's jukebox input into candidate tracks.

        A YouTube URL or bare 11-char id resolves via keyless oEmbed (one
        exact result); anything else goes through yt-dlp search when wired
        (up to 5 picks). Gated like sending — submitting to the queue IS
        sending protocol chatter, so a client that can't speak can't make
        the server fetch on its behalf either."""
        if not _can_send():
            return jsonify({"error": "Sending is disabled for this profile"}), 403
        payload = request.get_json(silent=True) or {}
        q = str(payload.get("q") or "").strip()
        if not q or len(q) > 200:
            return jsonify({"error": "Give me a YouTube link or a search"}), 400

        vid = _parse_youtube_id(q)
        if vid:
            try:
                meta = _oembed_fetch(vid) or {}
            except Exception:
                return jsonify({"error": "That video could not be resolved"}), 404
            return jsonify({"results": [{
                "id": vid,
                "title": str(meta.get("title") or "")[:120] or vid,
                "channel": str(meta.get("author_name") or "")[:80],
            }]})

        if _youtube_search is None:
            return jsonify({"error": "Search is unavailable — paste a YouTube link"}), 503
        try:
            found = _youtube_search(q, 5) or []
        except Exception:
            logger.exception("chat: jukebox search failed")
            return jsonify({"error": "YouTube search failed"}), 502
        results = []
        for v in found[:5]:
            vid2 = str(getattr(v, "video_id", "") or "")
            if not _YT_ID_RE.match(vid2):
                continue
            results.append({
                "id": vid2,
                "title": str(getattr(v, "title", "") or "")[:120],
                "channel": str(getattr(v, "channel", "") or "")[:80],
                "duration": int(getattr(v, "duration", 0) or 0),
                "views": int(getattr(v, "view_count", 0) or 0),
            })
        return jsonify({"results": results})

    # ── Auto-DJ radio brain ────────────────────────────────────────────────
    # The old radio searched YouTube for the PLAYING TRACK'S OWN TITLE, so the
    # top surviving hit was usually the same song again (another upload, a live
    # cut, a cover). This picks a genuinely different NEXT track using the
    # similarity data SoulSync already owns, best source first:
    #   1. Last.fm similar TRACKS  — a real track-level radio graph
    #   2. Last.fm similar ARTISTS — neighbour artist, let YouTube pick the song
    #   3. the local similar_artists graph (built by watchlist scans)
    # `avoid` (artist/track strings the room heard recently) is honoured at
    # every tier, so radio keeps moving instead of circling. Returns a search
    # QUERY; the client resolves it through /jukebox/resolve like any add.
    _RADIO_NOISE = _re.compile(
        r"\((?:[^)]*\b(?:official|lyric|lyrics|audio|video|visualizer|hd|4k|mv|remaster[^)]*)\b[^)]*)\)"
        r"|\[[^\]]*\]", _re.I)

    # Plenty of what gets pasted isn't a song at all — mixes, DJ sets, genre
    # essays ("this is what deep ambient techno is supposed to feel like |
    # PART II"). Those have no artist to branch from, but they DO have a genre,
    # which is the better thing to follow anyway.
    _RADIO_MIXY = _re.compile(
        r"\b(mix|mixtape|set|dj\s*set|liveset|live\s*set|playlist|compilation|"
        r"session|sessions|radio|episode|ep\.?\s*\d|vol\.?\s*\d|part\s+\w+|pt\.?\s*\d|"
        r"hour|hours|minutes|continuous|mixed\s+by|selected\s+by|full\s+album)\b", _re.I)
    # phrase-y titles that are describing a feeling/genre, not naming a track
    _RADIO_ESSAY = _re.compile(
        r"\b(this is what|sounds? like|feel like|feels like|music (?:to|for)|"
        r"songs? (?:to|for)|when you|that make)\b", _re.I)
    _RADIO_STOP = _re.compile(
        r"\b(this|is|what|are|the|a|an|of|to|for|and|you|your|when|it|its|"
        r"supposed|feel|feels|like|sounds?|music|songs?|track|tracks|"
        r"mix|mixtape|set|playlist|compilation|session|sessions|radio|"
        r"part|pt|vol|volume|episode|full|album|best|top|new|old|dj|"
        r"hour|hours|minute|minutes|continuous|official|video|audio|"
        # roman numerals trailing a series title ("… | PART II") would other-
        # wise poison the tag lookup ("deep ambient techno II" matches nothing)
        r"i{1,3}|iv|v|vi{1,3}|ix|x)\b", _re.I)

    def _radio_parse(raw):
        """('track'|'vibe', artist, track, vibe) from a YouTube-style title.

        'track' → a real song we can branch from by artist/track.
        'vibe'  → a mix/genre video; follow its GENRE instead.
        """
        t = _RADIO_NOISE.sub(" ", str(raw or ""))
        t = _re.sub(r"\s+", " ", t).strip()
        if not t:
            return "vibe", "", "", ""
        mixy = bool(_RADIO_MIXY.search(t) or _RADIO_ESSAY.search(t))
        # Only a dash separates artist from track. '|' and '~' are YouTube title
        # decoration ("... | PART II") and must never be read as an artist.
        if not mixy:
            for sep in (" - ", " – ", " — "):
                if sep in t:
                    left, right = t.split(sep, 1)
                    left, right = left.strip()[:80], right.strip()[:80]
                    # An artist name is short. A sentence on the left means this
                    # is a phrase, not "Artist - Track".
                    if left and right and len(left) <= 40 and len(left.split()) <= 6:
                        return "track", left, right, ""
                    break
        # Vibe: strip the filler words and keep the genre-ish remainder.
        words = [w for w in _re.split(r"[^A-Za-z0-9']+", t) if w]
        keep = [w for w in words if not _RADIO_STOP.fullmatch(w) and not w.isdigit()]
        return "vibe", "", "", " ".join(keep[:6])[:80]

    @bp.route("/api/chat/jukebox/radio", methods=["POST"])
    def chat_jukebox_radio():
        if not _can_send():
            return jsonify({"error": "Chat sending is admin-only on this server"}), 403
        body = request.get_json(silent=True) or {}
        kind, artist, track, vibe = _radio_parse(body.get("title"))
        avoid = set()
        for x in (body.get("avoid") or [])[:80]:
            s = str(x or "").strip().lower()
            if s:
                avoid.add(s)
        if not artist and not track and not vibe:
            return jsonify({"query": None, "why": "no seed"})

        def _fresh(a, t=""):
            """Skip anything the room just heard (either field matching)."""
            al, tl = str(a or "").strip().lower(), str(t or "").strip().lower()
            if al and al in avoid:
                return False
            if tl and tl in avoid:
                return False
            return bool(al or tl)

        lastfm = None
        try:
            from core.lastfm_client import LastFMClient
            from core.settings import config_manager as _cfg
            _key = _cfg.get("lastfm.api_key", "")
            if _key:
                lastfm = LastFMClient(api_key=_key)
        except Exception:   # noqa: BLE001 - radio degrades, never breaks the room
            logger.debug("chat radio: lastfm unavailable", exc_info=True)

        # 0) VIBE seeds (a mix, a DJ set, a "this is what X feels like" video).
        # There's no artist to branch from, but the GENRE is the better thread
        # to pull anyway: ask Last.fm who defines that tag and play them.
        if kind == "vibe" and vibe:
            if lastfm:
                try:
                    for cand in (lastfm.get_tag_top_artists(vibe, limit=40) or []):
                        ca = str(cand.get("name") or "")
                        if _fresh(ca):
                            return jsonify({"query": ca, "why": "%s" % vibe})
                except Exception:   # noqa: BLE001
                    logger.debug("chat radio: tag lookup failed", exc_info=True)
            # Unknown tag (or no Last.fm): stay in the same lane by searching
            # for more of the same KIND of thing rather than the same video.
            return jsonify({"query": "%s mix" % vibe, "why": vibe})

        # 1) similar TRACKS — the closest thing to a real station
        if lastfm and artist and track:
            try:
                for cand in (lastfm.get_similar_tracks(artist, track, limit=30) or []):
                    ca = str((cand.get("artist") or {}).get("name")
                             if isinstance(cand.get("artist"), dict) else cand.get("artist") or "")
                    ct = str(cand.get("name") or cand.get("title") or "")
                    if _fresh(ca, ct):
                        return jsonify({"query": ("%s %s" % (ca, ct)).strip(),
                                        "why": "similar to %s" % (track or artist)})
            except Exception:   # noqa: BLE001
                logger.debug("chat radio: similar-tracks failed", exc_info=True)

        # 2) similar ARTISTS — hand YouTube a neighbour and let it choose
        if lastfm and artist:
            try:
                for cand in (lastfm.get_similar_artists(artist, limit=30) or []):
                    ca = str(cand.get("name") or "")
                    if _fresh(ca):
                        return jsonify({"query": ca, "why": "similar to %s" % artist})
            except Exception:   # noqa: BLE001
                logger.debug("chat radio: similar-artists failed", exc_info=True)

        # 3) the local similar-artist graph the watchlist scan already built.
        # It only hits for artists the user owns, so the playing artist is
        # looked up in the library by name — folded, because the seed is a
        # YouTube title and its spelling is not the library's.
        #
        # `similar_artists.source_artist_id` is a PROVIDER id (whichever id the
        # scan ran with — see `similar_artists_worker.pick_source_artist_id`),
        # so the library's job here is to hand over the artist's provider ids.
        # Spotify and MusicBrainz sit in their own columns, every other
        # provider inside `external_ids`.
        if artist:
            conn = None
            try:
                from core.library2.provider_ids import parse_external_ids
                from core.text.normalize import normalize_for_comparison
                conn = _db()._get_connection()
                source_ids = []
                for row in conn.execute(
                    "SELECT spotify_id, musicbrainz_id, external_ids "
                    "FROM lib2_artists WHERE unidecode_lower(name) = ?",
                    (normalize_for_comparison(artist),),
                ).fetchall():
                    known = parse_external_ids(row["external_ids"])
                    for value in (row["spotify_id"], known.get("itunes"),
                                  known.get("deezer"), row["musicbrainz_id"]):
                        text = str(value or "").strip()
                        if text and text not in source_ids:
                            source_ids.append(text)
                marks = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    "SELECT similar_artist_name FROM similar_artists "
                    f"WHERE source_artist_id IN ({marks}) "
                    "ORDER BY similarity_rank LIMIT 30",
                    source_ids,
                ).fetchall() if source_ids else []
                for row in rows:
                    ca = str(row[0] or "")
                    if _fresh(ca):
                        return jsonify({"query": ca, "why": "similar to %s" % artist})
            except Exception:   # noqa: BLE001
                logger.debug("chat radio: local graph unavailable", exc_info=True)
            finally:
                if conn:
                    conn.close()

        return jsonify({"query": None, "why": "no similar data"})

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

    @bp.route("/api/chat/room/search", methods=["GET"])
    def chat_room_search():
        """Archive search: ?room=&q= → newest-first matches (message text or
        sender). Local archive only — Soulseek has no server-side history."""
        db = _db()
        if db is None:
            return jsonify({"messages": []})
        room = _resolve_room(request.args.get("room"))
        if room is None:
            return jsonify({"error": "Not in that room"}), 404
        qstr = str(request.args.get("q") or "").strip()[:200]
        if not qstr:
            return jsonify({"messages": []})
        return jsonify({"messages": db.search_chat_messages(room, qstr), "q": qstr})

    @bp.route("/api/chat/room/message", methods=["POST"])
    def chat_room_send():
        client = _client()
        if client is None:
            return jsonify({"error": "Soulseek (slskd) is not configured"}), 503
        if not _can_send():
            return jsonify({"error": "Chat sending is admin-only on this server"}), 403
        msg = _clean_message(request.get_json(silent=True))
        # An overlay share carries no text on purpose - the CARD is the message,
        # the way a poll or a game move is - so this guard has to know about it or
        # the feature can never send anything. A file share slips past because its
        # url IS the text.
        _shared_overlay = isinstance((request.get_json(silent=True) or {}).get("overlay"), dict)
        if not msg and not _shared_overlay:
            return jsonify({"error": "empty message"}), 400
        # Room messages ride the SoulSync envelope (rich format; other clients
        # see line noise). PMs are NEVER encoded — they must stay readable to
        # non-SoulSync users (and the ProveIt bots need literal plaintext).
        from core import chat_codec
        body = request.get_json(silent=True) or {}

        # PLAIN mode. Everything in this room was enveloped unconditionally, so
        # anyone talking to a vanilla Soulseek user was talking to themselves —
        # the room LOOKED shared and was not. `plain` sends the raw text so
        # every Soulseek client can read it.
        #
        # Nothing rich can ride along: there is no envelope to carry a reply
        # ref, a template, a channel tag or an avatar. Those are REFUSED rather
        # than quietly dropped — silently sending a bare sentence when someone
        # attached a template is the same class of lie this mode exists to fix.
        if body.get("plain") is True:
            for field, label in (("overlay", "an overlay template"), ("file", "a file"),
                                 ("reply", "a reply"), ("edit", "an edit")):
                if body.get(field):
                    return jsonify({"error": "Plain text can't carry %s — every Soulseek "
                                             "client has to be able to read it. Switch back "
                                             "to SoulSync format to send that." % label}), 400
            chan = str(body.get("chan") or "").strip().lower()
            if chan and chan != "general":
                return jsonify({"error": "Plain text always goes to the main room — a "
                                         "channel tag needs the SoulSync envelope."}), 400
            if body.get("thread"):
                return jsonify({"error": "Plain text can't go in a thread — threads need "
                                         "the SoulSync envelope."}), 400
            room = _resolve_room(body.get("room"))
            if room is None:
                return jsonify({"error": "Not in that room"}), 404
            try:
                if not _ensure_joined(client, room):
                    return jsonify({"error": "Could not join room '%s'" % room}), 502
                ok = _run_async(client.send_room_message(room, msg))
            except Exception as e:
                logger.exception("chat: plain room send failed")
                return jsonify({"error": str(e)}), 502
            if not ok:
                return jsonify({"error": "slskd rejected the message"}), 502
            return jsonify({"ok": True, "plain": True})

        extra = None
        rep = chat_codec.reply_of({"r": body.get("reply")})
        if rep:
            extra = {"r": rep}
        # A shared overlay template. Validated by the SAME codec the receive
        # path uses, so anything that leaves here is something a reader's card
        # can render. Refused loudly rather than sent as a dud: a template that
        # exceeds the wire limit would otherwise arrive truncated, which reads
        # as a broken design rather than as a message that did not fit.
        ovl = chat_codec.overlay_of({"o": body.get("overlay")})
        if body.get("overlay") is not None and ovl is None:
            return jsonify({"error": "That overlay template can't be shared "
                                     "(it needs a name and at least one layer)."}), 400
        if ovl:
            extra = dict(extra or {})
            extra["o"] = ovl
        fmeta = chat_codec.file_of({"f": body.get("file")})
        if fmeta:
            extra = dict(extra or {})
            extra["f"] = fmeta
        # Virtual channel tag. Slug-validated here so a hostile client can't
        # stuff arbitrary text into the envelope; the default channel is left
        # untagged so old clients (and vanilla Soulseek) read it as #general.
        chan = str(body.get("chan") or "").strip().lower()[:24]
        if chan and chan != "general" and _re.fullmatch(r"[a-z0-9][a-z0-9-]*", chan):
            extra = dict(extra or {})
            extra["c"] = chan
        # Preset avatar id, validated to the known range. Riding it on messages
        # you're already sending costs a few bytes and adds NO extra carrier
        # messages (so no extra line noise for vanilla Soulseek clients).
        try:
            _av = int(body.get("avatar"))
            if 1 <= _av <= AVATAR_COUNT and _avatar_allowed(_av, _self_username(client)):
                extra = dict(extra or {})
                extra["av"] = _av
        except (TypeError, ValueError):
            pass
        # Thread membership (parent message key + carried display name).
        thread = str(body.get("thread") or "").strip()[:160]
        if thread:
            extra = dict(extra or {})
            extra["th"] = thread
            tname = str(body.get("thread_name") or "").strip()[:80]
            if tname:
                extra["tn"] = tname
        # Message edit: 'ed' targets one of the sender's OWN earlier messages
        # by key; this envelope's text is the replacement. Author-match and
        # the 2-edit cap are enforced by the client fold on every machine —
        # the server only shape-checks (edit_of).
        edit = chat_codec.edit_of({"ed": body.get("edit")})
        if edit:
            extra = dict(extra or {})
            extra["ed"] = edit
        wrapped = chat_codec.encode(msg, extra)
        if wrapped is None:
            # Name the half that did not fit. With a template attached the text
            # is almost never the problem, and "message too long" sends someone
            # to shorten a sentence that was already short.
            if ovl:
                layers = len((ovl.get("d") or {}).get("layers") or [])
                return jsonify({"error": "That template is too big to send over chat "
                                         "(%d layers). Export it to a file instead."
                                         % layers}), 400
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
