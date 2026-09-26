"""torrent album bundles that come untagged, and tagged compilations.

a RuTracker rip lands as 'Nosferatu_-_Beaver_Cleaver.flac' or
'02 - Dan Winter - Carry Your Heart - Radio Edit.flac' with no tags. the
whole stem never scored against the clean title because the artist is glued
on, so the album downloaded fine and then no track ever claimed its file.

a properly tagged compilation failed too: the staging scan reads album
artist first, so every file read as 'Various Artists' and the artist half of
the score sank it (same trap as #1263).

filename guesses only count when the guessed artist matches, so none of this
can pull in a file that isn't the track.
"""

from __future__ import annotations

import os

from core.downloads import staging as ds
from tests.downloads.test_downloads_staging import (  # noqa: F401 - reset_state is an autouse fixture
    _build_deps,
    _seed_task,
    _Track,
    reset_state,
)


def _staged(tmp_path, stem, **extra):
    path = tmp_path / 'staging' / f'{stem}.flac'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x')
    return {'full_path': str(path), 'title': stem, 'artist': '', **extra}


def _claims(tmp_path, files, name, artist):
    deps = _build_deps(transfer_path=str(tmp_path / 'transfer'), staging_files=files)
    _seed_task('t1')
    ok = ds.try_staging_match('t1', 'b1', _Track(name=name, artists=[artist]), deps)
    return ok, os.listdir(tmp_path / 'transfer') if ok else []


class TestUntaggedReleaseFilenames:
    def test_artist_underscore_title(self, tmp_path):
        ok, moved = _claims(tmp_path, [_staged(tmp_path, 'Nosferatu_-_Beaver_Cleaver')],
                            'Beaver Cleaver', 'Nosferatu')
        assert ok and moved == ['Nosferatu_-_Beaver_Cleaver.flac']

    def test_number_artist_title_with_a_dash_in_the_title(self, tmp_path):
        files = [
            _staged(tmp_path, '01 - 4 Tune Fairytales - Take Me 2 Wonderland (Extended Mix)'),
            _staged(tmp_path, '02 - Dan Winter - Carry Your Heart - Radio Edit'),
        ]
        ok, moved = _claims(tmp_path, files, 'Carry Your Heart - Radio Edit', 'Dan Winter')
        assert ok and moved == ['02 - Dan Winter - Carry Your Heart - Radio Edit.flac']

    def test_artist_album_number_title(self, tmp_path):
        ok, _ = _claims(tmp_path, [_staged(tmp_path, 'Nosferatu - Some Album - 03 - Beaver Cleaver')],
                        'Beaver Cleaver', 'Nosferatu')
        assert ok

    def test_a_different_artist_in_the_filename_is_not_claimed(self, tmp_path):
        ok, _ = _claims(tmp_path, [_staged(tmp_path, '05 - Someone Else - Beaver Cleaver')],
                        'Beaver Cleaver', 'Nosferatu')
        assert ok is False

    def test_a_title_with_a_dash_is_not_split_into_a_fake_artist(self, tmp_path):
        # 'Hold Me - Live' read as artist 'Hold Me', title 'Live'
        ok, _ = _claims(tmp_path, [_staged(tmp_path, 'Hold Me - Live')], 'Live', 'The Band')
        assert ok is False

    def test_tagged_files_never_use_filename_guesses(self, tmp_path):
        # the tags say a different song, the filename can't overrule them
        f = _staged(tmp_path, 'Nosferatu - Beaver Cleaver')
        f['title'] = 'Something Else'
        f['artist'] = 'Nosferatu'
        ok, _ = _claims(tmp_path, [f], 'Beaver Cleaver', 'Nosferatu')
        assert ok is False


class TestTaggedCompilation:
    def _va(self, tmp_path, performer):
        f = _staged(tmp_path, '04')
        f.update(title='Carry Your Heart - Radio Edit', artist='Various Artists',
                 track_artist=performer)
        return f

    def test_performer_tag_matches_through_various_artists(self, tmp_path):
        ok, moved = _claims(tmp_path, [self._va(tmp_path, 'Dan Winter')],
                            'Carry Your Heart - Radio Edit', 'Dan Winter')
        assert ok and moved == ['04.flac']

    def test_wrong_performer_still_rejected(self, tmp_path):
        ok, _ = _claims(tmp_path, [self._va(tmp_path, 'Other Guy')],
                        'Carry Your Heart - Radio Edit', 'Dan Winter')
        assert ok is False


def test_staging_scan_carries_the_track_artist(monkeypatch, tmp_path):
    import web_server

    (tmp_path / 'a.flac').write_bytes(b'x')
    monkeypatch.setattr(web_server, 'get_staging_path', lambda: str(tmp_path))
    monkeypatch.setattr(web_server, '_get_album_bundle_staging_path', lambda _b: None)
    web_server._staging_tag_cache.clear()
    monkeypatch.setattr(web_server, '_read_staging_file_metadata', lambda full, rel: {
        'title': 'T', 'artist': 'Dan Winter', 'albumartist': 'Various Artists',
        'album': 'Alb', 'track_number': 4, 'disc_number': 1,
    })
    files = web_server._get_staging_file_cache('b')
    web_server._staging_tag_cache.clear()
    assert files[0]['artist'] == 'Various Artists'       # unchanged for everyone else
    assert files[0]['track_artist'] == 'Dan Winter'
