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
