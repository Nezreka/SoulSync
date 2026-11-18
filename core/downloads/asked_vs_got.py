"""which side of a library_history row is the ask and which is the file.

record_library_history_download writes ``title`` / ``artist_name`` from the
task's track_info (the wishlist, playlist or search target the download ran
for) and ``source_track_title`` / ``source_artist`` from the candidate the
source handed over (slskd's parsed filename, a stream's own title). the
review queue and the download detail sheet read them the other way round, so
"asked for" showed the file and "got" showed the ask. every reader goes
through here now.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def history_asked_and_got(entry: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(asked, got) for one library_history row, each ``{'title', 'artist'}``.

    a side the row never recorded comes back as empty strings, never None.
    """
    entry = entry or {}
    asked = {
        'title': _text(entry.get('title')),
        'artist': _text(entry.get('artist_name')),
    }
    got = {
        'title': _text(entry.get('source_track_title')),
        'artist': _text(entry.get('source_artist')),
    }
    return asked, got
