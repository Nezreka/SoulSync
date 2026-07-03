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


# ── the reorganize sweep that uses the predicate ──────────────────────────────
def test_delete_album_sidecars_sweeps_all_residual_keeps_real(tmp_path: Path):
    from core.library_reorganize import _delete_album_sidecars

    d = tmp_path / 'Old Album'
    d.mkdir()
    for n in ('cover.jpg', 'back.jpg', 'disc.png', 'lyrics.lrc', 'album.nfo', '.DS_Store'):
        (d / n).write_text('x')
    (d / 'booklet.pdf').write_text('keep')      # unrecognized → must survive

    _delete_album_sidecars(str(d))

    survivors = {p.name for p in d.iterdir()}
    assert survivors == {'booklet.pdf'}          # every residual swept, booklet kept
