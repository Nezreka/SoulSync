"""Which download source a review-queue delete should blacklist (#1801).

deleting a wrong file in review used to only delete it. the track went back
on the wishlist and the next run picked the very same file off the very same
source, so kevin2xk deleted the same six songs sync after sync.

the download worker already skips a blacklisted (username, filename), so a
reject only has to land there with the exact pair the candidate carries.
the history row keeps the filename but only a label for the source
("Soulseek", "YouTube"), so the peer comes from the provenance row recorded
for the same file. Pure, no I/O.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.downloads.live_detail import SOURCE_LABELS

# sources that were never a download, nothing to skip next time
_NOT_DOWNLOADS = frozenset({'staging', 'auto_import', 'torrent', 'usenet', 'podcast'})


def rejected_source(history: Dict[str, Any],
                    provenance: Optional[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """``(username, filename)`` to blacklist for a rejected review row, or None.

    ``provenance`` is the track_downloads row for the same file path. it is
    only trusted when its filename is the history row's filename, so a row
    from some other download of that path can't name the wrong source.
    without it a streaming source still works off the label, a soulseek peer
    does not (the label never names the peer).
    """
    filename = str((history or {}).get('source_filename') or '').strip()
    if not filename:
        return None
    prov = provenance or {}
    prov_user = str(prov.get('source_username') or '').strip()
    if prov_user and str(prov.get('source_filename') or '').strip() == filename:
        if prov_user in _NOT_DOWNLOADS:
            return None
        return prov_user, filename
    label = str((history or {}).get('download_source') or '').strip()
    for username, shown in SOURCE_LABELS.items():
        if shown == label:
            return (None if username in _NOT_DOWNLOADS else (username, filename))
    return None
