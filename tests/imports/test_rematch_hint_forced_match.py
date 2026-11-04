"""a re-identify the user picked by hand is filed as that track (#889)

the suzy case: a 4:26 file picked for a release whose track is shorter. the
tracklist matcher turned every pairing down on length, and the staged copy sat
in the import inbox while the review said it was being filed.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core import auto_import_worker as aiw
from core.auto_import_worker import _hinted_track_match

TRACKS = [{"id": "t1", "name": "TRUE LOVE"}, {"id": "t15", "name": "SUZY"}]


def _candidate(*files):
    return SimpleNamespace(name="15 - SUZY (1) [reid-1].flac", audio_files=list(files))


def test_the_chosen_track_stands_when_the_tracklist_match_turns_it_down():
    ident = {"method": "rematch_hint", "track_id": "t15"}
    [match] = _hinted_track_match(_candidate("/s/a.flac"), ident, TRACKS)
    assert match == {"track": TRACKS[1], "file": "/s/a.flac", "confidence": 1.0}


def test_only_a_hint_and_only_its_one_file():
    assert _hinted_track_match(_candidate("/s/a.flac"), {"method": "tags", "track_id": "t15"}, TRACKS) == []
    assert _hinted_track_match(_candidate("/s/a.flac", "/s/b.flac"),
                               {"method": "rematch_hint", "track_id": "t15"}, TRACKS) == []
    assert _hinted_track_match(_candidate("/s/a.flac"),
                               {"method": "rematch_hint", "track_id": "gone"}, TRACKS) == []
    # an object-shaped tracklist (typed Track) works the same
    typed = [SimpleNamespace(id="t15")]
    assert _hinted_track_match(_candidate("/s/a.flac"),
                               {"method": "rematch_hint", "track_id": "t15"}, typed)[0]["track"] is typed[0]


def _match(identification, monkeypatch):
    # the file is 4:26 and every track on the release is far shorter: the
    # tracklist matcher turns each pairing down on length
    monkeypatch.setattr(aiw, '_read_file_tags', lambda f: {
        'title': 'SUZY (1)', 'artist': '', 'album': '', 'track_number': 15,
        'disc_number': 1, 'duration_ms': 266000})
    tracks = [{'name': n, 'id': i, 'track_number': k, 'disc_number': 1,
               'duration_ms': 137000, 'artists': [{'name': 'Kanye West'}]}
              for k, (i, n) in enumerate([('t1', 'TRUE LOVE'), ('t15', 'SUZY')], start=1)]
    client = MagicMock()
    client.get_album = MagicMock(return_value={'id': 'a', 'name': 'DONDA 2',
                                               'tracks': {'items': tracks}})
    worker = aiw.AutoImportWorker.__new__(aiw.AutoImportWorker)
    candidate = aiw.FolderCandidate(path='/s', name='15 - SUZY (1) [reid-1].flac',
                                    audio_files=['/s/15 - SUZY (1) [reid-1].flac'],
                                    folder_hash='h')
    with patch('core.metadata_service.get_client_for_source', return_value=client), \
         patch('core.metadata_service.get_album_tracks_for_source', return_value=None):
        return worker._match_tracks(candidate, {
            'source': 'deezer', 'album_id': 'a', 'album_name': 'DONDA 2',
            'identification_confidence': 1.0, 'force_album_match': True, **identification})


def test_the_import_files_a_hinted_track_the_length_check_turned_down(monkeypatch):
    result = _match({'method': 'rematch_hint', 'track_id': 't15'}, monkeypatch)
    assert result is not None
    assert [m['track']['id'] for m in result['matches']] == ['t15']


def test_without_a_hint_the_length_check_still_decides(monkeypatch):
    assert _match({'method': 'tags', 'track_id': 't15'}, monkeypatch) is None
