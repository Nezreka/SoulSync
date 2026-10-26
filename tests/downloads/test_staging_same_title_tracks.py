"""Tracks that share one title must each claim THEIR file, by track number.

The parts of an audiobook are titled alike ('Der Fall', 'Der Fall', ...), so a
title score cannot tell the staged files apart and the first file in the folder
won every task. The pairing is then wrong: the length check runs the real
duration against another track's expectation and rejects it, and two tasks go
after one file, the second of which falls back to a fresh search.

The track number is only a tie-break. It never lifts a file past the title
threshold and never beats a clearly better title match.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core.downloads import staging as ds
from tests.downloads.test_downloads_staging import (  # noqa: F401 - reset_state is an autouse fixture
    _build_deps,
    _seed_task,
    reset_state,
)

TITLE = 'Der mysteriöse neue Schüler'


@dataclass
class _Numbered:
    name: str = TITLE
    artists: list = None
    album: str = 'Folge 15'
    track_number: int = 0
    disc_number: int = 0

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Lisa & Fiona']


def _file(tmp_path, number, title=TITLE, *, tag_number=True, disc=0, artist='Lisa & Fiona'):
    path = tmp_path / 'staging' / f'{number:02d} - {title}.mp3'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x')
    entry = {'full_path': str(path), 'title': title, 'artist': artist}
    if tag_number:
        entry['track_number'] = number
    if disc:
        entry['disc_number'] = disc
    return entry


def _claim(tmp_path, files, track, task_info=None):
    deps = _build_deps(transfer_path=str(tmp_path / 'transfer'), staging_files=files)
    _seed_task('t1', track_info=task_info)
    ok = ds.try_staging_match('t1', 'b1', track, deps)
    return ok, (os.listdir(tmp_path / 'transfer') if ok else [])


def test_each_task_claims_the_file_with_its_own_track_number(tmp_path):
    for wanted in (1, 3, 4):
        run = tmp_path / f'run{wanted}'
        files = [_file(run, n) for n in (1, 2, 3, 4)]
        ok, moved = _claim(run, files, _Numbered(track_number=wanted))
        assert ok and moved == [f'{wanted:02d} - {TITLE}.mp3'], wanted


def test_the_number_can_come_from_the_task_info_instead_of_the_track(tmp_path):
    files = [_file(tmp_path, n) for n in (1, 2, 3)]
    ok, moved = _claim(tmp_path, files, _Numbered(), task_info={'track_number': 3})
    assert ok and moved == [f'03 - {TITLE}.mp3']


def test_an_untagged_file_is_numbered_by_its_file_name(tmp_path):
    files = [_file(tmp_path, n, tag_number=False) for n in (1, 2, 3)]
    ok, moved = _claim(tmp_path, files, _Numbered(track_number=2))
    assert ok and moved == [f'02 - {TITLE}.mp3']


def test_without_a_wanted_number_the_first_file_still_wins(tmp_path):
    """No number to compare means the old behaviour, unchanged."""
    files = [_file(tmp_path, n) for n in (1, 2, 3)]
    ok, moved = _claim(tmp_path, files, _Numbered())
    assert ok and moved == [f'01 - {TITLE}.mp3']


def test_a_wrong_number_is_not_a_reason_to_refuse_the_only_candidate(tmp_path):
    ok, moved = _claim(tmp_path, [_file(tmp_path, 7)], _Numbered(track_number=2))
    assert ok and moved == [f'07 - {TITLE}.mp3']


def test_the_same_number_on_another_disc_does_not_count(tmp_path):
    files = [_file(tmp_path, 1, disc=2), _file(tmp_path, 1, disc=1)]
    # same name twice: tell them apart by the entries
    files[0]['full_path'] = str(tmp_path / 'staging' / 'disc2-01.mp3')
    (tmp_path / 'staging' / 'disc2-01.mp3').write_bytes(b'x')
    ok, moved = _claim(tmp_path, files, _Numbered(track_number=1, disc_number=1))
    assert ok and moved == [f'01 - {TITLE}.mp3']


def test_a_better_title_match_is_never_overruled_by_a_number(tmp_path):
    """The number is worth 0.02; a file that matches the title clearly better
    (here: the exact title against a longer one) keeps winning."""
    exact = _file(tmp_path, 9, title='Hello')
    longer = _file(tmp_path, 2, title='Hello World Again')
    ok, moved = _claim(tmp_path, [longer, exact], _Numbered(name='Hello', track_number=2))
    assert ok and moved == ['09 - Hello.mp3']


def test_a_file_below_the_title_threshold_is_not_lifted_by_its_number(tmp_path):
    ok, _ = _claim(tmp_path, [_file(tmp_path, 2, title='Something Else Entirely')],
                   _Numbered(track_number=2))
    assert ok is False


# --- private album-bundle staging: the number is the identity ----------------

def _private(batch_id, field):
    return field == 'album_bundle_private_staging'


def _claim_private(tmp_path, files, track, private=True):
    deps = _build_deps(
        transfer_path=str(tmp_path / 'transfer'), staging_files=files,
        get_batch_field=(lambda batch, field: field == 'album_bundle_private_staging') if private
        else (lambda batch, field: None),
    )
    _seed_task('t1')
    ok = ds.try_staging_match('t1', 'b1', track, deps)
    return ok, (os.listdir(tmp_path / 'transfer') if ok else [])


def _parts(tmp_path):
    """Parts titled alike; the part marker's length changes the title score, so
    a title-only match always prefers the shortest marker, never track 7."""
    base = 'Die verschwundene Meerjungfrau'
    titles = {1: f'{base} (Einleitung)', 7: f'{base} (Teil 05)',
              8: f'{base} (Teil 06)', 9: f'{base} (Teil 7)'}
    return [_file(tmp_path, n, title=t) for n, t in titles.items()]


def test_in_private_staging_the_number_beats_the_title_ranking(tmp_path):
    for wanted in (1, 7, 8, 9):
        run = tmp_path / f'run{wanted}'
        ok, moved = _claim_private(
            run, _parts(run), _Numbered(name='Die verschwundene Meerjungfrau', track_number=wanted))
        assert ok and moved[0].startswith(f'{wanted:02d} - '), (wanted, moved)


def test_outside_private_staging_a_better_title_still_wins(tmp_path):
    """A user's own staging folder is mixed; there the number only breaks ties."""
    ok, moved = _claim_private(tmp_path, _parts(tmp_path),
                               _Numbered(name='Die verschwundene Meerjungfrau', track_number=1), private=False)
    assert ok and not moved[0].startswith('01 - ')


def test_private_staging_does_not_claim_a_numbered_file_with_the_wrong_title(tmp_path):
    files = [_file(tmp_path, 7, title='Something Else Entirely'),
             _file(tmp_path, 2, title='Die verschwundene Meerjungfrau')]
    ok, moved = _claim_private(tmp_path, files,
                               _Numbered(name='Die verschwundene Meerjungfrau', track_number=7))
    assert ok and moved == ['02 - Die verschwundene Meerjungfrau.mp3']


def test_private_staging_without_a_matching_number_falls_back_to_the_best_title(tmp_path):
    base = 'Die verschwundene Meerjungfrau'
    files = [_file(tmp_path, 3, title=f'{base} (Teil 05)'), _file(tmp_path, 4, title=f'{base} (Teil 12)')]
    ok, moved = _claim_private(tmp_path, files, _Numbered(name=base, track_number=9))
    assert ok and moved[0].startswith(('03 - ', '04 - '))
