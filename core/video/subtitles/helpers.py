"""Generic subtitle helpers: pure parsing plus the filesystem-injected write loop.

Moved verbatim out of the old ``core/video/subtitles.py`` module when it became a
package — behavior is identical, and the package ``__init__`` re-exports these so
existing callers keep working.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable


def parse_langs(raw: Any) -> list:
    """'en, es ; fr' → ['en','es','fr'] (lower-cased, de-duped). Defaults to ['en']."""
    out = []
    for tok in re.split(r"[,\s;]+", str(raw or "")):
        t = tok.strip().lower()
        if t and t not in out:
            out.append(t)
    return out or ["en"]


_DEFAULT_PROVIDER_ORDER = ["opensubtitles"]


def parse_provider_order(raw: Any) -> list:
    """A JSON list string, list, or tuple → cleaned provider ids in order
    (non-empty strings, stripped, de-duped). Non-string entries are dropped;
    garbage (or a missing/empty value) falls back to ``["opensubtitles"]``.
    Never raises."""
    try:
        order = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001 - json.loads can raise RecursionError at
        # extreme nesting; the contract is "never raises"
        order = None
    if not isinstance(order, (list, tuple)):
        return list(_DEFAULT_PROVIDER_ORDER)
    cleaned = []
    for p in order:
        s = p.strip() if isinstance(p, str) else ""
        if s and s not in cleaned:
            cleaned.append(s)
    return cleaned or list(_DEFAULT_PROVIDER_ORDER)


def srt_name(video_path: Any, lang: str) -> str:
    """'<video stem>.<lang>.srt' (no directory).

    PHASE 3 GAP: hi/forced variants currently map to the SAME filename — no
    hi/forced wanted rows are created in Phase 1, so this is inert today, but
    Phase 3 must make filenames distinct (e.g. ``<stem>.<lang>.hi.srt``) before
    any hi/forced fetching lands, or the variants would overwrite each other.
    """
    stem = os.path.splitext(os.path.basename(str(video_path or "")))[0]
    return "%s.%s.srt" % (stem, lang)


def write_subtitles(video_path: str, langs: list, identity: dict, fetch: Callable, fs: Any) -> None:
    """For each language not already present as ``<stem>.<lang>.srt`` next to the video,
    fetch and write it via the injected ``fetch`` + ``fs`` (``list_dir``, ``write_text``).
    Idempotent + best-effort."""
    if not fetch:
        return
    folder = os.path.dirname(str(video_path or ""))
    try:
        existing = {str(n).lower() for n in (fs.list_dir(folder) or [])}
    except Exception:   # noqa: BLE001
        existing = set()
    for lang in (langs or []):
        name = srt_name(video_path, lang)
        if name.lower() in existing:
            continue
        try:
            text = fetch(identity, lang)
            if text:
                fs.write_text(os.path.join(folder, name), text)
        except Exception:   # noqa: BLE001 - a quota miss / network blip is expected, never fatal
            pass
