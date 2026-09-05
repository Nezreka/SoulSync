"""A stored path is a technical value; the table shows a place in the library.

``lib2_track_files.path`` holds whatever wrote it, and the same folder is
written three ways across the codebase — ``./Transfer/…`` from the album path
builder, ``Transfer/…`` from the simple one (``Path()`` swallows the ``./``)
and ``/app/Transfer/…`` from the repair scan's ``realpath``. Showing that
column raw meant every row led with a root the user configured themselves and
cannot act on, and the three spellings made the leading noise inconsistent
between rows of the SAME album.

``library_relative_path`` answers "where in the library" and leaves the full
path to the tooltip and the copy button. It must:

* strip all three spellings of the same root,
* prefer the longest matching root, so a nested one does not win,
* leave a path it does not recognise completely alone — a half-stripped path
  is worse than an honest one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.library2.paths import library_relative_path


class _Config:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.mark.parametrize('stored', [
    './Transfer/Sawano Hiroyuki/Attack on Titan/Disc 1/02 - Apetitan.flac',
    'Transfer/Sawano Hiroyuki/Attack on Titan/Disc 1/02 - Apetitan.flac',
])
def test_the_relative_spellings_of_one_root_all_strip(stored, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _Config(**{'soulseek.transfer_path': './Transfer'})

    assert library_relative_path(stored, cfg) == (
        'Sawano Hiroyuki/Attack on Titan/Disc 1/02 - Apetitan.flac')


def test_the_absolute_spelling_of_the_same_root_strips_too(tmp_path, monkeypatch):
    """The repair scan stores ``realpath`` output, so the same file can be in
    the table twice over with two different prefixes."""
    monkeypatch.chdir(tmp_path)
    cfg = _Config(**{'soulseek.transfer_path': './Transfer'})
    stored = str(tmp_path / 'Transfer' / 'AC_DC' / 'Back In Black' / '02 - Shoot to Thrill.flac')

    assert library_relative_path(stored, cfg) == (
        'AC_DC/Back In Black/02 - Shoot to Thrill.flac')


def test_a_declared_music_path_is_a_root_too(tmp_path):
    cfg = _Config(**{'library.music_paths': [str(tmp_path / 'music')]})
    stored = str(tmp_path / 'music' / 'Artist' / 'Album' / '01 - Song.flac')

    assert library_relative_path(stored, cfg) == 'Artist/Album/01 - Song.flac'


def test_the_longest_matching_root_wins(tmp_path):
    """A root nested inside another must not leave the deeper folder name
    dangling at the front of every one of its rows."""
    cfg = _Config(**{
        'soulseek.transfer_path': str(tmp_path / 'media'),
        'library.music_paths': [str(tmp_path / 'media' / 'lossless')],
    })
    stored = str(tmp_path / 'media' / 'lossless' / 'Artist' / 'Album' / '01 - Song.flac')

    assert library_relative_path(stored, cfg) == 'Artist/Album/01 - Song.flac'


def test_a_path_under_no_known_root_is_left_alone(tmp_path):
    """Half-stripping a media-server path would invent a location. Show it
    whole and let the tooltip and the copy button do their job."""
    cfg = _Config(**{'soulseek.transfer_path': str(tmp_path / 'Transfer')})

    assert library_relative_path('/music/Artist/Album/01 - Song.flac', cfg) == (
        '/music/Artist/Album/01 - Song.flac')


def test_the_root_itself_is_not_stripped_to_nothing(tmp_path):
    cfg = _Config(**{'soulseek.transfer_path': str(tmp_path / 'Transfer')})
    root = str(tmp_path / 'Transfer')

    assert library_relative_path(root, cfg) == root


@pytest.mark.parametrize('value', [None, '', 123])
def test_a_non_path_survives_unchanged(value, tmp_path):
    cfg = _Config(**{'soulseek.transfer_path': str(tmp_path / 'Transfer')})

    assert library_relative_path(value, cfg) == value


def test_a_windows_separator_is_matched_and_reported_with_slashes(tmp_path):
    cfg = _Config(**{'library.music_paths': [str(tmp_path / 'music')]})
    stored = os.path.join(str(tmp_path / 'music'), 'Artist', 'Album', '01.flac')

    assert library_relative_path(stored.replace('/', os.sep), cfg) == 'Artist/Album/01.flac'


def test_the_track_payload_carries_both_the_stored_and_the_shown_path(
        imported_conn, tmp_path, monkeypatch):
    """The table needs the short form and the tooltip needs the long one, so
    the payload has to carry both — dropping `path` would break every consumer
    that opens, plays or deletes the file."""
    from core.library2 import paths as lib2_paths
    from core.library2.queries import get_album

    conn = imported_conn
    row = conn.execute(
        "SELECT id, track_id, path FROM lib2_track_files LIMIT 1").fetchone()
    if row is None:
        pytest.skip('fixture catalogue has no files')
    stored = str(tmp_path / 'Transfer' / 'Artist' / 'Album' / '01 - Song.flac')
    conn.execute("UPDATE lib2_track_files SET path = ? WHERE id = ?",
                 (stored, row['id']))
    album_id = conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id = ?", (row['track_id'],)
    ).fetchone()['album_id']
    conn.commit()

    monkeypatch.setattr(lib2_paths, '_DISPLAY_PREFIX_MEMO', {})
    monkeypatch.setattr(
        lib2_paths, '_display_root_prefixes',
        lambda config_manager=None: [str(tmp_path / 'Transfer')])

    album = get_album(conn, album_id)
    track = next(t for t in album['tracks'] if t.get('file'))

    assert track['file']['path'] == stored
    assert track['file']['display_path'] == 'Artist/Album/01 - Song.flac'
