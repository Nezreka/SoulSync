"""Hand-tagged ("tag it yourself") downloads.

the user typed the metadata because no service knows the release: a bootleg,
a live set, a mixtape. everything automatic that would "correct" it has to
stand down:

- no source-id lookups. they match by name and would embed a studio release's
  ids and overwrite the user's date with a musicbrainz one
- no acoustid gate. a live recording fails the studio fingerprint by design
- cover art is the user's image or nothing. the preferred-art lookup would
  find the studio cover by artist + album name

the flag rides on the task's track_info (`_manual_metadata`), which every
post-processing context carries, whichever route the download took.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("metadata.manual")

MANUAL_TAG = 'SOULSYNC_MANUAL'


def is_manual_context(context: Any) -> bool:
    if not isinstance(context, dict):
        return False
    if context.get('_manual_metadata'):
        return True
    info = context.get('track_info')
    return isinstance(info, dict) and bool(info.get('_manual_metadata'))


def manual_cover_path(context: Any) -> Optional[str]:
    """a cover the user uploaded, saved on disk when the download started"""
    if not isinstance(context, dict):
        return None
    info = context.get('track_info') if isinstance(context.get('track_info'), dict) else {}
    path = context.get('_manual_cover_path') or info.get('_manual_cover_path')
    return path if path and os.path.isfile(path) else None


def read_manual_cover(path: Optional[str]):
    """(bytes, mime) from an uploaded cover, or (None, None)"""
    if not path:
        return None, None
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError as exc:
        logger.warning("[Manual] cover %s unreadable: %s", path, exc)
        return None, None
    lower = path.lower()
    mime = 'image/png' if lower.endswith('.png') else 'image/webp' if lower.endswith('.webp') else 'image/jpeg'
    return (data or None), (mime if data else None)


def write_manual_marker(audio_file, symbols) -> None:
    """SOULSYNC_MANUAL=1 in the file, so the marker survives a database rebuild"""
    try:
        if isinstance(audio_file.tags, symbols.ID3):
            audio_file.tags.add(symbols.TXXX(encoding=3, desc=MANUAL_TAG, text=['1']))
        elif isinstance(audio_file, symbols.MP4):
            audio_file[f'----:com.apple.iTunes:{MANUAL_TAG}'] = [b'1']
        elif audio_file.tags is not None:
            audio_file[MANUAL_TAG.lower()] = ['1']
    except Exception as exc:  # noqa: BLE001 - the db lock still holds without it
        logger.debug("[Manual] could not write the manual marker: %s", exc)
