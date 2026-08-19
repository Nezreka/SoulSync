"""Lining an album's files up against a different release (album reassign).

TheHomeGuy: "Is there any way to re assign an album to a different artist? ...
i have had this happen when a featured artist is taken as the album artist."

The reassign reuses the re-identify machinery (#889): stage a copy of each
file with a hint naming the chosen release, and let the import pipeline re-file
them. Tags, folder and DB all come from the code that handles a fresh download,
so nothing drifts.

The genuinely new problem is WHICH local file becomes WHICH target track. A
track re-identify has one file and one chosen track; an album has to line up
two tracklists that may disagree on length, numbering and titles. An album is
many files, so a silent wrong guess is many misfiled tracks — leaving a file
UNMAPPED and visible is always the better failure.
"""

from __future__ import annotations

from core.imports.reassign_album import (
    TITLE_MATCH_FLOOR,
    build_reassign_plan,
    hint_fields_for,
    map_album_tracks,
    normalize_title,
    title_similarity,
)


def _local(track_number, title, id=None, path=None):
    return {'id': id or f'L{track_number}', 'title': title,
            'track_number': track_number, 'file_path': path or f'/music/{track_number}.flac'}


def _target(track_number, name, id=None, disc_number=1):
    return {'id': id or f'T{track_number}', 'name': name,
            'track_number': track_number, 'disc_number': disc_number}


# ── the happy path ───────────────────────────────────────────────────────────

def test_track_numbers_line_the_album_up(self_check=None):
    locals_ = [_local(1, 'One'), _local(2, 'Two'), _local(3, 'Three')]
    targets = [_target(1, 'One'), _target(2, 'Two'), _target(3, 'Three')]

    pairings = map_album_tracks(locals_, targets)

    assert [p.target_id for p in pairings] == ['T1', 'T2', 'T3']
    assert {p.matched_by for p in pairings} == {'track_number'}


def test_a_mangled_title_still_matches_on_its_number():
    """Track number is the signal that survives a bad tagger."""
    pairings = map_album_tracks([_local(2, 'trakc 02 unknown')], [_target(2, 'Real Title')])

    assert pairings[0].target_id == 'T2'
    assert pairings[0].target_title == 'Real Title'


def test_a_missing_number_falls_back_to_the_title():
    locals_ = [{'id': 'L1', 'title': 'Comfortably Numb', 'file_path': '/m/a.flac'}]
    targets = [_target(6, 'Comfortably Numb')]

    pairings = map_album_tracks(locals_, targets)

    assert pairings[0].target_id == 'T6'
    assert pairings[0].matched_by == 'title'


# ── refusing to guess ────────────────────────────────────────────────────────

def test_an_extra_local_track_is_left_unmapped():
    """A bonus track with no home on the target release. Reported, not guessed."""
    plan = build_reassign_plan([_local(1, 'One'), _local(2, 'Bonus')], [_target(1, 'One')])

    assert [p.local_title for p in plan.mapped] == ['One']
    assert [p.local_title for p in plan.unmapped] == ['Bonus']


def test_a_shorter_local_album_maps_what_it_can():
    plan = build_reassign_plan([_local(2, 'Two')], [_target(1, 'One'), _target(2, 'Two')])

    assert len(plan.mapped) == 1
    assert plan.mapped[0].target_id == 'T2'


def test_duplicate_track_numbers_are_not_guessed_at():
    """A flattened multi-disc album has two track 1s. Picking one would misfile
    the other, so the number pass skips them and the title pass decides."""
    locals_ = [_local(1, 'Disc One Opener'), _local(1, 'Disc Two Opener')]
    targets = [_target(1, 'Disc One Opener'), _target(1, 'Disc Two Opener', id='T1b')]

    pairings = map_album_tracks(locals_, targets)

    assert {p.matched_by for p in pairings} == {'title'}
    assert {p.target_id for p in pairings} == {'T1', 'T1b'}


def test_a_weak_title_match_is_refused():
    """Below the floor is unmapped, not "close enough". A wrong pairing files a
    track under a name it does not have."""
    pairings = map_album_tracks(
        [{'id': 'L1', 'title': 'Something Entirely Different', 'file_path': '/m/a.flac'}],
        [_target(1, 'Comfortably Numb')])

    assert pairings[0].mapped is False


def test_two_files_can_never_claim_the_same_target():
    """One-to-one, or one target track would be written twice and another
    never at all."""
    locals_ = [{'id': 'L1', 'title': 'Money', 'file_path': '/m/a.flac'},
               {'id': 'L2', 'title': 'Money', 'file_path': '/m/b.flac'}]
    targets = [_target(6, 'Money')]

    mapped = [p for p in map_album_tracks(locals_, targets) if p.mapped]

    assert len(mapped) == 1


def test_a_target_without_an_id_cannot_be_used():
    """The import needs the id to fetch the release; a half-match would stage a
    file against nothing."""
    pairings = map_album_tracks([_local(1, 'One')], [{'name': 'One', 'track_number': 1}])

    assert pairings[0].mapped is False


def test_an_empty_target_release_maps_nothing():
    plan = build_reassign_plan([_local(1, 'One')], [])

    assert plan.mapped == []
    assert len(plan.unmapped) == 1


def test_no_local_tracks_is_an_empty_plan():
    assert build_reassign_plan([], [_target(1, 'One')]).pairings == []


# ── title normalisation ──────────────────────────────────────────────────────

def test_normalisation_ignores_case_punctuation_and_accents():
    assert normalize_title("Björk - It's Oh So Quiet!") == normalize_title('BJORK  ITS OH SO QUIET')


def test_an_apostrophe_disagreement_still_matches():
    """Taggers and metadata sources disagree about apostrophes constantly.
    Turning them into spaces splits one word into two and drops the score
    below the floor, so "Don't Stop" would not match "Dont Stop"."""
    assert title_similarity("Don't Stop", 'Dont Stop') == 1.0
    assert title_similarity('Rock \u2019n\u2019 Roll', 'Rock n Roll') == 1.0


def test_edition_qualifiers_are_not_stripped():
    """A live take and the studio take are DIFFERENT tracks. Folding them
    together is how a live version gets filed as the studio one."""
    assert title_similarity('Money', 'Money - Live') < 1.0


def test_similarity_is_zero_against_nothing():
    assert title_similarity('', 'Money') == 0.0
    assert title_similarity('Money', None) == 0.0


def test_the_floor_is_high_enough_to_reject_a_different_song():
    assert title_similarity('Money', 'Time') < TITLE_MATCH_FLOOR


# ── the hint payload ─────────────────────────────────────────────────────────

def test_every_paired_file_carries_the_same_release_identity():
    """What actually makes the pipeline file them together under the new
    artist: one release, many tracks."""
    plan = build_reassign_plan([_local(1, 'One'), _local(2, 'Two')],
                               [_target(1, 'One'), _target(2, 'Two')])

    fields = [hint_fields_for(p, source='spotify', album_id='AL9', album_name='The Wall',
                              artist_id='AR9', artist_name='Pink Floyd')
              for p in plan.mapped]

    assert {f['album_id'] for f in fields} == {'AL9'}
    assert {f['artist_name'] for f in fields} == {'Pink Floyd'}
    assert [f['track_id'] for f in fields] == ['T1', 'T2']
    assert [f['track_number'] for f in fields] == [1, 2]


# ── staging and hints ────────────────────────────────────────────────────────

import sqlite3          # noqa: E402

import pytest          # noqa: E402

from core.imports.reassign_album import apply_album_reassign          # noqa: E402


@pytest.fixture()
def cursor():
    conn = sqlite3.connect(':memory:')
    conn.execute("""CREATE TABLE rematch_hints (
        id INTEGER PRIMARY KEY, staged_path TEXT, content_hash TEXT, source TEXT,
        isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT, track_title TEXT,
        album_name TEXT, artist_name TEXT, album_type TEXT, track_number INTEGER,
        disc_number INTEGER, replace_track_id INTEGER, exempt_dedup INTEGER,
        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        consumed_at TIMESTAMP)""")
    conn.commit()
    return conn.cursor()


def _apply(plan, cursor, staging_dir):
    return apply_album_reassign(
        plan, source='spotify', album_id='AL9', album_name='The Wall',
        artist_id='AR9', artist_name='Pink Floyd', album_type='album',
        staging_dir=str(staging_dir), cursor=cursor)


def test_every_mapped_file_is_staged_with_a_hint(tmp_path, cursor):
    library = tmp_path / 'library'
    library.mkdir()
    files = []
    for n in (1, 2):
        f = library / f'{n}.flac'
        f.write_bytes(b'audio')
        files.append(str(f))

    plan = build_reassign_plan(
        [_local(1, 'One', path=files[0]), _local(2, 'Two', path=files[1])],
        [_target(1, 'One'), _target(2, 'Two')])

    result = _apply(plan, cursor, tmp_path / 'staging')

    assert len(result['staged']) == 2
    assert result['failed'] == []
    rows = cursor.execute("SELECT album_id, artist_name, track_id FROM rematch_hints").fetchall()
    assert {r[0] for r in rows} == {'AL9'}
    assert {r[1] for r in rows} == {'Pink Floyd'}
    assert {r[2] for r in rows} == {'T1', 'T2'}


def test_the_original_files_are_copied_not_moved(tmp_path, cursor):
    """#889's invariant: the original is only removed AFTER the re-import
    succeeds. Moving here would destroy the album on any later failure."""
    library = tmp_path / 'library'
    library.mkdir()
    original = library / '1.flac'
    original.write_bytes(b'audio')

    plan = build_reassign_plan([_local(1, 'One', path=str(original))], [_target(1, 'One')])
    _apply(plan, cursor, tmp_path / 'staging')

    assert original.exists(), 'the library file was moved instead of copied'


def test_an_unreadable_track_does_not_sink_the_album(tmp_path, cursor):
    """Eleven good tracks must still be staged when the twelfth is gone."""
    library = tmp_path / 'library'
    library.mkdir()
    good = library / '1.flac'
    good.write_bytes(b'audio')

    plan = build_reassign_plan(
        [_local(1, 'One', path=str(good)),
         _local(2, 'Two', path=str(library / 'missing.flac'))],
        [_target(1, 'One'), _target(2, 'Two')])

    result = _apply(plan, cursor, tmp_path / 'staging')

    assert [s['title'] for s in result['staged']] == ['One']
    assert len(result['failed']) == 1
    assert 'no longer on disk' in result['failed'][0]['error']


def test_unmapped_tracks_are_reported_not_staged(tmp_path, cursor):
    library = tmp_path / 'library'
    library.mkdir()
    good = library / '1.flac'
    good.write_bytes(b'audio')

    plan = build_reassign_plan(
        [_local(1, 'One', path=str(good)), _local(2, 'Bonus', path=str(good))],
        [_target(1, 'One')])

    result = _apply(plan, cursor, tmp_path / 'staging')

    assert len(result['staged']) == 1
    assert [s['title'] for s in result['skipped']] == ['Bonus']
    assert cursor.execute("SELECT COUNT(*) FROM rematch_hints").fetchone()[0] == 1


def test_nothing_mapped_stages_nothing(tmp_path, cursor):
    plan = build_reassign_plan([_local(1, 'One')], [])

    result = _apply(plan, cursor, tmp_path / 'staging')

    assert result['staged'] == []
    assert cursor.execute("SELECT COUNT(*) FROM rematch_hints").fetchone()[0] == 0


def test_a_failed_hint_takes_the_staged_copy_back_out(tmp_path, cursor, monkeypatch):
    """An orphaned staged copy is NOT harmless: auto-import picks it up as an
    ordinary file and matches it however it likes, so the user ends up with the
    original AND a duplicate filed somewhere they never asked for."""
    library = tmp_path / 'library'
    library.mkdir()
    original = library / '1.flac'
    original.write_bytes(b'audio')
    staging = tmp_path / 'staging'

    import core.imports.rematch_hints as hints_mod
    monkeypatch.setattr(hints_mod, 'create_hint',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db full')))

    plan = build_reassign_plan([_local(1, 'One', path=str(original))], [_target(1, 'One')])
    result = apply_album_reassign(
        plan, source='spotify', album_id='AL9', album_name='X', artist_id='AR9',
        artist_name='Y', album_type='album', staging_dir=str(staging), cursor=cursor)

    assert result['staged'] == []
    assert len(result['failed']) == 1
    leftovers = list(staging.glob('*')) if staging.exists() else []
    assert leftovers == [], f'staged copy left behind: {leftovers}'
    assert original.exists(), 'the original must never be touched'


def test_non_latin_titles_survive_normalisation():
    """An ASCII-only character class reduced any Japanese, Korean, Cyrillic or
    Greek title to an EMPTY string, so two IDENTICAL titles scored 0.0 and
    could never match by title. This project explicitly cares about non-Latin
    releases (see the Audio/Foreign category note in prowlarr_client.py)."""
    for title in ('君の名は', 'Пинк Флойд', '아이유', 'Καλημέρα'):
        assert normalize_title(title) != '', f'{title} normalised to nothing'
        assert title_similarity(title, title) == 1.0


def test_two_different_non_latin_titles_still_do_not_match():
    assert title_similarity('君の名は', '天気の子') < TITLE_MATCH_FLOOR


def test_non_latin_case_and_punctuation_still_fold():
    assert normalize_title('Пинк, Флойд!') == normalize_title('пинк флойд')


def test_a_non_latin_album_maps_by_title():
    """The end-to-end consequence: a release whose track numbers disagree can
    still be lined up by title."""
    locals_ = [{'id': 'L1', 'title': '君の名は', 'file_path': '/m/1.flac'}]
    targets = [{'id': 'T5', 'name': '君の名は', 'track_number': 5}]

    pairings = map_album_tracks(locals_, targets)

    assert pairings[0].target_id == 'T5'
    assert pairings[0].matched_by == 'title'
