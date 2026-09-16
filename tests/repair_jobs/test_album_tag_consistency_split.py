"""Album Tag Consistency catches albums the server has ALREADY split.

achilles4 (discord): download two songs from one album at different times,
soulsync shows one album, navidrome shows two, "the SoulSync tools have not
picked up on these discrepancies." navidrome keys an album on album + album
artist + musicbrainz release id, so two tracks that resolved different
releases split — and once split they reach our database as two album rows
with one track each, which the job's "2+ tracks per album" gate skipped.

now album rows sharing an artist and title are compared as one album, and a
track missing a tag the others carry counts as a mismatch. edition
qualifiers stay separate: "Album (Deluxe)" is not "Album".

temp db + tmp_path flac files. no network.
"""

from __future__ import annotations

from core.repair_jobs.album_tag_consistency import (
    AlbumTagConsistencyJob,
    MISSING,
    find_inconsistencies,
    split_group_key,
)
from core.repair_jobs.base import JobContext
from database.music_database import MusicDatabase

from tests.repair_jobs.test_album_tag_consistency_reporting import _Config, _make_flac

MBID_A = '11111111-1111-1111-1111-111111111111'
MBID_B = '22222222-2222-2222-2222-222222222222'


def _add_album(db, album_id, artist_id, artist_name, title, tracks):
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name, server_source) VALUES (?, ?, 'test')",
                     (artist_id, artist_name))
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'test')",
                     (album_id, artist_id, title))
        for track_id, file_path in tracks:
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                         "VALUES (?, ?, ?, ?, ?, 'test')", (track_id, album_id, artist_id, f'Track {track_id}', file_path))
        conn.commit()


def _run_scan(db, tmp_path):
    progress, findings = [], []
    context = JobContext(
        db=db, transfer_folder=str(tmp_path), config_manager=_Config(),
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=lambda: False, is_paused=lambda: False,
        report_progress=lambda **kw: progress.append(kw),
    )
    return AlbumTagConsistencyJob().scan(context), progress, findings


# ── the key ──────────────────────────────────────────────────────────────────

def test_group_key_folds_case_and_punctuation_but_keeps_editions():
    assert split_group_key('Muse', 'Simulation Theory') == split_group_key('MUSE', 'simulation  theory')
    assert split_group_key('Muse', "Black Holes & Revelations") == split_group_key('muse', 'black holes revelations')
    assert split_group_key('Muse', 'Simulation Theory') != split_group_key('Muse', 'Simulation Theory (Super Deluxe)')
    assert split_group_key('Muse', 'Absolution') != split_group_key('The Cure', 'Absolution')


# ── missing tags ─────────────────────────────────────────────────────────────

def test_a_missing_release_id_is_a_mismatch_and_never_the_majority():
    tag_data = [
        {'album_tag': 'X', 'albumartist_tag': 'A', 'mbid_tag': MBID_A},
        {'album_tag': 'X', 'albumartist_tag': 'A', 'mbid_tag': None},
        {'album_tag': 'X', 'albumartist_tag': 'A', 'mbid_tag': None},
    ]
    found = find_inconsistencies(tag_data, {})
    assert [f['field'] for f in found] == ['musicbrainz_albumid']
    assert found[0]['canonical'] == MBID_A            # two missing do not outvote one real id
    assert found[0]['variants'] == [MBID_A, MISSING]
    assert found[0]['outlier_count'] == 2


def test_a_tag_nobody_has_is_left_alone():
    tag_data = [
        {'album_tag': 'X', 'albumartist_tag': None, 'mbid_tag': None},
        {'album_tag': 'X', 'albumartist_tag': None, 'mbid_tag': None},
    ]
    assert find_inconsistencies(tag_data, {}) == []


def test_settings_still_switch_fields_off():
    tag_data = [
        {'album_tag': 'X', 'albumartist_tag': 'A', 'mbid_tag': MBID_A},
        {'album_tag': 'Y', 'albumartist_tag': 'B', 'mbid_tag': MBID_B},
    ]
    found = find_inconsistencies(tag_data, {'check_album_name': False, 'check_mb_release_id': False})
    assert [f['field'] for f in found] == ['albumartist']


# ── the split pass ───────────────────────────────────────────────────────────

def test_two_single_track_rows_of_one_album_are_compared_together(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    _make_flac(f1, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_A})
    _make_flac(f2, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_B})
    # what navidrome hands us after it split them: two albums, one track each
    _add_album(db, 'AL1', 'AR1', 'Achilles', 'Rebuild', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR1', 'Achilles', 'Rebuild', [('T2', str(f2))])

    result, progress, findings = _run_scan(db, tmp_path)

    assert len(findings) == 1
    f = findings[0]
    assert f['finding_type'] == 'album_tag_inconsistency'
    assert f['entity_id'] == 'AL1'
    assert f['title'].startswith('Split on your server: Rebuild by Achilles')
    assert f['details']['server_split'] is True
    assert f['details']['album_ids'] == ['AL1', 'AL2']
    assert [i['field'] for i in f['details']['inconsistencies']] == ['musicbrainz_albumid']
    assert {t['file_path'] for t in f['details']['tracks']} == {str(f1), str(f2)}
    assert any('has split' in c.get('log_line', '') for c in progress)


def test_split_rows_with_consistent_tags_are_not_flagged(tmp_path):
    # the server split them for some reason of its own; tags agree, nothing to fix
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    for f in (f1, f2):
        _make_flac(f, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_A})
    _add_album(db, 'AL1', 'AR1', 'Achilles', 'Rebuild', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR1', 'Achilles', 'Rebuild', [('T2', str(f2))])
    _, _, findings = _run_scan(db, tmp_path)
    assert findings == []


def test_deluxe_and_standard_are_not_treated_as_one_album(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    _make_flac(f1, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_A})
    _make_flac(f2, {'album': 'Rebuild (Deluxe)', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_B})
    _add_album(db, 'AL1', 'AR1', 'Achilles', 'Rebuild', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR1', 'Achilles', 'Rebuild (Deluxe)', [('T2', str(f2))])
    _, _, findings = _run_scan(db, tmp_path)
    assert findings == []


def test_same_title_different_artist_is_not_grouped(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    _make_flac(f1, {'album': 'Greatest Hits', 'albumartist': 'One', 'musicbrainz_albumid': MBID_A})
    _make_flac(f2, {'album': 'Greatest Hits', 'albumartist': 'Two', 'musicbrainz_albumid': MBID_B})
    _add_album(db, 'AL1', 'AR1', 'One', 'Greatest Hits', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR2', 'Two', 'Greatest Hits', [('T2', str(f2))])
    _, _, findings = _run_scan(db, tmp_path)
    assert findings == []


def test_a_missing_id_on_the_second_download_is_caught_and_the_fix_fills_it(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    _make_flac(f1, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_A})
    _make_flac(f2, {'album': 'Rebuild', 'albumartist': 'Achilles'})          # no release id at all
    _add_album(db, 'AL1', 'AR1', 'Achilles', 'Rebuild', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR1', 'Achilles', 'Rebuild', [('T2', str(f2))])
    _, _, findings = _run_scan(db, tmp_path)
    assert len(findings) == 1
    inc = findings[0]['details']['inconsistencies']
    assert inc[0]['field'] == 'musicbrainz_albumid' and inc[0]['canonical'] == MBID_A

    # applying the finding writes the majority id into the file that had none
    from core.repair_worker import RepairWorker
    worker = RepairWorker(db, transfer_folder=str(tmp_path))
    outcome = worker._fix_album_tag_inconsistency('album', 'AL1', None, findings[0]['details'])
    assert outcome['success'] is True and outcome['action'] == 'normalized_tags'
    from mutagen.flac import FLAC
    assert FLAC(str(f2))['musicbrainz_albumid'] == [MBID_A]
    assert FLAC(str(f1))['musicbrainz_albumid'] == [MBID_A]
    assert '(missing)' in outcome['message']


# ── two albums with one title are not one album ──────────────────────────────

RG_BLUE = 'aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa'
RG_GREEN = 'bbbbbbbb-0000-0000-0000-bbbbbbbbbbbb'


def test_self_titled_albums_with_different_release_groups_are_not_merged(tmp_path):
    # weezer: "Weezer" (1994) and "Weezer" (2001). each row internally
    # consistent, different release groups. merging would stamp one id on both
    db = MusicDatabase(str(tmp_path / 'm.db'))
    blue = [tmp_path / f'blue{i}.flac' for i in (1, 2)]
    green = [tmp_path / f'green{i}.flac' for i in (1, 2)]
    for f in blue:
        _make_flac(f, {'album': 'Weezer', 'albumartist': 'Weezer', 'musicbrainz_albumid': MBID_A,
                       'musicbrainz_releasegroupid': RG_BLUE, 'date': '1994-05-10'})
    for f in green:
        _make_flac(f, {'album': 'Weezer', 'albumartist': 'Weezer', 'musicbrainz_albumid': MBID_B,
                       'musicbrainz_releasegroupid': RG_GREEN, 'date': '2001-05-15'})
    _add_album(db, 'AL_BLUE', 'AR1', 'Weezer', 'Weezer', [('T1', str(blue[0])), ('T2', str(blue[1]))])
    _add_album(db, 'AL_GREEN', 'AR1', 'Weezer', 'Weezer', [('T3', str(green[0])), ('T4', str(green[1]))])

    _, progress, findings = _run_scan(db, tmp_path)

    assert findings == []
    assert any('look like different albums' in c.get('log_line', '') for c in progress)


def test_different_years_alone_keep_them_apart(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    _make_flac(f1, {'album': 'Weezer', 'albumartist': 'Weezer', 'musicbrainz_albumid': MBID_A, 'date': '1994'})
    _make_flac(f2, {'album': 'Weezer', 'albumartist': 'Weezer', 'musicbrainz_albumid': MBID_B, 'date': '2001'})
    _add_album(db, 'AL1', 'AR1', 'Weezer', 'Weezer', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR1', 'Weezer', 'Weezer', [('T2', str(f2))])
    _, _, findings = _run_scan(db, tmp_path)
    assert findings == []


def test_a_side_without_identity_tags_cannot_object(tmp_path):
    # the genuine split: the second download resolved another release of the
    # SAME release group, or carries no group/date at all. still one album.
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 't1.flac', tmp_path / 't2.flac'
    _make_flac(f1, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_A,
                    'musicbrainz_releasegroupid': RG_BLUE, 'date': '2019'})
    _make_flac(f2, {'album': 'Rebuild', 'albumartist': 'Achilles', 'musicbrainz_albumid': MBID_B,
                    'musicbrainz_releasegroupid': RG_BLUE})
    _add_album(db, 'AL1', 'AR1', 'Achilles', 'Rebuild', [('T1', str(f1))])
    _add_album(db, 'AL2', 'AR1', 'Achilles', 'Rebuild', [('T2', str(f2))])
    _, _, findings = _run_scan(db, tmp_path)
    assert len(findings) == 1
    assert findings[0]['details']['server_split'] is True


def test_rows_look_like_one_album_helper():
    from core.repair_jobs.album_tag_consistency import rows_look_like_one_album
    a = [{'rg_tag': RG_BLUE, 'date_tag': '1994-05-10'}]
    b = [{'rg_tag': RG_GREEN, 'date_tag': '1994-05-10'}]
    c = [{'rg_tag': None, 'date_tag': None}]
    d = [{'rg_tag': RG_BLUE, 'date_tag': '2001'}]
    assert not rows_look_like_one_album([a, b])          # groups differ
    assert not rows_look_like_one_album([a, d])          # years differ
    assert rows_look_like_one_album([a, c])              # nothing to object with
    assert rows_look_like_one_album([a, a])
