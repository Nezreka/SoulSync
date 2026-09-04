"""#891: the shared 'residual file' classifier — junk + cover/scan images +
lyric/metadata sidecars — used by both the Reorganize cleanup and the Empty
Folder Cleaner, plus the reorganize sweep that uses it.
"""

from __future__ import annotations

from pathlib import Path

from core.library.residual_files import (
    is_disposable,
    is_image,
    is_junk,
    is_sidecar,
)


def test_images_classified():
    for n in ('cover.jpg', 'Cover.JPEG', 'folder.png', 'back.webp', 'scan.tiff', 'art.gif'):
        assert is_image(n) and is_disposable(n)


def test_sidecars_classified():
    for n in ('lyrics.lrc', 'album.nfo', 'disc.cue', 'playlist.m3u', 'x.m3u8'):
        assert is_sidecar(n) and is_disposable(n)


def test_junk_classified():
    assert is_junk('.DS_Store') and is_disposable('Thumbs.db')


def test_real_content_not_disposable():
    # Audio + anything unrecognized (booklet, video, a note) is real content.
    for n in ('song.flac', 'track.mp3', 'booklet.pdf', 'movie.mkv', 'readme.txt', 'data.json'):
        assert not is_disposable(n), n


# The reorganize sweep that used this predicate (`_delete_album_sidecars`) is
# gone. It DELETED an emptied source folder's cover art and sidecars, which was
# safe only because the full-mode reorganize re-created them at the destination
# from the provider. A reorganize moves files now and has no such second half,
# so deleting them would lose them. The predicate stays — it is what any future
# "carry these along" sweep will ask.
