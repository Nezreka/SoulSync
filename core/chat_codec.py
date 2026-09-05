"""The SoulSync chat envelope — rich room messages other clients can't render.

A FORMAT, not a secret (like a .flac in a text editor): `!SS1!` + base64 of
zlib-compressed versioned JSON. SoulseekQT/Nicotine+ show line noise; SoulSync
decodes and renders the rich payload. Deliberately NO crypto — the repo is
public, so a baked-in key would be theater; anyone implementing this format
has simply adopted it.

Envelope v1: {"v": 1, "t": "<message text, markdown subset>"}
Unknown extra keys are preserved on decode (forward compatibility).

Hostile-input posture: everything arriving here is REMOTE data. decode()
returns None for anything that isn't a well-formed, size-sane v1 envelope —
bad base64, zlib bombs, wrong JSON shape, oversized text. Callers treat a
None as ordinary plaintext and render it escaped like any other message.
"""

from __future__ import annotations

import base64
import json
import zlib

from utils.logging_config import get_logger

logger = get_logger("chat.codec")

MARKER = "!SS1!"

# Soulseek chat messages have practical size limits; stay comfortably under.
MAX_ENCODED_LEN = 2000      # what we're willing to SEND (marker included)
MAX_WIRE_LEN = 8192         # what we're willing to even LOOK at on receive
MAX_RAW_BYTES = 16384       # decompression ceiling (zip-bomb guard)
MAX_TEXT_LEN = 4000         # decoded message text cap


def encode(text: str, extra: dict | None = None) -> str | None:
    """Wrap message text in a v1 envelope. None when it can't fit the wire
    limit (the caller should tell the user, not silently truncate).
    ``extra`` merges additional envelope fields (e.g. the reply reference
    {"r": {...}}) — the CALLER validates them; "v"/"t" can't be overridden."""
    payload = dict(extra or {})
    payload["v"] = 1
    payload["t"] = str(text or "")
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    packed = MARKER + base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    if len(packed) > MAX_ENCODED_LEN:
        return None
    return packed


def decode(text) -> dict | None:
    """The envelope payload dict ({'v':1,'t':...}), or None for anything that
    isn't a healthy SoulSync envelope. Never raises."""
    if not isinstance(text, str) or not text.startswith(MARKER):
        return None
    if len(text) > MAX_WIRE_LEN:
        return None
    body = text[len(MARKER):].strip()
    try:
        packed = base64.b64decode(body, validate=True)
        # Bounded decompression: a crafted envelope must not be able to
        # balloon into memory (classic zlib bomb).
        d = zlib.decompressobj()
        raw = d.decompress(packed, MAX_RAW_BYTES)
        if d.unconsumed_tail:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None
    t = payload.get("t")
    if not isinstance(t, str) or len(t) > MAX_TEXT_LEN:
        return None
    return payload


def react_key(username: str, text: str) -> str:
    """The reaction target key: a message has no protocol id, so reactions
    bind to (sender, text-hash). Known limitation: identical texts by the
    same sender share reactions."""
    import hashlib
    h = hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:8]
    return f"{str(username or '')[:64]}|{h}"


def reaction_of(payload) -> dict | None:
    """The validated reaction from a decoded envelope ({'k','e'}), or None.
    Remote input — strict shape, tiny caps (an emoji, not an essay)."""
    r = (payload or {}).get("re")
    if not isinstance(r, dict):
        return None
    k = str(r.get("k") or "").strip()[:80]
    e = str(r.get("e") or "").strip()
    if not k or "|" not in k or not e or len(e) > 8 or any(c in e for c in "<>&\"'"):
        return None
    return {"k": k, "e": e}


import re as _re

_PROTOCOL_KIND_RE = _re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)?$")


def protocol_of(payload) -> dict | None:
    """The validated protocol object from a decoded envelope ({'k': kind, ...}),
    or None. Protocol carriers are empty-text envelopes carrying machine
    coordination (jukebox votes, polls, pins, presence beacons) — invisible
    to vanilla clients, intercepted before the visible list and NEVER
    archived. REMOTE input: kind is a short dotted identifier, ≤16 fields,
    strings ≤512, numbers finite, one nesting level (list/dict) with the
    same caps. Mirrors ChatProtocol.parseProtocol in chat-protocol.js —
    the two validators must agree or clients desync."""
    p = (payload or {}).get("p") if isinstance(payload, dict) else None
    if not isinstance(p, dict):
        return None
    k = p.get("k")
    if not isinstance(k, str) or len(k) > 24 or not _PROTOCOL_KIND_RE.match(k):
        return None

    def _scalar_ok(v):
        if isinstance(v, str):
            return len(v) <= 512
        if isinstance(v, bool) or v is None:
            return True
        if isinstance(v, (int, float)):
            return abs(v) < 1e15
        return False

    def _payload_ok(obj, depth):
        if not isinstance(obj, dict) or len(obj) > 16:
            return False
        for v in obj.values():
            if _scalar_ok(v):
                continue
            if depth > 0 and isinstance(v, list):
                if len(v) > 32 or not all(_scalar_ok(x) for x in v):
                    return False
            elif depth > 0 and isinstance(v, dict):
                if not _payload_ok(v, depth - 1):
                    return False
            else:
                return False
        return True

    if not _payload_ok(p, 1):
        return None
    return p


def file_of(payload) -> dict | None:
    """The validated file-card metadata from a decoded envelope ({'n' name,
    's' size, 'm' mime}), or None. The URL itself travels as the message
    TEXT (so the link survives archives and copy); this object only dresses
    the card. REMOTE input — caps everywhere."""
    f = (payload or {}).get("f") if isinstance(payload, dict) else None
    if not isinstance(f, dict):
        return None
    n = str(f.get("n") or "").strip()[:200]
    if not n:
        return None
    out = {"n": n}
    try:
        size = int(f.get("s") or 0)
        if 0 < size < 10 ** 12:
            out["s"] = size
    except (TypeError, ValueError):
        pass
    m = str(f.get("m") or "").strip()[:80]
    if m:
        out["m"] = m
    return out


# An overlay template shared into the room. The definition rides in its OWN
# envelope key rather than in the protocol object, because protocol_of allows a
# single level of nesting and a template is layers-of-objects — it would be
# rejected outright. Same arrangement file cards ('f') and reactions ('re')
# already use.
#
# Everything here is REMOTE input from a public chat room, so the caps are the
# point. A definition is a drawing instruction that another install will render:
# it must be bounded in every dimension a renderer could be hurt by.
OVERLAY_MAX_LAYERS = 60
OVERLAY_MAX_JSON = 12000       # serialized definition ceiling
OVERLAY_MAX_NAME = 120
OVERLAY_MAX_KEYS = 40          # per object
OVERLAY_MAX_STR = 512
OVERLAY_MAX_DEPTH = 4          # layer > bg/shadow > value is 3; one spare


def _overlay_value_ok(v, depth: int) -> bool:
    """One value inside a template definition, recursively bounded."""
    if isinstance(v, str):
        return len(v) <= OVERLAY_MAX_STR
    if isinstance(v, bool) or v is None:
        return True
    if isinstance(v, (int, float)):
        # NaN and infinity survive json round-trips in some encoders and turn
        # into a renderer dividing by nothing.
        return v == v and abs(v) < 1e9 if isinstance(v, float) else abs(v) < 10 ** 12
    if depth <= 0:
        return False
    if isinstance(v, list):
        return len(v) <= OVERLAY_MAX_LAYERS and all(
            _overlay_value_ok(x, depth - 1) for x in v)
    if isinstance(v, dict):
        return len(v) <= OVERLAY_MAX_KEYS and all(
            isinstance(k, str) and len(k) <= 64 and _overlay_value_ok(x, depth - 1)
            for k, x in v.items())
    return False


def overlay_of(payload) -> dict | None:
    """A shared overlay template from a decoded envelope ({'n' name, 'd'
    definition}), or None.

    Refuses anything that is not a renderable template: a definition needs a
    layers LIST, because a template with no layers paints nothing and a card
    offering one is a card that wastes a click. Bounded in every direction —
    layer count, serialized size, nesting depth, key count, string length,
    number magnitude — since this arrives from a public room and will be handed
    to a poster renderer.
    """
    o = (payload or {}).get("o") if isinstance(payload, dict) else None
    if not isinstance(o, dict):
        return None
    name = " ".join(str(o.get("n") or "").split())[:OVERLAY_MAX_NAME]
    d = o.get("d")
    if not name or not isinstance(d, dict):
        return None
    layers = d.get("layers")
    if not isinstance(layers, list) or not layers or len(layers) > OVERLAY_MAX_LAYERS:
        return None
    if not all(isinstance(x, dict) for x in layers):
        return None
    if not _overlay_value_ok(d, OVERLAY_MAX_DEPTH):
        return None
    try:
        if len(json.dumps(d, separators=(",", ":"))) > OVERLAY_MAX_JSON:
            return None
    except (TypeError, ValueError):
        return None
    return {"n": name, "d": d}


def overlay_assets(definition) -> list:
    """Every ``asset://`` image a definition depends on, deduped, in order.

    These are the pieces that do NOT travel. An uploaded image lives on the
    SENDER's install; the ref is content-addressed (sha1 of the bytes), so the
    name identifies exactly which file is missing and a recipient who already
    has those bytes resolves it for free. Naming them is what lets an import say
    "this needs two images you do not have" instead of quietly rendering a
    template with holes in it.
    """
    out, seen = [], set()

    def walk(v, depth=OVERLAY_MAX_DEPTH):
        if isinstance(v, str):
            if v.startswith("asset://") and v not in seen:
                seen.add(v)
                out.append(v)
        elif depth > 0 and isinstance(v, list):
            for x in v:
                walk(x, depth - 1)
        elif depth > 0 and isinstance(v, dict):
            for x in v.values():
                walk(x, depth - 1)

    walk(definition)
    return out


def edit_of(payload) -> str | None:
    """The validated edit-target key from a decoded envelope, or None. An
    edit is a normal envelope whose 'ed' names one of the SENDER's own
    earlier messages (the client message key: 'user|timestamp|text', same
    format threads use for 'th'); the envelope's own 't' is the replacement
    text. Soulseek cannot unsend, so vanilla clients keep seeing both
    messages — SoulSync clients fold the edit onto the original and retain
    the history. Author-match and the edit cap are enforced by the client
    fold (every client computes them identically); this only shape-checks
    REMOTE input."""
    e = (payload or {}).get("ed") if isinstance(payload, dict) else None
    if not isinstance(e, str):
        return None
    e = e.strip()[:160]
    if not e or "|" not in e:
        return None
    return e


def reply_of(payload) -> dict | None:
    """The validated reply reference from a decoded envelope, or None.
    Everything here is REMOTE input — strict shape, hard caps."""
    r = (payload or {}).get("r")
    if not isinstance(r, dict):
        return None
    u = str(r.get("u") or "").strip()[:64]
    x = str(r.get("x") or "").strip()[:140]
    if not u:
        return None
    return {"u": u, "x": x}
