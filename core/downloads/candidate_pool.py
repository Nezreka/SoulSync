"""One download source's search hits, as rows for the candidate inspector.

``evaluate_candidates`` is the judge; this only formats. Accepted rows are what
the inspector offers by default (ranked as before); rejected rows ride along
with the reason they lost, capped so a 400-hit Soulseek search doesn't ship
400 rows nobody opens. The counts always cover everything.
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from core.downloads.decisions import Decision, reject, rejection_counts

REJECTED_ROW_CAP = 50

_DISPLAY_FORMATS = ('FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'WAV')
# Sources whose username IS the service; Soulseek usernames are peers.
_SERVICE_USERNAMES = frozenset({
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr', 'soundcloud', 'amazon',
})


def _audio_label(candidate) -> str:
    try:
        return candidate.audio_quality.label()
    except Exception:  # noqa: BLE001 - stub rows, odd formats
        return ''


def candidate_row(candidate, decision: Decision, *, source_name: str, query: str) -> dict:
    filename = str(getattr(candidate, 'filename', '') or '')
    display_name = os.path.basename(filename.replace('\\', '/'))
    ext = os.path.splitext(display_name)[1].lstrip('.').upper()
    quality = ext if ext in _DISPLAY_FORMATS else (getattr(candidate, 'quality', '') or '')
    username = getattr(candidate, 'username', '') or ''
    service = source_name if source_name != 'default' else 'hybrid'
    if username in _SERVICE_USERNAMES:
        service = username
    score = decision.score if decision.score is not None else getattr(candidate, 'confidence', None)
    size = getattr(candidate, 'size', 0) or 0
    return {
        'username': username,
        'filename': filename,
        'display_name': display_name,
        'size': size,
        'size_display': f"{size / 1048576:.1f} MB",
        'bitrate': getattr(candidate, 'bitrate', 0) or 0,
        'quality': quality,
        'quality_label': _audio_label(candidate),
        'bit_depth': getattr(candidate, 'bit_depth', None),
        'sample_rate': getattr(candidate, 'sample_rate', None),
        'duration': getattr(candidate, 'duration', 0) or 0,
        'artist': getattr(candidate, 'artist', None) or '',
        'title': getattr(candidate, 'title', None) or '',
        'confidence': round(float(score or 0), 3),
        'source_service': service,
        'source_query': query,
        'blacklisted': decision.code == 'blacklisted',
        'free_upload_slots': getattr(candidate, 'free_upload_slots', 0),
        'upload_speed': getattr(candidate, 'upload_speed', 0),
        'queue_length': getattr(candidate, 'queue_length', 0),
        'decision': decision.to_dict(),
    }


def build_source_rows(
    evaluated: Iterable[tuple],
    *,
    source_name: str,
    is_blacklisted: Callable[[str, str], bool],
    reject_cap: int = REJECTED_ROW_CAP,
) -> dict:
    """Turn ``[(query, [(candidate, Decision), ...]), ...]`` into the payload.

    The same file often comes back for both queries; one row per
    (username, filename), and an accepted sighting beats a rejected one.
    A blacklisted file is a rejection whatever validation said, since the
    download worker skips it anyway.
    """
    picked: dict = {}
    order = []
    for query, pairs in evaluated:
        for candidate, decision in pairs:
            key = (getattr(candidate, 'username', ''), getattr(candidate, 'filename', ''))
            held = picked.get(key)
            if held is None:
                order.append(key)
                picked[key] = (candidate, decision, query)
            elif decision.accepted and not held[1].accepted:
                picked[key] = (candidate, decision, query)

    accepted, rejected = [], []
    for key in order:
        candidate, decision, query = picked[key]
        if decision.accepted and _safe_blacklisted(is_blacklisted, *key):
            decision = reject('blacklisted', 'you blacklisted this file', decision.score)
        (accepted if decision.accepted else rejected).append((candidate, decision, query))

    accepted.sort(key=lambda t: -(t[1].score or 0))
    rejected.sort(key=lambda t: float('inf') if t[1].score is None else -t[1].score)
    counts = rejection_counts(d for _, d, _ in rejected)
    shown = rejected[:max(0, reject_cap)]
    return {
        'candidates': [candidate_row(c, d, source_name=source_name, query=q) for c, d, q in accepted],
        'rejected': [candidate_row(c, d, source_name=source_name, query=q) for c, d, q in shown],
        'rejected_total': len(rejected),
        'rejected_counts': counts,
    }


def _safe_blacklisted(is_blacklisted, username, filename) -> bool:
    try:
        return bool(is_blacklisted(username, filename))
    except Exception:  # noqa: BLE001 - a blacklist read must not sink the search
        return False


ALTERNATIVES_KEPT = 10


def _brief(candidate, decision: Decision) -> dict:
    """The few fields a stored decision needs; paths reduced to the file name."""
    username = getattr(candidate, 'username', '') or ''
    source = username if username in _SERVICE_USERNAMES | {'torrent', 'usenet'} else 'soulseek'
    row = candidate_row(candidate, decision, source_name=source, query='')
    return {k: row[k] for k in (
        'username', 'display_name', 'source_service', 'quality', 'quality_label',
        'bitrate', 'size', 'duration', 'confidence', 'decision',
    )}


def summarize_pool(pairs: Iterable[tuple], chosen_key: Optional[tuple] = None,
                   limit: int = ALTERNATIVES_KEPT) -> dict:
    """What an automatic search saw, small enough to store per task.

    ``pairs`` is every (candidate, Decision) the search judged, across
    queries; ``chosen_key`` is the (username, filename) actually taken. The
    alternatives are the best of the rest: accepted runners-up first, then the
    closest rejections. Counts always cover everything.
    """
    picked: dict = {}
    for candidate, decision in pairs:
        key = (getattr(candidate, 'username', ''), getattr(candidate, 'filename', ''))
        held = picked.get(key)
        if held is None or (decision.accepted and not held[1].accepted):
            picked[key] = (candidate, decision)

    chosen = picked.pop(tuple(chosen_key), None) if chosen_key else None
    rest = sorted(
        picked.values(),
        key=lambda t: (not t[1].accepted,
                       float('inf') if t[1].score is None else -t[1].score),
    )
    rejected = [d for _, d in rest if not d.accepted]
    return {
        'chosen': _brief(*chosen) if chosen else None,
        'alternatives': [_brief(c, d) for c, d in rest[:max(0, limit)]],
        'accepted_total': sum(1 for _, d in rest if d.accepted) + (
            1 if chosen and chosen[1].accepted else 0),
        'rejected_total': len(rejected),
        'rejected_counts': rejection_counts(rejected),
    }


def empty_source_rows(error: Optional[str] = None) -> dict:
    out = {'candidates': [], 'rejected': [], 'rejected_total': 0, 'rejected_counts': {}}
    if error:
        out['error'] = error
    return out
