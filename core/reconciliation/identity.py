"""Stable, conservative grouping for maintenance review cases.

An album title is display metadata, not identity: editions and unrelated files
can share it. Folder audits use the physical folder plus the local album row;
legacy album findings use the local album row when no explicit folder exists.
Findings without either stay separate instead of grouping by a bad tag.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping


def review_case_key(details: Mapping[str, Any] | None, file_path: str | None,
                    finding_id: int | None = None) -> str:
    details = details if isinstance(details, Mapping) else {}
    profile = str(details.get('profile_id') or 1)
    album_id = details.get('album_id')
    folder = details.get('album_folder') or details.get('folder_path')
    if isinstance(folder, str) and folder.strip():
        normalized = os.path.normcase(os.path.normpath(folder.strip()))
        digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]
        return f'folder:{profile}:{album_id if album_id not in (None, "") else "unassigned"}:{digest}'
    if album_id not in (None, ''):
        return f'album:{profile}:{album_id}'

    if file_path:
        folder = os.path.dirname(file_path)
    if isinstance(folder, str) and folder.strip():
        normalized = os.path.normcase(os.path.normpath(folder.strip()))
        digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]
        return f'folder:{profile}:{digest}'

    if finding_id is None:
        # Before INSERT the identity is unknown. The worker fills it afterwards.
        return ''
    return f'finding:{finding_id}'


def missing_slot_key(album_id: object, source: str, release_id: str,
                     slot: Mapping[str, Any]) -> str:
    """Stable identity even when a release repeats a disc/track number."""
    identity = (slot.get('source_track_id') or slot.get('name') or
                slot.get('title') or '')
    raw = f'{album_id}|{source}|{release_id}|{slot.get("disc_number")}|{slot.get("track_number")}|{identity}'
    return hashlib.sha256(raw.encode()).hexdigest()
