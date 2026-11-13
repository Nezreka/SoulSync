"""#2077 (wolf39us): file a greatest hits album's songs under the albums they
first came out on, so a player that filters by the album's year gets it right.

The stand-in MusicBrainz answers in the shapes the live service gave: a
release with its recordings, and a recording's releases with their groups."""

from __future__ import annotations

from mutagen.flac import FLAC

from core.repair_jobs.base import JobContext
from core.repair_jobs.compilation_origins import (
    VARIOUS_ARTISTS_MBID, CompilationOriginsJob, pick_original, plan_compilation)
from core.repair_worker import RepairWorker
from tests.library2.test_tags_to_library_2070 import _DB
from tests.repair_jobs.test_album_tag_consistency_reporting import _make_flac

DM = [{'name': 'Depeche Mode', 'artist': {'id': 'dm', 'name': 'Depeche Mode'}}]


def _rel(rid, group, kind, date, title, *, secondary=(), status='Official', credit=DM):
    return {'id': rid, 'title': title, 'date': date, 'status': status, 'artist-credit': credit,
            'release-group': {'id': group, 'title': title, 'primary-type': kind,
                              'secondary-types': list(secondary), 'first-release-date': date}}


class FakeMB:
    """one best-of with three songs: an album cut, a single mix, a compilation-only song"""

    def __init__(self, credit=DM):
        self.browsed = []
        self.group_sizes = {}
        self.best_of = {
            'id': 'best-of', 'title': 'The Best Of', 'date': '2006-11-13', 'artist-credit': credit,
            'release-group': {'id': 'rg-best', 'secondary-types': ['Compilation']},
            'media': [{'position': 1, 'track-count': 3, 'tracks': [
                {'position': 1, 'title': 'Just Can’t Get Enough',
                 'recording': {'id': 'rec-jcge', 'artist-credit': DM}},
                {'position': 2, 'title': 'Enjoy the Silence (single mix)',
                 'recording': {'id': 'rec-ets-single', 'artist-credit': DM}},
                {'position': 3, 'title': 'Martyr', 'recording': {'id': 'rec-martyr', 'artist-credit': DM}},
            ]}]}
        self.releases = {
            'rec-jcge': [
                _rel('chart-81', 'rg-chart', 'Album', '1981', 'Chart Hits 81', secondary=['Compilation']),
                _rel('jcge-single', 'rg-jcge', 'Single', '1981-09-07', 'Just Can’t Get Enough'),
                _rel('promo', 'rg-jcge', 'Single', '1981', 'Just Can’t Get Enough', status='Promotion'),
                _rel('speak-2', 'rg-speak', 'Album', '1981-10-05', 'Speak & Spell'),
                _rel('speak-1', 'rg-speak', 'Album', '1981-10-05', 'Speak & Spell'),
                _rel('best-of', 'rg-best', 'Album', '2006-11-13', 'The Best Of', secondary=['Compilation']),
            ],
            'rec-ets-single': [
                _rel('ets-single', 'rg-ets', 'Single', '1990-02-05', 'Enjoy the Silence'),
                _rel('best-of', 'rg-best', 'Album', '2006-11-13', 'The Best Of', secondary=['Compilation']),
            ],
            'rec-martyr': [
                _rel('best-of', 'rg-best', 'Album', '2006-11-13', 'The Best Of', secondary=['Compilation']),
            ],
        }
        self.track_lists = {
            'speak-1': {'media': [{'position': 1, 'track-count': 11, 'tracks': [
                {'position': 11, 'recording': {'id': 'rec-jcge'}}]}]},
            'ets-single': {'media': [{'position': 1, 'track-count': 4, 'tracks': [
                {'position': 1, 'recording': {'id': 'rec-ets-single'}}]}]},
        }

    def get_release(self, mbid, includes=None):
        return self.best_of if mbid == 'best-of' else self.track_lists.get(mbid)

    def get_release_group(self, mbid, includes=None):
        return {'id': mbid, 'releases': [{'id': n} for n in range(self.group_sizes.get(mbid, 2))]}

    def browse_recording_releases(self, recording, limit=100, offset=0):
        self.browsed.append((recording, offset))
        return self.releases.get(recording, [])[offset:offset + limit]


TRACKS = [{'track_id': 'lib2:1', 'file_id': 1, 'title': "Just Can't Get Enough", 'file_path': '/m/1.flac'},
          {'track_id': 'lib2:2', 'file_id': 2, 'title': 'Enjoy the Silence (Single Mix)', 'file_path': '/m/2.flac'},
          {'track_id': 'lib2:3', 'file_id': 3, 'title': 'Martyr', 'file_path': '/m/3.flac'}]


def test_the_studio_album_wins_when_the_same_recording_is_on_one():
    plan = plan_compilation(FakeMB(), 'best-of', TRACKS, {})
    to = {m['title']: m['to'] for m in plan['moves']}
    album = to["Just Can't Get Enough"]
    # the single came out a month earlier; the album is where the song belongs
    assert (album['album_title'], album['kind'], album['year']) == ('Speak & Spell', 'album', '1981')
    assert (album['track_number'], album['disc_number'], album['total_tracks']) == (11, 1, 11)
    # the earliest pressing of the group, ties broken the same way every run
    assert album['release_id'] == 'speak-1'
    assert album['album_artist'] == 'Depeche Mode'


def test_a_single_mix_goes_to_its_single_and_a_compilation_only_song_stays():
    plan = plan_compilation(FakeMB(), 'best-of', TRACKS, {})
    to = {m['title']: m['to'] for m in plan['moves']}
    single = to['Enjoy the Silence (Single Mix)']
    assert (single['album_title'], single['kind'], single['year']) == ('Enjoy the Silence', 'single', '1990')
    assert plan['staying'] == [{'title': 'Martyr', 'why': 'only ever on compilations'}]


def test_other_compilations_promos_and_the_album_itself_are_never_the_origin():
    assert pick_original([
        _rel('a', 'rg-a', 'Album', '1979', 'Hits', secondary=['Compilation']),
        _rel('b', 'rg-b', 'Album', '1980', 'Live', secondary=['Live']),
        _rel('c', 'rg-c', 'Single', '1981', 'Song', status='Bootleg'),
        _rel('d', 'rg-d', 'Album', '', 'Undated'),
    ]) is None
    assert pick_original([_rel('x', 'rg-x', 'Album', '1982', 'Own')], not_group='rg-x') is None


def test_a_bonus_track_on_the_reissue_does_not_make_it_the_albums_song():
    """Shake the Disease: a 1985 single, on Black Celebration (1986) only from the remasters"""
    reissue = _rel('bc-2007', 'rg-bc', 'Album', '2007-03-26', 'Black Celebration')
    reissue['release-group']['first-release-date'] = '1986-03-17'
    single = _rel('std', 'rg-std', 'Single', '1985-04-29', 'Shake the Disease')
    assert pick_original([reissue, single])['album_title'] == 'Shake the Disease'
    # the same song on the album's own first pressing is the album's
    original = _rel('bc-1986', 'rg-bc', 'Album', '1986-03-17', 'Black Celebration')
    assert pick_original([reissue, original, single])['release_id'] == 'bc-1986'


def test_an_odd_edition_filed_with_the_album_does_not_make_it_the_albums_song():
    """Enjoy the Silence, the single mix: on one 8-track pressing in the Violator group"""
    odd = _rel('v-odd', 'rg-v', 'Album', '1990-03-19', 'Violator')
    single = _rel('ets', 'rg-ets', 'Single', '1990-02-05', 'Enjoy the Silence')
    sizes = {'rg-v': 60}
    assert pick_original([odd, single], group_size=sizes.get)['album_title'] == 'Enjoy the Silence'
    # on most of a small album's pressings: it is the album's
    assert pick_original([odd, single], group_size={'rg-v': 2}.get)['album_title'] == 'Violator'
    # nothing known about the album's size: taken at its word
    assert pick_original([odd, single], group_size={}.get)['album_title'] == 'Violator'


def test_the_track_number_is_read_off_a_standard_edition():
    def pressing(rid, date, media):
        release = _rel(rid, 'rg-v', 'Album', date, 'Violator')
        release['release-group']['first-release-date'] = '1990-03-19'
        release['media'] = media
        return release
    double_vinyl = pressing('a-vinyl', '1990-03-19', [{'format': '12" Vinyl'}, {'format': '12" Vinyl'}])
    cassette = pressing('b-tape', '1990-03-19', [{'format': 'Cassette'}])
    cd = pressing('c-cd', '1990-03-19', [{'format': 'CD'}])
    later_cd = pressing('d-cd', '2006-04-03', [{'format': 'CD'}])
    assert pick_original([double_vinyl, cassette, later_cd, cd])['release_id'] == 'c-cd'
    # no CD from that year: one disc still beats two
    assert pick_original([double_vinyl, cassette, later_cd])['release_id'] == 'b-tape'


def test_a_various_artists_compilation_and_a_plain_album_are_left_alone():
    various = [{'name': 'Various Artists', 'artist': {'id': VARIOUS_ARTISTS_MBID}}]
    assert plan_compilation(FakeMB(credit=various), 'best-of', TRACKS, {}) == {'reason': 'various_artists'}
    mb = FakeMB()
    mb.best_of['release-group']['secondary-types'] = []
    assert plan_compilation(mb, 'best-of', TRACKS, {}) == {'reason': 'not_a_compilation'}
    assert plan_compilation(mb, 'unknown', TRACKS, {}) == {'reason': 'lookup_failed'}


def test_a_guests_song_is_not_filed_under_the_guests_album():
    mb = FakeMB()
    mb.best_of['media'][0]['tracks'][0]['recording']['artist-credit'] = [
        {'name': 'Someone Else', 'artist': {'id': 'other'}}]
    plan = plan_compilation(mb, 'best-of', TRACKS[:1], {})
    assert plan['moves'] == []
    assert plan['staying'][0]['why'] == 'credited to another artist'


def test_a_long_list_of_releases_is_read_to_the_end():
    mb = FakeMB()
    filler = [_rel(f'c{n}', f'rg-c{n}', 'Album', '1990', f'Hits {n}', secondary=['Compilation'])
              for n in range(130)]
    mb.releases['rec-jcge'] = filler + mb.releases['rec-jcge']
    plan = plan_compilation(mb, 'best-of', TRACKS[:1], {})
    assert plan['moves'][0]['to']['album_title'] == 'Speak & Spell'
    assert [offset for rec, offset in mb.browsed if rec == 'rec-jcge'] == [0, 100]


# ── the job and the approve step, on real files ──────────────────────────────

def _library(imported_conn, legacy_db, tmp_path, only=None):
    conn = imported_conn
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Depeche Mode')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, year, musicbrainz_id) "
        "VALUES(?, 'The Best Of', 'compilation', 2006, 'best-of')", (artist,)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album, artist))
    subjects, paths = [], {}
    for number, title in enumerate(["Just Can't Get Enough", 'Enjoy the Silence (Single Mix)', 'Martyr'], 1):
        if only and title not in only:
            continue
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
            "VALUES(?,?,?,1,1)", (album, title, number)).lastrowid
        path = tmp_path / f'{number:02d}.flac'
        _make_flac(path, {'album': 'The Best Of', 'albumartist': 'Depeche Mode', 'title': title,
                          'artist': 'Depeche Mode', 'date': '2006', 'tracknumber': str(number)})
        file_id = conn.execute("INSERT INTO lib2_track_files(track_id, path, format) VALUES(?,?,'flac')",
                               (track, str(path))).lastrowid
        paths[title] = path
        subjects.append({'album_id': album, 'track_id': track, 'file_id': file_id, 'path': str(path),
                         'title': title, 'album_title': 'The Best Of', 'album_musicbrainz_id': 'best-of',
                         'album_artist_name': 'Depeche Mode', 'artist_name': 'Depeche Mode',
                         'artist_id': artist, 'owner_profile_id': None})
    conn.commit()
    return {'db': _DB(legacy_db), 'conn': conn, 'album': album, 'artist': artist,
            'subjects': subjects, 'paths': paths, 'tmp': tmp_path}


def _scan(lib, monkeypatch, mb=None):
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects",
                        lambda *_a, **_k: lib['subjects'])
    findings = []
    result = CompilationOriginsJob().scan(JobContext(
        db=lib['db'], config_manager=None, transfer_folder=str(lib['tmp']), mb_client=mb or FakeMB(),
        create_finding=lambda **kw: findings.append(kw) or True))
    return result, findings


def test_one_finding_per_compilation_says_where_its_songs_go(imported_conn, legacy_db, tmp_path, monkeypatch):
    lib = _library(imported_conn, legacy_db, tmp_path)
    result, findings = _scan(lib, monkeypatch)
    assert result.findings_created == 1
    finding = findings[0]
    assert finding['finding_type'] == 'compilation_track_origin'
    assert finding['entity_id'] == f"lib2:{lib['album']}"
    assert '2 of 3 songs' in finding['description']
    assert 'Speak & Spell (1981)' in finding['description']
    assert '1 stay on the compilation' in finding['description']


def test_approving_retags_the_files_and_moves_the_tracks(imported_conn, legacy_db, tmp_path, monkeypatch):
    lib = _library(imported_conn, legacy_db, tmp_path)
    _, findings = _scan(lib, monkeypatch)
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = lib['db'], None, str(tmp_path)
    out = worker._fix_compilation_track_origin(
        'album', findings[0]['entity_id'], None, findings[0]['details'])
    assert out['success'], out

    moved = FLAC(lib['paths']["Just Can't Get Enough"])
    assert moved['album'] == ['Speak & Spell']
    assert moved['date'] == ['1981-10-05']
    assert moved['tracknumber'][0].split('/')[0] == '11'
    assert moved['title'] == ["Just Can't Get Enough"]
    # the song that had nowhere to go is untouched
    assert FLAC(lib['paths']['Martyr'])['album'] == ['The Best Of']

    conn = lib['conn']
    albums = dict(conn.execute(
        "SELECT t.title, a.title FROM lib2_tracks t JOIN lib2_albums a ON a.id = t.album_id "
        "JOIN lib2_track_files f ON f.track_id = t.id").fetchall())
    assert albums["Just Can't Get Enough"] == 'Speak & Spell'
    assert albums['Enjoy the Silence (Single Mix)'] == 'Enjoy the Silence'
    assert albums['Martyr'] == 'The Best Of'
    # the albums it made are the same artist's
    assert conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE title='Speak & Spell'"
                        ).fetchone()[0] == lib['artist']
    # the new album is what MusicBrainz says it is, not a compilation, and it
    # holds only the song that moved: nothing is put on a wanted list
    kind, mbid, new_id = conn.execute(
        "SELECT album_type, musicbrainz_id, id FROM lib2_albums WHERE title='Speak & Spell'").fetchone()
    assert (kind, mbid) == ('album', 'speak-1')
    assert conn.execute("SELECT album_type FROM lib2_albums WHERE title='Enjoy the Silence'"
                        ).fetchone()[0] == 'single'
    fileless = conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id=? AND NOT EXISTS "
        "(SELECT 1 FROM lib2_track_files f WHERE f.track_id = t.id)", (new_id,)).fetchone()[0]
    assert fileless == 0


def test_a_song_that_left_the_album_since_the_scan_is_not_touched(imported_conn, legacy_db, tmp_path, monkeypatch):
    lib = _library(imported_conn, legacy_db, tmp_path)
    _, findings = _scan(lib, monkeypatch)
    other = lib['conn'].execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Elsewhere')", (lib['artist'],)).lastrowid
    lib['conn'].execute("UPDATE lib2_tracks SET album_id=?", (other,))
    lib['conn'].commit()
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = lib['db'], None, str(tmp_path)
    out = worker._fix_compilation_track_origin(
        'album', findings[0]['entity_id'], None, findings[0]['details'])
    assert out['action'] == 'nothing_to_change'
    assert FLAC(lib['paths']["Just Can't Get Enough"])['album'] == ['The Best Of']


def test_no_musicbrainz_is_an_error_not_a_silent_empty_scan(imported_conn, legacy_db, tmp_path, monkeypatch):
    lib = _library(imported_conn, legacy_db, tmp_path)
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects",
                        lambda *_a, **_k: lib['subjects'])
    result = CompilationOriginsJob().scan(JobContext(db=lib['db'], config_manager=None,
                                                     transfer_folder=str(tmp_path)))
    assert result.errors == 1 and result.findings_created == 0


# --- #2084 (wolf39us): "0 song(s) filed under their original albums" ---------

JCGE = "Just Can't Get Enough"


def _worker(lib, tmp_path):
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = lib['db'], None, str(tmp_path)
    return worker


def _album_of(conn, title):
    return conn.execute(
        "SELECT a.title FROM lib2_tracks t JOIN lib2_albums a ON a.id = t.album_id "
        "JOIN lib2_track_files f ON f.track_id = t.id WHERE t.title=?", (title,)).fetchone()[0]


def test_the_only_song_of_a_compilation_moves_and_the_compilation_keeps_its_name(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    # one song of the compilation in the library: "every file names another
    # album" read as a rename of the compilation, and nothing was filed
    from core.library2.metadata_overrides import get_field_overrides

    lib = _library(imported_conn, legacy_db, tmp_path, only={JCGE})
    _, findings = _scan(lib, monkeypatch)
    out = _worker(lib, tmp_path)._fix_compilation_track_origin(
        'album', findings[0]['entity_id'], None, findings[0]['details'])

    assert out['success'], out
    assert out['message'].startswith('1 song(s) filed')
    assert _album_of(lib['conn'], JCGE) == 'Speak & Spell'
    assert get_field_overrides(lib['conn'], entity_type='release_group', entity_id=lib['album']) == {}
    assert lib['conn'].execute("SELECT title FROM lib2_albums WHERE id=?",
                               (lib['album'],)).fetchone()[0] == 'The Best Of'


def test_values_taken_from_file_tags_do_not_stop_the_move(imported_conn, legacy_db, tmp_path, monkeypatch):
    # Use File Tags ran on this album first: its year and the song's number
    # are held as edits. the fix rewrites the file and the Library takes the
    # file again, so they are not in the way
    from core.library2.metadata_overrides import set_field_override

    lib = _library(imported_conn, legacy_db, tmp_path)
    track = next(s['track_id'] for s in lib['subjects'] if s['title'] == JCGE)
    for entity, eid, field, value in (('release_group', lib['album'], 'year', 2006),
                                      ('release_group', lib['album'], 'title', 'The Best Of'),
                                      ('track', track, 'track_number', 1)):
        set_field_override(lib['conn'], entity_type=entity, entity_id=eid, field_name=field,
                           value=value, reason='file tags')
    lib['conn'].commit()
    _, findings = _scan(lib, monkeypatch)
    out = _worker(lib, tmp_path)._fix_compilation_track_origin(
        'album', findings[0]['entity_id'], None, findings[0]['details'])

    assert out['success'], out
    moved = FLAC(lib['paths'][JCGE])
    assert moved['album'] == ['Speak & Spell'] and moved['date'] == ['1981-10-05']
    assert moved['tracknumber'][0].split('/')[0] == '11'
    assert _album_of(lib['conn'], JCGE) == 'Speak & Spell'


def test_a_title_typed_in_by_hand_still_keeps_the_song_where_it_is(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    from core.library2.metadata_overrides import set_field_override

    lib = _library(imported_conn, legacy_db, tmp_path, only={JCGE})
    _, findings = _scan(lib, monkeypatch)
    set_field_override(lib['conn'], entity_type='release_group', entity_id=lib['album'],
                       field_name='title', value='The Best Of', reason=None)
    lib['conn'].commit()
    out = _worker(lib, tmp_path)._fix_compilation_track_origin(
        'album', findings[0]['entity_id'], None, findings[0]['details'])

    assert out['action'] == 'nothing_to_change'
    assert FLAC(lib['paths'][JCGE])['album'] == ['The Best Of']


def test_a_compilation_the_old_fix_renamed_is_put_right_on_the_next_run(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    # what the bug left behind: the file already says Speak & Spell, and the
    # compilation carries that title as a value taken from the file
    from core.library2.metadata_overrides import get_field_overrides, set_field_override

    lib = _library(imported_conn, legacy_db, tmp_path, only={JCGE})
    _, findings = _scan(lib, monkeypatch)
    audio = FLAC(lib['paths'][JCGE])
    audio['album'] = ['Speak & Spell']
    audio.save()
    set_field_override(lib['conn'], entity_type='release_group', entity_id=lib['album'],
                       field_name='title', value='Speak & Spell', reason='file tags')
    lib['conn'].commit()

    out = _worker(lib, tmp_path)._fix_compilation_track_origin(
        'album', findings[0]['entity_id'], None, findings[0]['details'])

    assert out['success'], out
    assert out['message'].startswith('1 song(s) filed')
    assert 'title' not in get_field_overrides(lib['conn'], entity_type='release_group',
                                              entity_id=lib['album'])
    moved_to = lib['conn'].execute(
        "SELECT a.id, a.title FROM lib2_tracks t JOIN lib2_albums a ON a.id = t.album_id "
        "JOIN lib2_track_files f ON f.track_id = t.id WHERE t.title=?", (JCGE,)).fetchone()
    assert moved_to[1] == 'Speak & Spell' and moved_to[0] != lib['album']
