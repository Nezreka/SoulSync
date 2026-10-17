"""The smart-skip fingerprint stored in a playlist's sync status.

A scheduled sync skips a playlist whose track hashes are unchanged and that
was fully matched last time. Anything that must force the next run to really
sync (an authoritative Quality Profile change, a write the server refused)
drops these keys and keeps the rest of the status.
"""

FINGERPRINT_KEYS = ('tracks_hash', 'mirror_tracks_hash', 'matched_tracks')


def clear_fingerprint(sync_statuses: dict, sync_id: str) -> bool:
    """Drop ``sync_id``'s fingerprint in place. True when anything changed,
    i.e. the statuses need saving."""
    status = sync_statuses.get(sync_id)
    if not status:
        return False
    removed = [status.pop(key) for key in FINGERPRINT_KEYS if key in status]
    return bool(removed)
