"""#891: the shared 'residual file' classifier — junk + cover/scan images +
lyric/metadata sidecars — used by both the Reorganize cleanup and the Empty
Folder Cleaner, plus the reorganize sweep that uses it.
"""

from __future__ import annotations


from core.library.residual_files import (
    is_appledouble,
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


def test_appledouble_classified():
    # AppleDouble sidecars carry a real audio extension — the reason plain
    # extension checks let them through.
    for n in ('._01 - Track.flac', '._cover.jpg', '._.DS_Store'):
        assert is_appledouble(n) and is_disposable(n), n
    # Only the '._' prefix. Other dot-names are somebody's data (a Syncthing
    # '.stfolder', a '.nomedia' marker) and stay real content unless separately junk.
    for n in ('song.flac', 'cover.jpg', 'Artist Name', '.stfolder', '.nomedia'):
        assert not is_appledouble(n), n
    assert not is_disposable('.stfolder') and not is_disposable('.nomedia')


def test_real_content_not_disposable():
    # Audio + anything unrecognized (booklet, video, a note) is real content.
    for n in ('song.flac', 'track.mp3', 'booklet.pdf', 'movie.mkv', 'readme.txt', 'data.json'):
        assert not is_disposable(n), n


