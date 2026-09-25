"""tag it yourself: what the user typed becomes the tasks, and everything
automatic that would "correct" it stands down."""

from __future__ import annotations

import base64

import pytest

from core.metadata import manual
from core.search import manual_download as md


def _key(f):
    return f"{f['username']}::{f['filename']}"


FILES = [
    {'username': 'deadair', 'filename': r'Glasto\01 - a.flac', 'duration': 199_000},
    {'username': 'deadair', 'filename': r'Glasto\02 - b.flac', 'duration': 259_000},
]
ALBUM = {'name': ' Live at  Glastonbury 2003 ', 'artist': 'Radiohead', 'date': '2003-06-28',
         'genre': 'Alternative Rock', 'type': 'live'}
TRACKS = [
    {'file_key': r'deadair::Glasto\01 - a.flac', 'title': '2 + 2 = 5', 'track_number': 1, 'disc_number': 1},
    {'file_key': r'deadair::Glasto\02 - b.flac', 'title': 'Lucky', 'track_number': 2, 'disc_number': 1},
]


class TestBuildManualTasks:
    def test_tasks_carry_exactly_what_the_user_typed(self):
        album, tasks = md.build_manual_tasks(FILES, ALBUM, TRACKS, file_key=_key)
        assert album['name'] == 'Live at Glastonbury 2003'   # whitespace tidied, words kept
        assert album['release_date'] == '2003-06-28'
        assert album['album_type'] == 'album'                  # live is an album that's live
        assert album['total_tracks'] == 2
        info = tasks[1][1]
        assert info['name'] == 'Lucky' and info['track_number'] == 2
        assert info['_manual_metadata'] is True
        assert info['_is_explicit_album_download'] is True
        assert info['_explicit_artist_context']['genres'] == ['Alternative Rock']
        assert info['duration_ms'] == 259_000

    def test_no_ids_and_no_source_anywhere(self):
        # a fake id or a source would get embedded as a real provider id
        album, tasks = md.build_manual_tasks(FILES, ALBUM, TRACKS, file_key=_key)
        assert album['id'] == '' and 'source' not in album
        for _file, info in tasks:
            assert info['id'] == '' and 'source' not in info

    def test_counts_discs(self):
        tracks = [dict(TRACKS[0]), dict(TRACKS[1], disc_number=2)]
        album, _ = md.build_manual_tasks(FILES, ALBUM, tracks, file_key=_key)
        assert album['total_discs'] == 2

    @pytest.mark.parametrize('patch, message', [
        ({'name': '  '}, 'Give the album a name.'),
        ({'artist': ''}, 'Who made it?'),
        ({'image_url': 'file:///etc/passwd'}, 'http://'),
    ])
    def test_refuses_with_a_sentence(self, patch, message):
        with pytest.raises(md.ManualInputError, match=message):
            md.build_manual_tasks(FILES, {**ALBUM, **patch}, TRACKS, file_key=_key)

    def test_a_track_without_a_title_is_refused(self):
        with pytest.raises(md.ManualInputError, match='Every track needs a title'):
            md.build_manual_tasks(FILES, ALBUM, [dict(TRACKS[0], title=' ')], file_key=_key)

    def test_rows_for_files_that_were_not_picked_are_ignored(self):
        rows = TRACKS + [{'file_key': 'someone::else.flac', 'title': 'x'}]
        _album, tasks = md.build_manual_tasks(FILES, ALBUM, rows, file_key=_key)
        assert len(tasks) == 2


class TestCover:
    def test_an_uploaded_cover_lands_on_disk(self, tmp_path):
        data = b'\xff\xd8\xff' + b'x' * 100
        url = 'data:image/jpeg;base64,' + base64.b64encode(data).decode()
        path = md.save_cover(url, directory=str(tmp_path))
        assert path.endswith('.jpg')
        assert open(path, 'rb').read() == data

    def test_not_an_image_is_refused(self, tmp_path):
        with pytest.raises(md.ManualInputError):
            md.save_cover('data:text/html;base64,PGh0bWw+', directory=str(tmp_path))

    def test_too_big_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, 'MAX_COVER_BYTES', 10)
        url = 'data:image/png;base64,' + base64.b64encode(b'x' * 50).decode()
        with pytest.raises(md.ManualInputError, match='12 MB'):
            md.save_cover(url, directory=str(tmp_path))

    def test_the_cover_path_rides_on_every_task(self, tmp_path):
        _album, tasks = md.build_manual_tasks(FILES, ALBUM, TRACKS, cover_path='/c.jpg', file_key=_key)
        assert all(info['_manual_cover_path'] == '/c.jpg' for _f, info in tasks)


class TestManualContext:
    def test_the_flag_is_read_off_track_info(self):
        assert manual.is_manual_context({'track_info': {'_manual_metadata': True}})
        assert manual.is_manual_context({'_manual_metadata': True})
        assert not manual.is_manual_context({'track_info': {'name': 'x'}})
        assert not manual.is_manual_context(None)

    def test_acoustid_stands_down_for_manual_only(self):
        from core.imports.pipeline import _should_skip_quarantine_check
        assert _should_skip_quarantine_check({'track_info': {'_manual_metadata': True}}, 'acoustid')
        assert not _should_skip_quarantine_check({'track_info': {}}, 'acoustid')
        # only acoustid: integrity and quality still check the file itself
        assert not _should_skip_quarantine_check({'track_info': {'_manual_metadata': True}}, 'integrity')

    def test_cover_path_must_exist(self, tmp_path):
        real = tmp_path / 'c.jpg'
        real.write_bytes(b'x')
        assert manual.manual_cover_path({'track_info': {'_manual_cover_path': str(real)}}) == str(real)
        assert manual.manual_cover_path({'track_info': {'_manual_cover_path': str(tmp_path / 'gone.jpg')}}) is None
