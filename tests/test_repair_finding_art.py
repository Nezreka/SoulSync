"""a finding row and an issue type carry the artwork to show beside them.

most jobs store no picture on a finding, so the findings list was text only.
the catalogue has the cover, and the row now gets it: by the finding's
library v2 album or track, then by artist and album name.
"""

from __future__ import annotations

import json

import pytest

from core.repair_worker import RepairWorker


@pytest.fixture()
def worker(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists (id, name, image_url) VALUES (1, 'Crystal Waters', '/art/cw.jpg')")
    conn.execute("INSERT INTO lib2_artists (id, name, image_url) VALUES (2, 'Other', '/art/other.jpg')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) VALUES (10, 1, 'Remixes', '/art/remixes.jpg')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) VALUES (11, 2, 'Remixes', '/art/wrong.jpg')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) VALUES (12, 1, 'No Cover', NULL)")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (100, 10, 'Song')")
    conn.commit()
    conn.close()
    return w


def _add(worker, finding_type, details, *, entity_type=None, entity_id=None, status='pending'):
    conn = worker.db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, entity_type,"
        " entity_id, title, details_json) VALUES (?, ?, 'info', ?, ?, ?, 't', ?)",
        (finding_type, finding_type, status, entity_type, entity_id, json.dumps(details)))
    conn.commit()
    conn.close()


def _only(worker):
    items = worker.get_findings(status='pending')['items']
    assert len(items) == 1
    return items[0]


def test_a_picture_the_finding_carries_is_used(worker):
    _add(worker, 'canonical_version', {'album_thumb_url': '/own/a.jpg', 'artist_thumb_url': '/own/b.jpg'})
    row = _only(worker)
    assert row['album_thumb_url'] == '/own/a.jpg'
    assert row['artist_thumb_url'] == '/own/b.jpg'


def test_a_tag_scan_finding_gets_its_album_cover_by_id(worker):
    _add(worker, 'library_retag', {'validation': {'album_id': 10, 'track_id': 100}},
         entity_type='track', entity_id='lib2:100')
    row = _only(worker)
    assert row['album_thumb_url'] == '/art/remixes.jpg'
    assert row['artist_thumb_url'] == '/art/cw.jpg'


def test_a_track_finding_finds_the_album_through_its_track(worker):
    _add(worker, 'missing_lyrics', {}, entity_type='track', entity_id='lib2:100')
    assert _only(worker)['album_thumb_url'] == '/art/remixes.jpg'


def test_a_bare_number_is_not_read_as_a_library_v2_id(worker):
    # an old library's track 100 is some other song
    _add(worker, 'missing_lyrics', {'track_id': '100'}, entity_type='track', entity_id='100')
    assert 'album_thumb_url' not in _only(worker)


def test_a_named_album_matches_on_artist_and_title(worker):
    _add(worker, 'missing_lyrics', {'artist': 'Crystal Waters', 'album_title': 'Remixes'})
    # two albums are called Remixes; the other artist's cover is not this one's
    assert _only(worker)['album_thumb_url'] == '/art/remixes.jpg'


def test_another_artists_album_of_the_same_name_is_not_borrowed(worker):
    # only Crystal Waters has an album called Remixes Two; a finding that
    # names it under another artist is about some other record
    conn = worker.db._get_connection()
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) VALUES (13, 1, 'Remixes Two', '/art/two.jpg')")
    conn.commit()
    conn.close()
    _add(worker, 'missing_lyrics', {'artist': 'Other', 'album_title': 'Remixes Two'})
    _add(worker, 'missing_lyrics', {'artist': 'Crystal Waters', 'album_title': 'Remixes Two'})
    rows = worker.get_findings(status='pending', sort='oldest')['items']
    assert 'album_thumb_url' not in rows[0]
    assert rows[1]['album_thumb_url'] == '/art/two.jpg'


def test_a_finding_about_nothing_in_the_library_has_no_picture(worker):
    _add(worker, 'orphan_file', {'folder': '/music/x'}, entity_type='file')
    row = _only(worker)
    assert 'album_thumb_url' not in row
    assert 'artist_thumb_url' not in row


def test_missing_cover_art_shows_the_cover_it_found(worker):
    _add(worker, 'missing_cover_art',
         {'library_v2': {'album_id': 12}, 'found_artwork_url': 'https://covers/x.jpg'},
         entity_type='album', entity_id='lib2:12')
    row = _only(worker)
    assert row['album_thumb_url'] == 'https://covers/x.jpg'
    assert row['artist_thumb_url'] == '/art/cw.jpg'


def test_an_issue_type_carries_a_few_distinct_covers(worker):
    for _ in range(3):
        _add(worker, 'library_retag', {'validation': {'album_id': 10}})
    _add(worker, 'library_retag', {'album_thumb_url': '/own/z.jpg'})
    _add(worker, 'library_retag', {'album_thumb_url': '/own/closed.jpg'}, status='resolved')
    _add(worker, 'orphan_file', {})
    groups = {g['finding_type']: g for g in worker.get_finding_groups()}
    # newest first, one per cover, open rows only
    assert groups['library_retag']['art'] == ['/own/z.jpg', '/art/remixes.jpg']
    assert groups['orphan_file']['art'] == []


def test_a_broken_catalogue_lookup_still_returns_the_page(worker):
    _add(worker, 'library_retag', {'validation': {'album_id': 10}})
    conn = worker.db._get_connection()
    conn.execute("ALTER TABLE lib2_albums RENAME TO lib2_albums_gone")
    conn.commit()
    conn.close()
    assert len(worker.get_findings(status='pending')['items']) == 1
