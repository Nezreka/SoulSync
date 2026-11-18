"""in-app chat (makiel): "mixup when a song stays in review, 'asked for' and
'got' are reversed".

the import writes the ask (track_info: the wishlist, playlist or search
target) into library_history.title/artist_name and the candidate the source
handed over into source_track_title/source_artist. the review queue row, the
download detail sheet and wrong song all read those the other way round.

these run the real writer and feed its row to every reader, with the two
sides deliberately different, so a swap on either end shows up.
"""

from __future__ import annotations

import pytest

import core.imports.side_effects as side_effects
from core.downloads import status as st
from core.downloads.asked_vs_got import history_asked_and_got
from core.downloads.track_detail import build_track_detail


class _CaptureDb:
    def __init__(self):
        self.rows = []

    def add_library_history_entry(self, **kwargs):
        self.rows.append(kwargs)
        return None  # no id: skips the correlation persist


@pytest.fixture
def written_row(monkeypatch):
    """the library_history row the import writes for a soulseek grab that
    fetched the wrong song."""
    db = _CaptureDb()
    monkeypatch.setattr(side_effects, 'get_database', lambda: db)
    context = {
        # what was asked for
        'track_info': {
            'id': 'sp-dead-or-alive',
            'name': 'Dead or Alive',
            'artists': [{'name': 'Lil Yee'}],
            'album': {'name': 'Yee Tape'},
        },
        # what the source handed over
        'original_search_result': {
            'username': 'peer42',
            'filename': '@@music\\Lil Jon\\01 - Alive.flac',
            'title': '01 - Alive',
            'artist': 'Lil Jon',
        },
        '_final_processed_path': '/lib/Lil Yee/Dead or Alive.flac',
        '_verification_status': 'unverified',
    }
    side_effects.record_library_history_download(context)
    assert len(db.rows) == 1
    row = dict(db.rows[0])
    row.setdefault('id', 41)
    row.setdefault('created_at', '2026-11-16 10:00:00')
    return row


def test_the_writer_keeps_the_ask_in_title(written_row):
    asked, got = history_asked_and_got(written_row)
    assert asked == {'title': 'Dead or Alive', 'artist': 'Lil Yee'}
    assert got == {'title': '01 - Alive', 'artist': 'Lil Jon'}


def test_review_row_puts_the_ask_in_expected_and_the_file_in_got(written_row):
    item = st._build_history_download_item(written_row)
    assert item['title'] == 'Dead or Alive'
    assert (item['expected_title'], item['expected_artist']) == ('Dead or Alive', 'Lil Yee')
    assert (item['got_title'], item['got_artist']) == ('01 - Alive', 'Lil Jon')


def test_detail_sheet_puts_the_ask_in_expected_and_the_file_in_downloaded(written_row):
    task = {'task_id': 't1', 'status': 'completed', 'track_info': {}}
    detail = build_track_detail(task, written_row)
    assert detail['expected'] == {'title': 'Dead or Alive', 'artist': 'Lil Yee'}
    assert detail['downloaded'] == {'title': '01 - Alive', 'artist': 'Lil Jon'}
    # the headline is still the song that was asked for
    assert detail['title'] == 'Dead or Alive'
    assert detail['artist'] == 'Lil Yee'
    assert detail['album'] == 'Yee Tape'


def test_a_row_with_no_source_side_has_nothing_to_compare():
    asked, got = history_asked_and_got({'title': 'Song', 'artist_name': None})
    assert asked == {'title': 'Song', 'artist': ''}
    assert got == {'title': '', 'artist': ''}
