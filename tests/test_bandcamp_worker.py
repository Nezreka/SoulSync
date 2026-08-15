"""BandcampWorker against Library v2 (docs §32.3.1 stage 2).

Same behaviours this file has always pinned, restated against lib2 now that the
worker no longer touches the legacy tables:

* a re-enrichment pass that carries no numeric id must not erase the id already
  recorded — the "already matched, re-fetch from the stored URL" path passes
  ``{'id': None, ...}``, and anything keyed off that id (the per-track match chip,
  the artist enrichment coverage percentage) would silently break while the item
  is still matched;
* a match enriches the album's real metadata, not only Bandcamp's own namespace,
  and only ever by backfill;
* a stored URL is refreshed by direct fetch and never re-searched (issue #501),
  and a transient refresh failure preserves the match instead of falling through
  to a search that could overwrite it.

Where legacy wrote ``bandcamp_id``/``bandcamp_url``/``bandcamp_match_status``
columns, lib2 carries the same three facts as ``enrichment.bandcamp.id``,
``external_ids.bandcamp`` and a ``lib2_provider_attempts`` row.
"""

import json
import sqlite3

import pytest

from core.bandcamp_worker import BandcampWorker
from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema,
)
from core.library2.schema import ensure_library_v2_schema

from lib2_ownership import own_every_track


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class _FakeClient:
    def __init__(self, release=None, search_result=None):
        self._release = release
        self._search_result = search_result
        self.release_calls = []
        self.search_calls = []

    def get_release_metadata(self, url):
        self.release_calls.append(url)
        return self._release

    def search_album(self, artist, album):
        self.search_calls.append((artist, album))
        return self._search_result

    def search_track(self, artist, title):
        self.search_calls.append((artist, title))
        return self._search_result


@pytest.fixture
def worker(tmp_path):
    path = str(tmp_path / "bc.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Some Artist','Some Artist')"
    ).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Pending Album','album')", (artist,)).lastrowid
    conn.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Pending Track')", (album,))
    conn.commit()
    conn.close()

    instance = BandcampWorker.__new__(BandcampWorker)
    instance.db = _Db(path)
    instance.retry_days = 30
    instance.name_similarity_threshold = 0.75
    instance.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    instance.client = _FakeClient()
    return instance


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def _bandcamp(worker, table='lib2_albums', entity_id=1):
    return json.loads(_row(worker, table, entity_id)['enrichment']).get('bandcamp', {})


def _status(worker, entity_type='album', entity_id=1):
    conn = worker.db._get_connection()
    try:
        return attempt_state(
            conn, entity_type=entity_type, entity_id=entity_id
        ).get('bandcamp', {}).get('status')
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The id must survive a re-enrichment pass that has no id to report.
# ---------------------------------------------------------------------------


def test_first_match_records_id_url_and_status(worker):
    worker._update_entity('album', 1, {
        'id': 3131312045, 'url': 'https://fbr.bandcamp.com/album/episode-1',
        'title': 'Episode 1', 'tags': ['idm'], 'label': 'FBR',
    })

    assert _bandcamp(worker)['id'] == '3131312045'
    assert json.loads(_row(worker, 'lib2_albums')['external_ids'])['bandcamp'] == \
        'https://fbr.bandcamp.com/album/episode-1'
    assert _status(worker) == 'matched'


def test_reenrichment_with_no_id_preserves_existing_bandcamp_id(worker):
    worker._update_entity('album', 1, {
        'id': 3131312045, 'url': 'https://fbr.bandcamp.com/album/episode-1',
        'title': 'Episode 1', 'tags': ['idm'], 'label': 'FBR',
    })

    # Exactly what _process_album's stored-URL path passes: no numeric id.
    worker._update_entity('album', 1, {
        'id': None, 'url': 'https://fbr.bandcamp.com/album/episode-1',
        'title': 'Episode 1', 'tags': ['idm'], 'label': 'Updated Label',
    })

    payload = _bandcamp(worker)
    assert payload['id'] == '3131312045', "a re-enrichment must not erase the id"
    assert payload['label'] == 'Updated Label'
    assert _status(worker) == 'matched'


def test_track_reenrichment_also_preserves_id(worker):
    worker._update_entity('track', 1, {
        'id': 42, 'url': 'https://x.bandcamp.com/track/t', 'title': 'T', 'tags': [],
    })
    worker._update_entity('track', 1, {
        'id': None, 'url': 'https://x.bandcamp.com/track/t', 'title': 'T', 'tags': [],
    })

    assert _bandcamp(worker, 'lib2_tracks')['id'] == '42'


# ---------------------------------------------------------------------------
# Shared-column persistence: a match enriches the album's real metadata, not
# just Bandcamp's own namespace (PR #968 review).
# ---------------------------------------------------------------------------


def test_album_match_persists_shared_columns(worker):
    worker._update_entity('album', 1, {
        'id': 555, 'url': 'https://x.bandcamp.com/album/y', 'title': 'Y',
        'tags': ['Techno', 'Detroit'], 'label': 'Underground Resistance',
        'release_date': '1992-05-01', 'total_tracks': 8,
    })

    row = _row(worker, 'lib2_albums')
    assert row['label'] == 'Underground Resistance'
    assert row['release_date'] == '1992-05-01'
    assert row['expected_track_count'] == 8
    assert 'Techno' in (row['genres'] or '')
    # The namespace still carries it too.
    assert _bandcamp(worker)['label'] == 'Underground Resistance'


def test_shared_columns_are_backfill_only(worker):
    """Must never clobber a value another source or the user already set."""
    conn = worker.db._get_connection()
    conn.execute("UPDATE lib2_albums SET label='Original Label', "
                 "release_date='2000-01-01' WHERE id=1")
    conn.commit()
    conn.close()

    worker._update_entity('album', 1, {
        'id': 555, 'url': 'https://x.bandcamp.com/album/y', 'title': 'Y',
        'tags': ['rock'], 'label': 'Bandcamp Label',
        'release_date': '1999-09-09', 'total_tracks': 3,
    })

    row = _row(worker, 'lib2_albums')
    assert row['label'] == 'Original Label'
    assert row['release_date'] == '2000-01-01'


def test_track_match_does_not_touch_album_shared_columns(worker):
    """Tracks have no album-level columns — the shared-column block is
    album-only, so a track update must not reach for them."""
    worker._update_entity('track', 1, {
        'id': 9, 'url': 'https://x.bandcamp.com/track/t', 'title': 'T',
        'tags': ['ambient'], 'label': 'L', 'release_date': '2010-01-01',
        'total_tracks': 1,
    })

    ids = json.loads(_row(worker, 'lib2_tracks')['external_ids'])
    assert ids['bandcamp'] == 'https://x.bandcamp.com/track/t'


# ---------------------------------------------------------------------------
# honor_stored_match: a stored URL refreshes by direct fetch, never by
# re-searching, and a transient refresh failure preserves the match.
# ---------------------------------------------------------------------------


def test_stored_url_refreshes_by_fetch_not_search(worker):
    conn = worker.db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET external_ids=?, enrichment=? WHERE id=1",
        (json.dumps({'bandcamp': 'https://x.bandcamp.com/album/y'}),
         json.dumps({'bandcamp': {'id': '555'}})))
    conn.commit()
    conn.close()
    worker.client = _FakeClient(release={
        'url': 'https://x.bandcamp.com/album/y', 'title': 'Y', 'tags': ['idm'],
        'label': 'FBR', 'release_date': '2021-01-01', 'total_tracks': 4,
    })

    worker._process_album(1, 'Y', 'Artist')

    assert worker.client.release_calls == ['https://x.bandcamp.com/album/y']
    assert worker.client.search_calls == [], "never re-searched"
    row = _row(worker, 'lib2_albums')
    assert _bandcamp(worker)['id'] == '555', "preserved through the merge"
    assert row['label'] == 'FBR', "shared columns refreshed"
    assert row['expected_track_count'] == 4


def test_stored_url_refresh_failure_preserves_match_without_searching(worker):
    conn = worker.db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET external_ids=? WHERE id=1",
        (json.dumps({'bandcamp': 'https://x.bandcamp.com/album/y'}),))
    conn.commit()
    conn.close()
    worker.client = _FakeClient(release=None, search_result={
        'id': 1, 'url': 'https://other.bandcamp.com/album/z', 'title': 'Y',
        'tags': [], 'label': None,
    })

    worker._process_album(1, 'Y', 'Artist')

    # A transient fetch miss must NOT fall through to a name search that could
    # overwrite the manual match.
    assert worker.client.search_calls == []
    ids = json.loads(_row(worker, 'lib2_albums')['external_ids'])
    assert ids['bandcamp'] == 'https://x.bandcamp.com/album/y'


def test_no_stored_url_falls_through_to_search(worker):
    worker.client = _FakeClient(search_result={
        'id': 777, 'url': 'https://x.bandcamp.com/album/y', 'title': 'Pending Album',
        'tags': ['idm'], 'label': 'FBR', 'release_date': '2021-01-01',
        'total_tracks': 2,
    })

    worker._process_album(1, 'Pending Album', 'Artist')

    assert worker.client.search_calls == [('Artist', 'Pending Album')]
    assert _bandcamp(worker)['id'] == '777'
    assert json.loads(_row(worker, 'lib2_albums')['external_ids'])['bandcamp'] == \
        'https://x.bandcamp.com/album/y'
    assert _status(worker) == 'matched'


# ---------------------------------------------------------------------------
# _get_next_item honors the Manage Enrichment Workers priority override.
# ---------------------------------------------------------------------------


def test_get_next_item_defaults_to_album_first(worker):
    item = worker._get_next_item()

    assert item['type'] == 'album' and item['id'] == 1


def test_get_next_item_honors_track_priority_override(worker):
    """PR #968 review: the Bandcamp worker must respect the Manage Enrichment
    Workers 'process this group first' override like the other workers."""
    from core.settings import config_manager
    key = 'bandcamp_enrichment_priority'
    old = config_manager.get(key, '')
    try:
        config_manager.set(key, 'track')
        item = worker._get_next_item()
        assert item['type'] == 'track' and item['id'] == 1, \
            "pinned track group must jump ahead of the album"
    finally:
        config_manager.set(key, old)


def test_artists_are_never_offered(worker):
    """Bandcamp has no artist pass; offering artists would mark each one
    not_found and count it as progress."""
    worker._mark_status('album', 1, 'matched')
    worker._mark_status('track', 1, 'matched')

    assert worker._get_next_item() is None
    assert set(worker._get_progress_breakdown()) == {'albums', 'tracks'}


def test_the_worker_holds_no_legacy_sql_at_all():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(pathlib.Path("core/bandcamp_worker.py").read_text())

    assert (usage.reads, usage.writes) == (0, 0)
