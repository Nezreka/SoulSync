"""Opt-in "parent folder artist" resolution for imports.

Historically the auto-import worker derived the artist from the top Staging
folder whenever the path had >=2 levels and that folder wasn't a category word
(albums/singles/eps/...). It did so *unconditionally*, overriding even a
confidently metadata-identified artist — which mass-mislabelled files when a
user staged everything under one container folder (see the "soulsync" incident).

This module isolates that decision as a pure function so it can be:
- gated behind an opt-in setting (``import.folder_artist_override``,
  default off), and
- unit-tested without standing up the whole import worker.
"""

import os

# Top-level folder names that denote a *category*, not an artist.
DEFAULT_CATEGORY_NAMES = frozenset({
    'albums', 'singles', 'eps', 'compilations', 'mixtapes',
    'discography', 'music', 'downloads',
})


def resolve_folder_artist(rel_path, identified_artist, enabled,
                          category_names=DEFAULT_CATEGORY_NAMES):
    """Return the folder-derived artist to use, or ``None`` to keep the
    already-identified artist.

    When ``enabled`` is False this always returns ``None`` — the import keeps
    whatever artist the metadata match produced. Only when explicitly enabled
    does it fall back to the staging folder name, and even then never when the
    folder already equals the identified artist.

    ``rel_path`` is the candidate's path relative to the staging root.
    """
    if not enabled:
        return None

    parts = [p for p in rel_path.replace('\\', '/').split('/') if p]

    folder_artist = None
    if len(parts) >= 2:
        if len(parts) >= 3 and parts[1].lower() in category_names:
            # Artist/Albums/AlbumFolder -> parts[0] is the artist
            folder_artist = parts[0]
        elif parts[0].lower() not in category_names:
            # Artist/AlbumFolder -> parts[0] is the artist
            folder_artist = parts[0]

    if folder_artist and folder_artist.lower() != (identified_artist or '').lower():
        return folder_artist
    return None
