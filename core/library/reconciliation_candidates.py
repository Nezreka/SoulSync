"""Internal candidate results and shared pair presentation facts.

Tuple ordering remains part of the ranking contract. NamedTuple keeps that
ordering and compatibility with persisted/test tuple inputs while making new
code explicit about which part of the candidate it uses.
"""
from __future__ import annotations

import os
from typing import NamedTuple


class ScoredCandidate(NamedTuple):
    clear: int
    matched: int
    negative_extras: int
    source: str
    release_id: str
    slots: list[dict]
    plan: dict


def assignment_evidence(file: dict, pair: dict) -> str:
    if pair.get('confidence') == 'manual':
        return 'user'
    if pair.get('confidence') != 'clear':
        return 'conflicting'
    audio = file.get('acoustid') or {}
    if (audio.get('status') == 'ok' and
            any(reason.startswith('AcoustID ') for reason in pair.get('reasons') or [])):
        return 'audio'
    return 'metadata'


def pair_display_fields(file: dict, slot: dict, *, duration_fallback: bool = True,
                        strict_title: bool = True) -> dict:
    """Facts shared by finding evidence, assignments and release previews.

    Preview historically reads duration only from tags; assignment evidence
    also accepts a measured file duration. Keep that choice explicit.
    """
    return {
        'file': os.path.basename(file['path']),
        'file_duration_ms': ((file.get('tags') or {}).get('duration_ms') or
                             (file.get('duration_ms') if duration_fallback else 0) or 0),
        'slot_title': slot['title'] if strict_title else slot.get('title') or '',
        'slot_duration_ms': slot.get('duration_ms') or 0,
        'slot_preview_url': slot.get('preview_url') or '',
    }
