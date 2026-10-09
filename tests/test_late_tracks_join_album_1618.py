"""#1618: tracks that arrive after their album is already on disk join it.

mateusguilherme: three nirvana tracks came in a later wishlist batch, hours
after their ten siblings, and got a 1980 finnish single's release id, date
and label. navidrome showed 10 + 3. three things let that happen:

- the verification wrapper pops batch_id before the inner pipeline runs, so
  no batched download ever registered for the album pass. the album pass,
  sibling adoption (#1000) and loose-track adoption never ran.
- the album preflight searched with the batch's 3 tracks, not the album's 14,
  and ignored the release the album was already pinned to.
- adopting the folder's tags left the other release's date, label and
  catalog number on the file.

real flac / mp3 files under tmp_path, mutagen reads them back. no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen import File as MutagenFile
from mutagen.flac import FLAC

import core.album_consistency as ac
from core.imports import pipeline

from tests.test_album_consistency_adopt import _make_flac

OURS = '50fd8098-b33e-46b4-8419-df0845cae368'
SINGLE = '3e25396c-5c66-4609-8e47-37f250d323c7'

SIBLING_TAGS = {'album': 'Nirvana', 'albumartist': 'Nirvana', 'musicbrainz_albumid': OURS,
                'date': '2002-10-29', 'label': 'Geffen', 'releasetype': 'album'}
SINGLE_TAGS = {'album': 'Nirvana', 'albumartist': 'Nirvana', 'musicbrainz_albumid': SINGLE,
               'date': '1980', 'label': 'Elite Rekords', 'catalognumber': 'NIRRI-1',
               'barcode': '6417', 'releasetype': 'single'}


class _NoNetwork:
    def __getattr__(self, name):
        raise AssertionError(f'adoption must not reach musicbrainz ({name})')


# -- the wrapper hands the batch through ------------------------------------

def _drive_wrapper(monkeypatch, context, batch_id):
    seen = {}
    monkeypatch.setattr(pipeline, 'post_process_matched_download',
                        lambda key, ctx, path, runtime, metadata_runtime=None:
                        seen.update(batch=pipeline.owning_batch_id(ctx), raw=ctx.get('batch_id')))
    try:
        pipeline.post_process_matched_download_with_verification(
            'k', context, '/nowhere.flac', 'T1', batch_id, runtime=object())
    except Exception:  # noqa: BLE001 - only what the inner run saw matters here
        pass
    return seen


def test_a_batched_download_reaches_the_album_pass_with_its_batch(monkeypatch):
    seen = _drive_wrapper(monkeypatch, {'batch_id': 'B1', 'task_id': 'T1', 'profile_id': 1}, 'B1')
    assert seen['batch'] == 'B1'
    # the inner run still can't fire the completion callbacks itself
    assert seen['raw'] is None


def test_a_staging_match_reaches_it_too(monkeypatch):
    # try_staging_match never puts batch_id in its context
    seen = _drive_wrapper(monkeypatch, {'profile_id': 1}, 'B2')
    assert seen['batch'] == 'B2'


def test_the_batch_does_not_outlive_the_inner_run(monkeypatch):
    context = {'batch_id': 'B1', 'task_id': 'T1', 'profile_id': 1}
    _drive_wrapper(monkeypatch, context, 'B1')
    assert '_owning_batch_id' not in context


# -- the album's track count and its pinned release --------------------------

def test_the_album_pass_scores_releases_against_the_whole_album(tmp_path, monkeypatch):
    counts = []
    monkeypatch.setattr(ac, '_resolve_album_release',
                        lambda album, artist, count, svc, barcode=None: counts.append(count))
    folder = tmp_path / 'Nirvana' / 'Nirvana'
    folder.mkdir(parents=True)
    files = []
    for i in (11, 12, 13):
        path = folder / f'{i}.flac'
        _make_flac(path, {'title': f't{i}'})
        files.append({'path': str(path), 'track_number': i, 'disc_number': 1, 'title': f't{i}'})

    ac.run_album_consistency(files, 'Nirvana', 'Nirvana', object(), total_tracks=14)

    assert counts == [14]


@pytest.mark.parametrize('total, files, expected', [(14, 3, 14), (0, 3, 3), ('14', 3, 14), (None, 2, 2), (2, 5, 5)])
def test_album_track_count(total, files, expected):
    assert ac._album_track_count(total, [{}] * files) == expected


# -- adoption takes the folder's release whole --------------------------------

def _folder_with_siblings(tmp_path, n=3):
    folder = tmp_path / 'Nirvana' / 'Nirvana'
    folder.mkdir(parents=True)
    for i in range(1, n + 1):
        _make_flac(folder / f'{i:02d}.flac', dict(SIBLING_TAGS, title=f's{i}'))
    return folder


def test_a_late_track_drops_the_other_releases_date_label_and_catalog(tmp_path):
    folder = _folder_with_siblings(tmp_path)
    new = folder / '11 - Rape Me.flac'
    _make_flac(new, dict(SINGLE_TAGS, title='Rape Me'))

    out = ac.adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])

    assert out['written'] == 1
    tags = FLAC(str(new))
    assert tags['musicbrainz_albumid'] == [OURS]
    assert tags['date'] == ['2002-10-29']
    assert tags['label'] == ['Geffen']
    assert tags['releasetype'] == ['album']
    assert 'catalognumber' not in tags
    assert 'barcode' not in tags
    assert tags['title'] == ['Rape Me']


def test_the_album_batch_path_does_the_same(tmp_path):
    folder = _folder_with_siblings(tmp_path)
    infos = []
    for i, title in enumerate(['Rape Me', 'Dumb', 'All Apologies (Live)'], 11):
        path = folder / f'{i} - {title}.flac'
        _make_flac(path, dict(SINGLE_TAGS, title=title))
        infos.append({'path': str(path), 'track_number': i, 'disc_number': 1, 'title': title})

    out = ac.run_album_consistency(infos, 'Nirvana', 'Nirvana', _NoNetwork(), total_tracks=14)

    assert out['adopted'] and out['tags_written'] == 3
    for info in infos:
        tags = FLAC(info['path'])
        assert tags['musicbrainz_albumid'] == [OURS]
        assert tags['date'] == ['2002-10-29'] and tags['label'] == ['Geffen']
        assert 'catalognumber' not in tags


def test_a_track_already_on_the_folders_release_keeps_its_extra_fields(tmp_path):
    # nothing came from another release, so nothing gets cleared
    folder = _folder_with_siblings(tmp_path)
    new = folder / '11.flac'
    _make_flac(new, dict(SIBLING_TAGS, title='x', catalognumber='493 523-2'))

    ac.adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])

    assert FLAC(str(new))['catalognumber'] == ['493 523-2']


def _make_mp3(path: Path, tags: dict):
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    path.write_bytes(frame * 20)
    audio = MutagenFile(str(path))
    audio.add_tags()
    for key, value in tags.items():
        if key in ('album', 'albumartist'):
            ac._write_standard_tag(audio, key, value)
        else:
            ac._write_tag_to_file(audio, key, value)
    audio.save()


def test_mp3_date_and_label_live_in_their_native_frames(tmp_path):
    folder = tmp_path / 'Nirvana' / 'Nirvana'
    folder.mkdir(parents=True)
    for i in (1, 2):
        _make_mp3(folder / f'{i:02d}.mp3', {'album': 'Nirvana', 'MUSICBRAINZ_RELEASE_ID': OURS,
                                             'DATE': '2002', 'LABEL': 'Geffen'})
    new = folder / '11.mp3'
    _make_mp3(new, {'album': 'Nirvana', 'MUSICBRAINZ_RELEASE_ID': SINGLE, 'DATE': '1980',
                    'LABEL': 'Elite Rekords', 'CATALOGNUMBER': 'NIRRI-1'})

    out = ac.adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])

    assert out['written'] == 1
    tags = MutagenFile(str(new)).tags
    assert tags.getall('TXXX:MusicBrainz Album Id')[0].text == [OURS]
    assert str(tags.getall('TDRC')[0].text[0]) == '2002'
    assert tags.getall('TPUB')[0].text == ['Geffen']
    assert not tags.getall('TXXX:CATALOGNUMBER')
