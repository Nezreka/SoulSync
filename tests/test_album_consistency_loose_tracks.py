"""a single downloaded track joins the album already in its folder.

the album batch path runs run_album_consistency, whose step 0 (#1000) adopts
the album-level tags from files already on disk. a single track never got
there (lifecycle gates on is_album_download AND 2+ files), so it kept the
musicbrainz release it resolved for itself, and a different release id
splits the album on navidrome. achilles4: "download two songs from the same
album ... they will be different in Navidrome."

real flac files under tmp_path, mutagen reads them back. no network.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.flac import FLAC

from core.album_consistency import adopt_sibling_tags_for_loose_tracks

from tests.test_album_consistency_adopt import _make_flac

REL_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
REL_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
RG_A = 'cccccccc-cccc-cccc-cccc-cccccccccccc'


def _album_folder(tmp_path, n=2, album='Rebuild', artist='Achilles', release=REL_A):
    folder = tmp_path / artist / album
    folder.mkdir(parents=True)
    for i in range(1, n + 1):
        _make_flac(folder / f'{i:02d} - old {i}.flac', {
            'title': f'old {i}', 'artist': artist, 'album': album, 'albumartist': artist,
            'tracknumber': str(i), 'musicbrainz_albumid': release, 'musicbrainz_releasegroupid': RG_A,
            'releasetype': 'album', 'originaldate': '2019-04-05',
        })
    return folder


def _new_track(folder, name='09 - new.flac', album='Rebuild', release=REL_B, extra=None):
    path = folder / name
    tags = {'title': 'new', 'artist': 'Achilles', 'album': album, 'albumartist': 'Achilles',
            'tracknumber': '9', 'musicbrainz_albumid': release}
    tags.update(extra or {})
    _make_flac(path, tags)
    return path


def test_a_loose_track_adopts_the_folders_release(tmp_path):
    folder = _album_folder(tmp_path)
    before = {p.name: FLAC(str(p)).tags.as_dict() for p in folder.glob('*.flac')}
    new = _new_track(folder)

    out = adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])

    assert out['written'] == 1 and out['gated'] == 0 and out['errors'] == 0
    tags = FLAC(str(new))
    assert tags['musicbrainz_albumid'] == [REL_A]
    assert tags['musicbrainz_releasegroupid'] == [RG_A]
    assert tags['originaldate'] == ['2019-04-05']
    # track-level fields are the file's own
    assert tags['title'] == ['new'] and tags['tracknumber'] == ['9']
    # the siblings were read, never written
    after = {p.name: FLAC(str(p)).tags.as_dict() for p in folder.glob('*.flac') if p.name != new.name}
    assert after == before


def test_a_track_whose_album_differs_from_the_folder_is_left_alone(tmp_path):
    # filed into the wrong folder, or a folder of loose singles: joining it to
    # that album would be the bug this exists to prevent
    folder = _album_folder(tmp_path)
    new = _new_track(folder, album='Something Else')

    out = adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])

    assert out['gated'] == 1 and out['written'] == 0
    assert FLAC(str(new))['musicbrainz_albumid'] == [REL_B]
    assert FLAC(str(new))['album'] == ['Something Else']


def test_case_and_punctuation_do_not_block_the_join(tmp_path):
    folder = _album_folder(tmp_path, album='Black Holes & Revelations')
    new = _new_track(folder, album='black holes and revelations'.replace(' and ', ' '))
    out = adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])
    assert out['written'] == 1
    assert FLAC(str(new))['album'] == ['Black Holes & Revelations']


def test_an_edition_qualifier_does_block_it(tmp_path):
    folder = _album_folder(tmp_path)
    new = _new_track(folder, album='Rebuild (Deluxe)')
    out = adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])
    assert out['gated'] == 1
    assert FLAC(str(new))['musicbrainz_albumid'] == [REL_B]


def test_a_track_with_no_album_tag_joins_the_folder(tmp_path):
    folder = _album_folder(tmp_path)
    path = folder / '09 - bare.flac'
    _make_flac(path, {'title': 'bare', 'musicbrainz_albumid': REL_B})
    out = adopt_sibling_tags_for_loose_tracks([{'path': str(path)}])
    assert out['written'] == 1
    tags = FLAC(str(path))
    assert tags['album'] == ['Rebuild'] and tags['albumartist'] == ['Achilles']
    assert tags['musicbrainz_albumid'] == [REL_A]


def test_two_new_tracks_do_not_vote_for_each_other(tmp_path):
    # both arrive with release B; the folder's two existing files say A. if
    # the new files counted as siblings of each other, B would tie A
    folder = _album_folder(tmp_path, n=2)
    n1 = _new_track(folder, '09 - new1.flac')
    n2 = _new_track(folder, '10 - new2.flac')
    out = adopt_sibling_tags_for_loose_tracks([{'path': str(n1)}, {'path': str(n2)}])
    assert out['written'] == 2
    assert FLAC(str(n1))['musicbrainz_albumid'] == [REL_A]
    assert FLAC(str(n2))['musicbrainz_albumid'] == [REL_A]


def test_a_fresh_folder_has_nothing_to_adopt(tmp_path):
    folder = tmp_path / 'Achilles' / 'Brand New'
    folder.mkdir(parents=True)
    new = _new_track(folder, album='Brand New')
    out = adopt_sibling_tags_for_loose_tracks([{'path': str(new)}])
    assert out == {'written': 0, 'gated': 0, 'no_siblings': 1, 'errors': 0, 'total_files': 1}
    assert FLAC(str(new))['musicbrainz_albumid'] == [REL_B]


def test_missing_files_and_empty_input_are_harmless(tmp_path):
    assert adopt_sibling_tags_for_loose_tracks([])['total_files'] == 0
    out = adopt_sibling_tags_for_loose_tracks([{'path': str(tmp_path / 'gone.flac')}, {'path': None}])
    assert out['written'] == 0 and out['errors'] == 0


def test_the_file_lock_is_taken_for_every_write(tmp_path):
    folder = _album_folder(tmp_path)
    new = _new_track(folder)
    locked = []

    class _Lock:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            locked.append(self.path)
        def __exit__(self, *a):
            return False

    adopt_sibling_tags_for_loose_tracks([{'path': str(new)}], file_lock_fn=_Lock)
    assert locked == [str(new)]


def test_lifecycle_runs_it_where_the_album_pass_does_not(tmp_path):
    # both completion paths gate the album pass on is_album_download + 2 files;
    # the loose-track pass must sit in the else of BOTH, or one path still splits
    src = Path('core/downloads/lifecycle.py').read_text(encoding='utf-8')
    album_gates = src.count("if batch.get('is_album_download') and _cons_files and len(_cons_files) >= 2:")
    loose_calls = src.count('_adopt_loose_tracks(_cons_files,')
    assert album_gates == 2
    assert loose_calls == 2
