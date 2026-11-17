"""One heal for a combined-credit artist, and the hand-off to the projection writers.

``native_enrich.heal_combined_artist`` is the catalogue change every
combined-credit healer makes (the local and the provider split, the Split
Artists fix, the settle after an import). ``RepairWorker.request_projection``
is how a healer hands the changed tracks to Re-tag and Reorganize: scoped,
by their own settings, never forced on a job the user switched off.
"""

from __future__ import annotations

import pytest

from core.library2 import native_enrich as NE
from core.repair_worker import RepairWorker, _merge_scope
from database.music_database import MusicDatabase
from tests import lib2_seed


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _album(db, artist, *titles, legacy=None):
    with db._get_connection() as conn:
        ids = [lib2_seed.track(conn, artist, 'Album', t) for t in titles]
        if legacy is not None:
            lib2_seed.artist(conn, artist, legacy_artist_id=legacy)
        conn.commit()
    return ids


def _id(conn, name):
    row = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def _heal(db, name, parts, **kw):
    with db._get_connection() as conn:
        out = NE.heal_combined_artist(conn, _id(conn, name), parts, **kw)
        conn.commit()
    return out


def test_the_album_and_every_credit_move_to_the_parts(db):
    tracks = _album(db, 'Camellia, Toby Fox', 'One', 'Two')
    out = _heal(db, 'Camellia, Toby Fox', ['Camellia', 'Toby Fox'])
    assert out['track_ids'] == sorted(tracks) and out['created'] == 2
    with db._get_connection() as conn:
        assert conn.execute("SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar "
                            "ON ar.id=al.primary_artist_id").fetchone()[0] == 'Camellia'
        for track in tracks:
            names = [r[0] for r in conn.execute(
                "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a "
                "ON a.id=ta.artist_id WHERE ta.track_id=? ORDER BY ta.position", (track,))]
            assert names == ['Camellia', 'Toby Fox']
        assert _id(conn, 'Camellia, Toby Fox') is None


def test_an_existing_artist_is_reused_identified_first_and_the_lead_is_chosen(db):
    _album(db, 'Camellia, Toby Fox', 'One')
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('Toby Fox', 'toby fox')")
        real = conn.execute("INSERT INTO lib2_artists(name, name_key, spotify_id) "
                            "VALUES('Toby Fox', 'toby fox', 'sp-toby')").lastrowid
        conn.commit()
    out = _heal(db, 'Camellia, Toby Fox', ['Camellia', 'Toby Fox'], lead='Toby Fox')
    assert out['primary_id'] == real and out['created'] == 1


def test_a_legacy_backed_combined_row_stays_as_an_alias_of_the_lead(db):
    _album(db, 'Camellia, Toby Fox', 'One', legacy='LEG1')
    out = _heal(db, 'Camellia, Toby Fox', ['Camellia', 'Toby Fox'])
    with db._get_connection() as conn:
        row = conn.execute("SELECT canonical_artist_id FROM lib2_artists WHERE id=?",
                           (out['combined_id'],)).fetchone()
    assert row[0] == out['primary_id']


def test_the_watch_goes_to_the_lead_only(db):
    _album(db, 'Camellia, Toby Fox', 'One')
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_artists SET monitored=1 WHERE name='Camellia, Toby Fox'")
        conn.commit()
    _heal(db, 'Camellia, Toby Fox', ['Camellia', 'Toby Fox'])
    with db._get_connection() as conn:
        watched = {r[0] for r in conn.execute("SELECT name FROM lib2_artists WHERE monitored=1")}
    assert watched == {'Camellia'}


def test_a_lock_on_one_track_refuses_the_whole_heal(db, monkeypatch):
    tracks = _album(db, 'Camellia, Toby Fox', 'One', 'Two')
    from core.library2 import locks
    monkeypatch.setattr(locks, 'blocks_entity',
                        lambda kind, entity_id, *a, **k: kind == 'track' and entity_id == tracks[1])
    out = _heal(db, 'Camellia, Toby Fox', ['Camellia', 'Toby Fox'])
    assert out['refused'] == 'locked' and out['locked_track_ids'] == [tracks[1]]
    with db._get_connection() as conn:
        assert _id(conn, 'Camellia, Toby Fox') is not None and _id(conn, 'Camellia') is None


def test_a_hand_set_name_refuses_the_heal(db):
    _album(db, 'Camellia, Toby Fox', 'One')
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, "
                     "value_json) VALUES('artist', ?, 'name', '\"Camellia, Toby Fox\"')",
                     (_id(conn, 'Camellia, Toby Fox'),))
        conn.commit()
    assert _heal(db, 'Camellia, Toby Fox', ['Camellia', 'Toby Fox'])['refused'] == 'manual'


@pytest.mark.parametrize("parts", [[], ['Camellia'], ['Camellia', 'camellia']])
def test_fewer_than_two_artists_is_nothing_to_do(db, parts):
    _album(db, 'Camellia, Toby Fox', 'One')
    assert _heal(db, 'Camellia, Toby Fox', parts) is None


# ── the hand-off to Re-tag / Reorganize ─────────────────────────────────────

def test_scopes_of_one_job_add_up_until_it_runs():
    a = {'file_paths': ['/m/a.flac']}
    b = {'file_paths': ['/m/b.flac']}
    assert _merge_scope(None, a, queued=False) == a
    assert _merge_scope(a, b, queued=True) == {'file_paths': ['/m/a.flac', '/m/b.flac']}
    # a waiting library-wide run already covers everything
    assert _merge_scope(None, b, queued=True) is None
    assert _merge_scope(a, None, queued=True) is None
    # another library's files are another run: the newest wins, as before
    assert _merge_scope({**a, 'library': 2}, b, queued=True) == b


def _bare_worker(db, enabled=True):
    worker = RepairWorker(db)
    worker._jobs = {job: type('J', (), {'supports_file_scope': True, 'display_name': job})()
                    for job in ('library_retag', 'library_reorganize')}
    worker._ensure_jobs_loaded = lambda: None
    worker.get_job_config = lambda job_id: {'enabled': enabled}
    return worker


def test_request_projection_queues_both_writers_scoped_to_the_files(db):
    tracks = _album(db, 'Camellia', 'One', 'Two')
    worker = _bare_worker(db)
    out = worker.request_projection(tracks)
    assert out == {'files': 2, 'queued': ['retag', 'reorganize']}
    assert worker._force_run_queue == ['library_retag', 'library_reorganize']
    assert len(worker._force_run_scopes['library_retag']['file_paths']) == 2
    more = _album(db, 'Toby Fox', 'Three')
    worker.request_projection(more, reorganize=False)
    assert len(worker._force_run_scopes['library_retag']['file_paths']) == 3
    assert len(worker._force_run_scopes['library_reorganize']['file_paths']) == 2


def test_a_heal_asks_the_writers_even_without_their_schedule(db):
    # both jobs ship switched off: that switch is their library-wide
    # schedule. Whether a heal asks is the healer's own setting
    tracks = _album(db, 'Camellia', 'One')
    worker = _bare_worker(db, enabled=False)
    assert worker.request_projection(tracks)['queued'] == ['retag', 'reorganize']
    assert worker._force_run_queue == ['library_retag', 'library_reorganize']
    assert worker.request_projection(_album(db, 'Toby Fox', 'Two'),
                                     retag=False, reorganize=False)['queued'] == []


def test_split_artists_settings_decide_for_every_split_heal():
    from core.repair_worker import split_projection

    class Cfg:
        def __init__(self, settings):
            self.settings = settings

        def get(self, key, default=None):
            return self.settings if key == 'repair.jobs.comma_artist_splitter.settings' else default
    assert split_projection(None) == {'retag': True, 'reorganize': True}
    assert split_projection(Cfg({})) == {'retag': True, 'reorganize': True}
    assert split_projection(Cfg({'request_reorganize': False})) == \
        {'retag': True, 'reorganize': False}


def test_request_projection_with_nothing_to_project(db):
    worker = _bare_worker(db)
    assert worker.request_projection([]) == {'files': 0, 'queued': []}
    assert worker.request_projection([999]) == {'files': 0, 'queued': []}


# ── only Split Artists splits: everything else asks it ───────────────────────

def test_a_split_request_queues_split_artists_for_those_artists(db):
    worker = _bare_worker(db, enabled=False)
    worker._jobs['comma_artist_splitter'] = type('J', (), {
        'supports_artist_id_scope': True, 'supports_file_scope': True,
        'display_name': 'Split Artists'})()
    assert worker.request_split([7, 3, 7], reason='test') is True
    assert worker._force_run_queue == ['comma_artist_splitter']
    assert worker._force_run_scopes['comma_artist_splitter'] == {'artist_ids': [3, 7]}
    # a second request before it runs adds up
    worker.request_split([9])
    assert worker._force_run_scopes['comma_artist_splitter'] == {'artist_ids': [3, 7, 9]}
    assert worker.request_split([]) is False


def test_a_job_that_cannot_take_artist_ids_refuses_them(db):
    worker = _bare_worker(db)
    with pytest.raises(ValueError):
        worker.run_job_now('library_retag', scope={'artist_ids': [1]})


def test_without_a_worker_a_split_request_is_a_no_op(monkeypatch):
    import core.repair_worker as rw
    from core.repair_worker import request_artist_split
    monkeypatch.setattr(rw, '_ACTIVE_WORKER', None)
    assert request_artist_split([1, 2]) is False


def test_a_file_scope_and_an_artist_request_add_up():
    files = {'file_paths': ['/m/a.flac']}
    artists = {'artist_ids': [3]}
    assert _merge_scope(files, artists, queued=True) == {'file_paths': ['/m/a.flac'],
                                                         'artist_ids': [3]}
    assert _merge_scope(artists, files, queued=True) == {'artist_ids': [3],
                                                         'file_paths': ['/m/a.flac']}
    # another library's request is another run, as before
    assert _merge_scope({**files, 'library': 2}, artists, queued=True) == artists
