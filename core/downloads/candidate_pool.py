"""One download source's search hits, as rows for the candidate inspector.

``evaluate_candidates`` is the judge; this only formats. Accepted rows are what
the inspector offers by default (ranked as before); rejected rows ride along
with the reason they lost, capped so a 400-hit Soulseek search doesn't ship
400 rows nobody opens. The counts always cover everything.
"""

from __future__ import annotations

import os
from copy import copy
from typing import Any, Callable, Iterable, Optional

from core.downloads.decisions import Decision, reject, rejection_counts

REJECTED_ROW_CAP = 50

_DISPLAY_FORMATS = ('FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'WAV')
# Sources whose username IS the service; Soulseek usernames are peers.
_SERVICE_USERNAMES = frozenset({
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr', 'soundcloud', 'amazon',
})
# Every source whose filename is an `id||name` dispatch key rather than a
# path — the eight above plus the two release sources, whose token is an
# opaque candidate-store id that must never be displayed either.
DECODE_USERNAMES = _SERVICE_USERNAMES | frozenset({'torrent', 'usenet'})


def _audio_label(candidate) -> str:
    try:
        return candidate.audio_quality.label()
    except Exception:  # noqa: BLE001 - stub rows, odd formats
        return ''


def display_name_for(filename: str, username: str = '') -> str:
    """The human name a candidate row should show for ``filename``.

    Streaming sources don't hand back paths — their filename is a dispatch
    key, ``id||name`` (SoundCloud: ``id||url||name``, because its worker
    needs the permalink). Basename-splitting a key showed the inspector the
    raw id and, for SoundCloud, the permalink slug glued to the title with
    the separator still in it (#1911) — the returned songs read as machine
    IDs, not songs. The shape recognition matches the completed-file
    finder's (file_finder._encoded_display_name, #1239/#835) with one
    deliberate difference: a display name keeps everything after the
    second separator, where the finder's on-disk search target takes the
    last segment only. A key with extra segments and a non-URL middle
    reads everything after the first separator as the name. The URL-middle
    check applies to every dispatch-key source, not just SoundCloud: a
    middle segment that is a URL is a permalink, never part of a title.
    It only applies to dispatch-key usernames, whose filenames are always
    dispatch keys; a Soulseek peer's real remote path keeps the basename
    rule, ``||`` in a folder name and all.
    """
    filename = str(filename or '')
    if username in DECODE_USERNAMES and '||' in filename:
        parts = filename.split('||')
        if len(parts) > 2 and parts[1].startswith(('http://', 'https://')):
            # SoundCloud's three parts, split the way its own worker splits
            # them (maxsplit=2): a title containing '||' survives whole.
            name = filename.split('||', 2)[2]
        else:
            name = filename.split('||', 1)[1]
        if name.strip():
            return name
        # The key encodes no name at all: show the key, not a fragment of
        # it (the basename rule would carve a slug out of the URL part).
        return filename
    return os.path.basename(filename.replace('\\', '/'))


def candidate_row(candidate, decision: Decision, *, source_name: str, query: str) -> dict:
    filename = str(getattr(candidate, 'filename', '') or '')
    username = getattr(candidate, 'username', '') or ''
    display_name = display_name_for(filename, username)
    # A decoded dispatch key's "name" is a title, not a file name: a dot in
    # it ('Artist - Song.Mp3') must not override the candidate's own format.
    if username in DECODE_USERNAMES and '||' in filename:
        quality = getattr(candidate, 'quality', '') or ''
    else:
        ext = os.path.splitext(display_name)[1].lstrip('.').upper()
        quality = ext if ext in _DISPLAY_FORMATS else (getattr(candidate, 'quality', '') or '')
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



def _collapse_release_pairs(pairs):
    from core.download_plugins.release_identity import dedupe_release_candidates
    by_endpoint = {(item[0].username, item[0].filename): item for item in pairs}
    return [(c, *by_endpoint[(c.username, c.filename)][1:])
            for c in dedupe_release_candidates([item[0] for item in pairs])]


def build_source_rows(
    evaluated: Iterable[tuple],
    *,
    source_name: str,
    is_blacklisted: Callable[[str, str], bool],
    is_failed_blocked: Optional[Callable[[Any], bool]] = None,
    reject_cap: int = REJECTED_ROW_CAP,
    provenance: Optional[dict] = None,
    policy: Optional[dict] = None,
    policy_profile: Optional[dict] = None,
) -> dict:
    """Turn ``[(query, [(candidate, Decision), ...]), ...]`` into the payload.

    The same file often comes back for both queries; one row per
    (username, filename), and an accepted sighting beats a rejected one.
    A blacklisted file is a rejection whatever validation said, since the
    download worker skips it anyway. A failed-blocklisted file (terminal
    import give-up, core/downloads/failed_blocklist.py) is rejected the same
    way, through the existing 'blacklisted' code.

    ``provenance``/``policy`` (core/downloads/provenance.py) ride along when
    given, so the UI can say which search produced these rows and under what
    ladder. ``policy_profile`` (a quality-profile dict) additionally stamps
    every row with its own candidate policy facet — the rung that candidate
    reached on the ladder — while the run-level ``policy`` keeps describing
    the ladder in effect.
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
        elif (decision.accepted and is_failed_blocked is not None
                and _safe_failed_blocked(is_failed_blocked, candidate)):
            decision = reject('blacklisted', 'this file failed import before', decision.score)
        (accepted if decision.accepted else rejected).append((candidate, decision, query))

    accepted.sort(key=lambda t: -(t[1].score or 0))
    accepted = _collapse_release_pairs(accepted)
    rejected.sort(key=lambda t: float('inf') if t[1].score is None else -t[1].score)
    counts = rejection_counts(d for _, d, _ in rejected)
    shown = rejected[:max(0, reject_cap)]

    def _row(c, d, q):
        row = candidate_row(c, d, source_name=source_name, query=q)
        if policy_profile is not None:
            from core.downloads.provenance import build_policy_facet
            row['policy'] = build_policy_facet(policy_profile, c)
        return row

    out = {
        'candidates': [_row(c, d, q) for c, d, q in accepted],
        'rejected': [_row(c, d, q) for c, d, q in shown],
        'rejected_total': len(rejected),
        'rejected_counts': counts,
    }
    if provenance is not None:
        out['provenance'] = provenance
    if policy is not None:
        out['policy'] = policy
    return out


def _safe_blacklisted(is_blacklisted, username, filename) -> bool:
    try:
        return bool(is_blacklisted(username, filename))
    except Exception:  # noqa: BLE001 - a blacklist read must not sink the search
        return False


def _safe_failed_blocked(is_failed_blocked, candidate) -> bool:
    try:
        return bool(is_failed_blocked(candidate))
    except Exception:  # noqa: BLE001 - a blocklist read must not sink the search
        return False


ALTERNATIVES_KEPT = 10


def _brief(candidate, decision: Decision) -> dict:
    """The few fields a stored decision needs.

    ``filename`` rides along in full — a peer's whole remote path, a
    service's whole dispatch key — as the row's identity: for a service
    source the username is a constant and several hits can share one
    decoded display name (same song, different uploads), so (username,
    display_name) alone cannot tell two stored rows apart (#1911 review).
    It is persisted with the decision record and reaches the track-detail
    payload verbatim; nothing renders it, it is identity only.
    """
    username = getattr(candidate, 'username', '') or ''
    source = username if username in DECODE_USERNAMES else 'soulseek'
    row = candidate_row(candidate, decision, source_name=source, query='')
    return {k: row[k] for k in (
        'username', 'filename', 'display_name', 'source_service', 'quality', 'quality_label',
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
    if chosen is None and chosen_key:
        from core.download_plugins.release_identity import release_sources
        for root_key, (root, decision) in list(picked.items()):
            endpoint = next((s for s in release_sources(root)
                             if (s.username, s.filename) == tuple(chosen_key)), None)
            if endpoint is not None:
                # The root's validated metadata/quality belongs to this release;
                # the selected endpoint supplies its opaque download token.
                selected = copy(root)
                selected.username, selected.filename = endpoint.username, endpoint.filename
                chosen = (selected, decision)
                picked.pop(root_key)
                break
    rest = sorted(
        picked.values(),
        key=lambda t: (not t[1].accepted,
                       float('inf') if t[1].score is None else -t[1].score),
    )
    grouped = _collapse_release_pairs(([chosen] if chosen else []) + rest)
    if chosen:
        chosen, rest = grouped[0], grouped[1:]
    else:
        rest = grouped
    rejected = [d for _, d in rest if not d.accepted]
    return {
        'chosen': _brief(*chosen) if chosen else None,
        'alternatives': [_brief(c, d) for c, d in rest[:max(0, limit)]],
        'accepted_total': sum(1 for _, d in rest if d.accepted) + (
            1 if chosen and chosen[1].accepted else 0),
        'rejected_total': len(rejected),
        'rejected_counts': rejection_counts(rejected),
    }


def empty_source_rows(error: Optional[str] = None, provenance: Optional[dict] = None,
                      policy: Optional[dict] = None) -> dict:
    out = {'candidates': [], 'rejected': [], 'rejected_total': 0, 'rejected_counts': {}}
    if error:
        out['error'] = error
    if provenance is not None:
        out['provenance'] = provenance
    if policy is not None:
        out['policy'] = policy
    return out
