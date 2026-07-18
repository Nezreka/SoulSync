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


def encode(text: str) -> str | None:
    """Wrap message text in a v1 envelope. None when it can't fit the wire
    limit (the caller should tell the user, not silently truncate)."""
    payload = {"v": 1, "t": str(text or "")}
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
