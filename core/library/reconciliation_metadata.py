"""Metadata evidence independent of the selected release provider."""
from __future__ import annotations

from collections import Counter
from uuid import UUID

from core.library.reconciliation_plan import _recording_supports_slot


def confirmed_recording(file: dict, slot: dict, source: str) -> dict:
    """Use an exact MB slot or a unique, strongly supported recording identity.

    AcoustID links are unordered. Multiple supported IDs are not permission to
    replace one with whichever the service returned first.
    """
    audio = file.get('acoustid') or {}
    if audio.get('status') != 'ok':
        return {}
    strong = [row for row in audio.get('recordings') or []
              if float(row.get('score') or 0) >= .90 and row.get('mbid')]
    if source == 'musicbrainz':
        return next((row for row in strong if row['mbid'] == slot.get('recording_mbid')), {})
    local = (file.get('tags') or {}).get('duration_ms')
    remote = slot.get('duration_ms')
    if not local or not remote or abs(float(local) - float(remote)) > 3000:
        return {}
    if (not strong or not slot.get('artist') or
            not all(row.get('artist') and _recording_supports_slot(row, slot) for row in strong)):
        return {}
    identities = {str(row['mbid']) for row in strong}
    if len(identities) != 1:
        return {}
    try:
        UUID(next(iter(identities)))
    except ValueError:
        return {}
    return strong[0]


def album_consistency(files: list[dict]) -> dict:
    """Describe differing embedded values across the logical album."""
    conflicts = {}
    for field in ('album', 'musicbrainz_release_id'):
        values = Counter(str((file.get('tags') or {}).get(field) or '').strip()
                         for file in files)
        if len(values) > 1:
            conflicts[field] = [{'value': value, 'files': count}
                                for value, count in sorted(values.items())]
    return conflicts


def recording_id_conflict(file: dict, slot: dict, client, cache: dict) -> dict:
    """Recheck an unsupported embedded ID before offering its removal.

    Missing responses and catalogue aliases are not proof of an invalid ID.
    The caller keeps this a manual decision and never changes the release.
    """
    mbid = (file.get('tags') or {}).get('mbid')
    if not mbid or not client:
        return {}
    try:
        UUID(str(mbid))
    except ValueError:
        return {}
    audio = file.get('acoustid') or {}
    if any(row.get('mbid') == mbid and float(row.get('score') or 0) >= .9
           for row in audio.get('recordings') or []):
        return {}
    if mbid not in cache:
        try:
            cache[mbid] = client.get_recording(mbid, includes=['artist-credits']) or {}
        except Exception:
            cache[mbid] = {}
    raw = cache[mbid]
    if not raw.get('title') or not raw.get('id'):
        return {}
    artist = ' & '.join(str(row.get('name') or (row.get('artist') or {}).get('name') or '')
                        for row in raw.get('artist-credit') or [] if isinstance(row, dict))
    recording = {'mbid': raw['id'], 'title': raw['title'], 'artist': artist}
    if _recording_supports_slot(recording, slot):
        return {}
    return {'mbid': mbid, 'title': raw['title'], 'artist': artist,
            'reason': 'The embedded recording ID describes a different catalogue recording; confirm removing it or keep the current value'}
