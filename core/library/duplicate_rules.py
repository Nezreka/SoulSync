"""Shared rules for distinguishing duplicates from intentional lossy copies.

Both the catalogue duplicate detector and the filesystem duplicate cleaner
compare files with the same stem.  Keeping this exception in one place avoids
one tool protecting a configured lossy companion while the other quarantines
that exact same file.
"""

from __future__ import annotations

import os
from typing import Any

from core.quality.lossless import is_lossless_format
from core.quality.source_map import format_from_extension


LOSSY_CODEC_EXTS = {"mp3": ".mp3", "opus": ".opus", "aac": ".m4a"}


def lossy_companion_exts(config_manager: Any = None, database: Any = None,
                         logger: Any = None) -> set[str]:
    """Extensions intentionally written by any enabled lossy-copy setting.

    The global setting and every enabled quality profile count.  Failures are
    deliberately non-fatal: an unreadable setting must not make duplicate
    detection itself fail, and an empty result preserves the historical
    behaviour of treating same-stem cross-format files as duplicates.
    """
    exts: set[str] = set()
    try:
        if config_manager and config_manager.get("lossy_copy.enabled", False):
            codec = str(config_manager.get("lossy_copy.codec", "mp3")).lower()
            exts.add(LOSSY_CODEC_EXTS.get(codec, ".mp3"))
    except Exception as exc:  # noqa: BLE001 - optional protection setting
        if logger:
            logger.debug("lossy companion config read failed: %s", exc)

    if database is None:
        return exts
    try:
        conn = database._get_connection()
        try:
            rows = conn.execute(
                "SELECT lossy_copy_codec FROM quality_profiles "
                "WHERE lossy_copy_enabled = 1"
            ).fetchall()
            for (codec,) in rows:
                exts.add(LOSSY_CODEC_EXTS.get(str(codec or "mp3").lower(), ".mp3"))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - old/missing schema is valid
        if logger:
            logger.debug("lossy companion profile read failed: %s", exc)
    return exts


def is_lossy_companion_pair(path1: Any, path2: Any,
                            companion_exts: set[str] | frozenset[str]) -> bool:
    """Whether two paths are one lossless source and its configured copy."""
    if not companion_exts:
        return False
    p1 = str(path1 or "").replace("\\", "/")
    p2 = str(path2 or "").replace("\\", "/")
    d1, b1 = os.path.split(p1)
    d2, b2 = os.path.split(p2)
    if d1.lower() != d2.lower():
        return False
    s1, e1 = os.path.splitext(b1)
    s2, e2 = os.path.splitext(b2)
    if s1.lower() != s2.lower():
        return False
    lossless1 = is_lossless_format(format_from_extension(e1.lstrip(".").lower()))
    lossless2 = is_lossless_format(format_from_extension(e2.lstrip(".").lower()))
    if lossless1 == lossless2:
        return False
    lossy_ext = e2 if lossless1 else e1
    return lossy_ext.lower() in companion_exts
