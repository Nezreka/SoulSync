"""Match a file back to its download-history row when its path has drifted (#934).

``library_history.file_path`` is frozen at import time, but the file moves afterward
(media-server import, library reorganize) and ``tracks.file_path`` — what the AcoustID
scanner reads — no longer equals it. Matching on the exact path alone then fails twice:
the verification status never reaches the history row (verified tracks read "unverified"),
and a fresh ``acoustid_scan`` row gets inserted every run (thousands of duplicates).

This module picks the canonical history row by exact path first, then by FILENAME guarded
by a title check — so a shared filename ("01 - Intro.flac") can never heal the wrong song.
Pure (no DB) so the matching rules are unit-testable; the caller does the SQL.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional, Sequence, Tuple


def _norm_title(value) -> str:
    """Alphanumeric-only lowercase form, so "Song (Remaster)" vs "song remaster"
    style drift between the download tag and the media-server tag still agrees."""
    return ''.join(ch for ch in str(value or '').lower() if ch.isalnum())


def like_filename_filter(basename: str) -> str:
    """A ``LIKE ... ESCAPE '\\'`` pattern that coarsely matches rows whose path ends
    in ``basename``. Escapes the LIKE metacharacters (``%`` ``_`` ``\\``) — filenames
    routinely contain underscores. Callers MUST still confirm with an exact basename
    compare (``pick_history_row`` does), since ``'%name'`` also matches ``'xname'``."""
    esc = basename.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + esc


def pick_history_row(candidates: Sequence[Tuple], *, current_paths: Iterable[str],
                     basename: str, title: str) -> Optional[int]:
    """Return the id of the history row to update for this file, or None.

    ``candidates``: ``(id, file_path, title, download_source)`` rows the DB pre-filtered
    (exact path or filename LIKE). A row matches when its path equals the current path OR
    its filename matches AND its title agrees — the title guard prevents a shared filename
    ("01 - Intro.flac") from healing a different song's row. Among matches a REAL download
    row is preferred over a synthetic ``acoustid_scan`` row, so the scanner heals the
    genuine record and the caller can delete the synthetic duplicate. None when nothing
    matches safely (caller then inserts a fresh row — the "file SoulSync never downloaded"
    intent)."""
    paths = {p for p in current_paths if p}
    want = _norm_title(title)
    matches: list = []  # (id, is_exact, is_real)
    for cid, cpath, ctitle, csource in candidates:
        is_real = csource != 'acoustid_scan'
        if cpath and cpath in paths:
            matches.append((cid, True, is_real))
        elif (basename and cpath and os.path.basename(cpath) == basename
              and (not want or not _norm_title(ctitle) or _norm_title(ctitle) == want)):
            matches.append((cid, False, is_real))
    if not matches:
        return None
    # Prefer a REAL download row over a synthetic acoustid_scan row; within that, prefer an
    # exact-path match over a filename match. Stable, so ties keep DB order (first/oldest id).
    matches.sort(key=lambda m: (m[2], m[1]), reverse=True)
    return matches[0][0]
