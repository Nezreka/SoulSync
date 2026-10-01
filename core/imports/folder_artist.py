"""Opt-in "parent folder artist" resolution for imports.

Historically the auto-import worker derived the artist from the top Staging
folder whenever the path had >=2 levels and that folder wasn't a category word
(albums/singles/eps/...). It did so *unconditionally*, overriding even a
confidently metadata-identified artist — which mass-mislabelled files when a
user staged everything under one container folder (see the "soulsync" incident).

This module isolates that decision as a pure function so it can be:
- gated behind an opt-in global setting (``import.folder_artist_override``,
  default on for legacy compatibility), and
- unit-tested without standing up the whole import worker.
"""

import os
import re

# Top-level folder names that denote a *category*, not an artist.
DEFAULT_CATEGORY_NAMES = frozenset({
    'albums', 'singles', 'eps', 'compilations', 'mixtapes',
    'discography', 'music', 'downloads',
    # download client / arr containers. a torrent client's completed folder
    # mounted inside staging named the artist "qbittorrent" (discord,
    # Tostadaman: /downloads/qbittorrent -> /MUSIC/qbittorrent/...)
    'qbittorrent', 'transmission', 'deluge', 'rtorrent', 'torrents',
    'complete', 'completed', 'incoming', 'sabnzbd', 'nzbget', 'usenet',
})

_AUDIO_EXT = re.compile(r'\.(flac|mp3|m4a|aac|ogg|opus|wav|aiff?|ape|wma|alac|wv)$', re.I)
_DISC_FOLDER = re.compile(r'^(cd|disc|disk)\s*\d+', re.I)


def resolve_folder_artist(rel_path, identified_artist, enabled,
                          category_names=DEFAULT_CATEGORY_NAMES):
    """Return the folder-derived artist to use, or ``None`` to keep the
    already-identified artist.

    When ``enabled`` is False this always returns ``None`` — the import keeps
    whatever artist the metadata match produced. Only when explicitly enabled
    does it fall back to the staging folder name, and even then never when the
    folder already equals the identified artist.

    ``rel_path`` is the candidate's path relative to the staging root: the
    album folder (a file path works too, the file is dropped).

    the artist is the folder the album sits IN, not the top folder in staging.
    with Artist/Album that's the same thing, but a client folder mounted deeper
    (qbittorrent/lidarr-done/Artist/Album) used to name the artist after the
    mount. category and container names are skipped on the way up.
    """
    if not enabled:
        return None

    parts = [p for p in rel_path.replace('\\', '/').split('/') if p and p != '.']
    if parts and _AUDIO_EXT.search(parts[-1]):
        parts = parts[:-1]                      # a file, not the album folder
    while parts and _DISC_FOLDER.match(parts[-1]):
        parts = parts[:-1]                      # CD1/ belongs to its album

    folder_artist = None
    # parts[-1] is the album folder; walk up from its parent
    for name in reversed(parts[:-1]):
        if name.lower() in category_names:
            continue
        folder_artist = name
        break

    if folder_artist and folder_artist.lower() != (identified_artist or '').lower():
        return folder_artist
    return None
