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
    # release-type folders in the singular, which is how soulsync's own
    # Artist/$albumtype/... template names them. a library copied back into
    # staging as A Skylit Drive/Album/[2013] Rise filed every album under an
    # artist called "Album" (discord, SeadogsBooty: mp3/Album/Album/...)
    'album', 'single', 'ep', 'compilation', 'mixtape',
    'live', 'soundtrack', 'soundtracks', 'ost', 'remix', 'remixes',
    'anthology', 'demo', 'demos', 'bootleg', 'bootlegs', 'other',
    'singles & eps', 'eps & singles',
    # download client / arr containers. a torrent client's completed folder
    # mounted inside staging named the artist "qbittorrent" (discord,
    # Tostadaman: /downloads/qbittorrent -> /MUSIC/qbittorrent/...)
    'qbittorrent', 'transmission', 'deluge', 'rtorrent', 'torrents',
    'complete', 'completed', 'incoming', 'sabnzbd', 'nzbget', 'usenet',
})

# what a path template hangs on the artist's folder to tell namesakes apart:
# "Nirvana (UK)", "$uicideboy$ (American hip-hop duo)", "Air [FR]"
_NOTE_SUFFIX = re.compile(r'^(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}))+$')
_UNSAFE = re.compile(r'[<>:"/\\|?*]')


def _fold(name):
    """a name as a folder would carry it: AC/DC is AC_DC on disk."""
    return re.sub(r'\s+', ' ', _UNSAFE.sub('_', str(name or ''))).strip('. ').casefold()


def is_same_artist_folder(folder, artist, same_as=()):
    """True when the folder is this artist's own folder under another
    spelling: the filesystem-safe name, a name the user's template renders,
    or the name with a note in brackets after it (#2045)."""
    folded = _fold(folder)
    names = {_fold(name) for name in (artist, *same_as)} - {''}
    if folded in names:
        return True
    own = _fold(artist)
    return bool(own) and folded.startswith(own) and bool(_NOTE_SUFFIX.match(folded[len(own):]))


_AUDIO_EXT = re.compile(r'\.(flac|mp3|m4a|aac|ogg|opus|wav|aiff?|ape|wma|alac|wv)$', re.I)
_DISC_FOLDER = re.compile(r'^(cd|disc|disk)\s*\d+', re.I)


def resolve_folder_artist(rel_path, identified_artist, enabled,
                          category_names=DEFAULT_CATEGORY_NAMES, same_as=()):
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

    ``same_as`` is every folder name the path templates give the identified
    artist. a folder soulsync named itself is not a second opinion on who the
    artist is: "$albumartist ($albumartistdisambiguation)" put "(American
    hip-hop duo)" in the Album Artist tag of anything that came back through
    staging (#2045).
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

    if folder_artist and not is_same_artist_folder(folder_artist, identified_artist, same_as):
        return folder_artist
    return None
