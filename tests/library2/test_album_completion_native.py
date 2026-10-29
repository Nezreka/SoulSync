"""Completing an album creates native monitoring intent, never a second download queue."""
from types import SimpleNamespace

import pytest

from database.music_database import MusicDatabase
from tests import lib2_seed
from core.repair_jobs.base import JobContext


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / 'completion.db'))
    with db._get_connection() as conn:
        owned = lib2_seed.track(conn, 'Artist', 'Album', 'Owned', track_number=1, disc_number=1, monitored=0)
        missing = lib2_seed.track(conn, 'Artist', 'Album', 'Missing', owned=False, track_number=2, disc_number=1, monitored=0)
        aid = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (owned,)).fetchone()[0]
        conn.commit()
    loads = []
    def load(*args, **kwargs):
        loads.append(args[3])
        assert kwargs['inherit_monitoring'] is False
        return []
    monkeypatch.setattr('core.library2.completeness.load_album_catalogue', load)
    return db, aid, owned, missing, loads


def run(db, *, settings=None, scope=None):
    from core.repair_jobs import get_all_jobs
    cls = get_all_jobs().get('album_completeness')
    assert cls is not None, 'The opt-in completion job must run on Library v2'
    cfg = SimpleNamespace(get=lambda key, default=None: settings if key.endswith('.settings') else default)
    return cls().scan(JobContext(db, '/music', cfg, scope=scope))


def rules(db):
    with db._get_connection() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT entity_id,profile_id,monitored FROM lib2_monitor_rules WHERE entity_type='track' ORDER BY entity_id,profile_id")]


def test_job_monitors_only_missing_tracks_without_requiring_a_pin(catalogue):
    db, aid, owned, missing, loads = catalogue
    result = run(db)
    assert result.errors == 0 and result.auto_fixed == 1
    assert loads == [aid]
    assert rules(db) == [(missing, 1, 1)]
    with db._get_connection() as conn:
        assert conn.execute('SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1', (missing,)).fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM lib2_mirror_outbox').fetchone()[0] > 0
        assert conn.execute('SELECT monitored FROM lib2_tracks WHERE id=?', (owned,)).fetchone()[0] == 0
    assert run(db).auto_fixed == 0


def test_job_never_opts_a_browsed_discography_into_monitoring(catalogue):
    db, aid, _, missing, loads = catalogue
    with db._get_connection() as conn:
        other = lib2_seed.track(conn, 'Artist', 'Browsing only', 'Do not want', owned=False)
        conn.commit()
    run(db)
    assert loads == [aid]
    assert rules(db) == [(missing, 1, 1)]


def test_job_uses_each_librarys_file_ownership(catalogue):
    db, aid, owned, missing, _ = catalogue
    with db._get_connection() as conn:
        conn.execute('UPDATE lib2_track_files SET owner_profile_id=7 WHERE track_id=?', (owned,))
        # The missing track exists in another library; that must not complete this one.
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,'/other/missing.flac',NULL)", (missing,))
        conn.commit()
    result = run(db)
    assert result.errors == 0
    assert (missing, 7, 1) in rules(db)
    assert (missing, 1, 1) not in rules(db)


@pytest.mark.parametrize('mode', ['dry_run', 'hand_tagged', 'outside_scope'])
def test_job_respects_scope_and_protected_files(catalogue, mode):
    db, _, owned, _, _ = catalogue
    if mode == 'hand_tagged':
        with db._get_connection() as conn:
            path = conn.execute('SELECT path FROM lib2_track_files WHERE track_id=?', (owned,)).fetchone()[0]
        db.record_manual_metadata_file(path)
    run(db, settings={'dry_run': mode == 'dry_run'},
        scope={'file_paths': []} if mode == 'outside_scope' else None)
    assert rules(db) == []


def test_job_does_not_monitor_unknown_number_placeholders(catalogue):
    db, _, _, missing, _ = catalogue
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET title='Track 2',spotify_id=NULL,external_ids='{}' WHERE id=?", (missing,))
        conn.commit()
    run(db)
    assert rules(db) == []


def test_target_album_does_not_widen_to_other_albums(catalogue):
    db, _, _, _, loads = catalogue
    run(db, scope={'target_album_id': 'lib2:999'})
    assert loads == [] and rules(db) == []


def test_job_monitors_only_the_selected_edition(catalogue):
    from core.library2.editions import ensure_release_track

    db, aid, owned, missing, _ = catalogue
    with db._get_connection() as conn:
        bonus = lib2_seed.track(conn, 'Artist', 'Album', 'Deluxe bonus', owned=False,
                               track_number=3, monitored=0)
        chosen = conn.execute(
            'INSERT INTO lib2_release_editions(release_group_id,is_default) VALUES(?,1)', (aid,)).lastrowid
        deluxe = conn.execute(
            'INSERT INTO lib2_release_editions(release_group_id,is_default) VALUES(?,0)', (aid,)).lastrowid
        for tid, edition in [(owned, chosen), (missing, chosen), (bonus, deluxe)]:
            row = conn.execute('SELECT * FROM lib2_tracks WHERE id=?', (tid,)).fetchone()
            ensure_release_track(conn.cursor(), row, edition)
        conn.commit()
    run(db)
    assert rules(db) == [(missing, 1, 1)]


def test_provider_failure_leaves_that_album_unmonitored(catalogue, monkeypatch):
    db, _, _, _, _ = catalogue
    def unavailable(*args, **kwargs):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr('core.library2.completeness.load_album_catalogue', unavailable)
    assert run(db).errors == 1
    assert rules(db) == []


def test_a_bulk_run_leaves_other_artists_tracks_to_an_explicit_fill(catalogue):
    """upstream b12666102/bfe5cd21c/320ec94ef: one artist's compilation or
    soundtrack must not pull every other artist on it into the library. A
    guest on the album artist's own track still counts as the artist's."""
    db, aid, _, missing, _ = catalogue
    with db._get_connection() as conn:
        guest = lib2_seed.track(conn, 'Artist', 'Album', 'Their Song', owned=False,
                                credit='Someone Else', track_number=3, disc_number=1, monitored=0)
        feat = lib2_seed.track(conn, 'Artist', 'Album', 'Our Song', owned=False,
                               credit='Someone Else', track_number=4, disc_number=1, monitored=0)
        artist_id = conn.execute('SELECT primary_artist_id FROM lib2_albums WHERE id=?', (aid,)).fetchone()[0]
        conn.execute('INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)', (feat, artist_id))
        conn.commit()

    run(db)
    assert [r[0] for r in rules(db)] == sorted([missing, feat])

    run(db, scope={'target_album_id': f'lib2:{aid}'})
    assert [r[0] for r in rules(db)] == sorted([missing, guest, feat])


def test_a_credit_to_the_album_artists_alias_is_the_artists_own(catalogue):
    """lib2 port of the same rule: an alias of the album artist (one alias
    group, as the artist page reads it) is not another artist."""
    db, aid, _, missing, _ = catalogue
    with db._get_connection() as conn:
        artist_id = conn.execute('SELECT primary_artist_id FROM lib2_albums WHERE id=?', (aid,)).fetchone()[0]
        alias = conn.execute("INSERT INTO lib2_artists(name, name_key, canonical_artist_id) "
                             "VALUES('Alias', 'alias', ?)", (artist_id,)).lastrowid
        aliased = lib2_seed.track(conn, 'Artist', 'Album', 'Alias Song', owned=False,
                                  track_number=5, disc_number=1, monitored=0)
        conn.execute('INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)', (aliased, alias))
        conn.commit()

    run(db)
    assert [r[0] for r in rules(db)] == sorted([missing, aliased])
