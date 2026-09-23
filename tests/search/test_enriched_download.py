"""enriched downloads from basic search: one provider end to end, files mapped
to the release on the server with the import matcher."""

from __future__ import annotations

import pytest

from core.search import enriched_download as ed


def _file(title, n, filename=None, **over):
    base = {
        'username': 'peer',
        'filename': filename or rf'Music\Radiohead\Glastonbury\{n:02d} - {title}.flac',
        'title': title, 'artist': 'Radiohead', 'album': 'Glastonbury 2003',
        'track_number': n, 'duration': 240_000 + n * 1000, 'size': 30_000_000,
    }
    base.update(over)
    return base


def _tracklist(*titles):
    return [
        {'id': f't{i}', 'name': t, 'track_number': i + 1, 'disc_number': 1,
         'duration_ms': 240_000 + (i + 1) * 1000, 'artists': [{'name': 'Radiohead'}]}
        for i, t in enumerate(titles)
    ]


class TestFetchRelease:
    def test_uses_only_the_provider_asked_for(self):
        seen = {}

        def fake(album_id, **kw):
            seen.update(kw, album_id=album_id)
            return {'success': True, 'source': 'deezer', 'album': {'name': 'X'}, 'tracks': _tracklist('a', 'b')}

        release = ed.fetch_release('deezer', '42', 'X', 'Radiohead', get_album_tracks=fake)
        assert seen['source_override'] == 'deezer' and seen['album_id'] == '42'
        assert release['album']['source'] == 'deezer'
        assert release['total_discs'] == 1
        assert len(release['tracks']) == 2

    def test_refuses_an_answer_from_a_different_provider(self):
        # the old modal's bug: ask deezer, quietly get itunes ids back
        def fake(album_id, **kw):
            return {'success': True, 'source': 'itunes', 'album': {}, 'tracks': []}

        with pytest.raises(ed.ProviderMismatch):
            ed.fetch_release('deezer', '42', 'X', 'Radiohead', get_album_tracks=fake)

    def test_a_failed_lookup_says_so(self):
        with pytest.raises(LookupError):
            ed.fetch_release('deezer', '42', 'X', 'R',
                             get_album_tracks=lambda *a, **k: {'success': False, 'error': 'gone'})

    def test_counts_discs(self):
        tracks = _tracklist('a', 'b')
        tracks[1]['disc_number'] = 2
        release = ed.fetch_release('deezer', '1', 'X', 'R', get_album_tracks=lambda *a, **k: {
            'success': True, 'source': 'deezer', 'album': {}, 'tracks': tracks})
        assert release['total_discs'] == 2
        assert release['album']['total_discs'] == 2


class TestMatchFiles:
    def test_real_matcher_maps_each_file_to_its_track(self):
        files = [_file('Lucky', 2), _file('2 + 2 = 5', 1), _file('The National Anthem', 3)]
        tracks = _tracklist('2 + 2 = 5', 'Lucky', 'The National Anthem')
        out = ed.match_files(files, tracks, 'Glastonbury 2003')
        assert [o['track_index'] for o in out] == [1, 0, 2]
        assert all(o['confidence'] >= 0.4 for o in out)
        assert out[0]['file_key'] == ed.file_key(files[0])

    def test_a_file_that_matches_nothing_is_unassigned(self):
        files = [_file('Lucky', 2), _file('Completely Unrelated Interview', 9, duration=900_000)]
        out = ed.match_files(files, _tracklist('2 + 2 = 5', 'Lucky'), 'Glastonbury 2003')
        assert out[0]['track_index'] == 1
        assert out[1]['track_index'] is None

    def test_untagged_soulseek_paths_still_match_by_filename(self):
        # no title from the result: the backslash path has to be split
        files = [_file('', 1, filename=r'Music\Radiohead\Live\01 - Lucky.flac')]
        files[0]['title'] = None
        out = ed.match_files(files, _tracklist('Lucky'), 'Live')
        assert out[0]['track_index'] == 0


class TestTrackInfo:
    def test_album_tasks_carry_the_explicit_release(self):
        album = ed.album_context({'id': 'a1', 'name': 'Live', 'release_date': '2003-06-28',
                                  'images': [{'url': 'http://img'}], 'artists': [{'name': 'Radiohead'}],
                                  'total_tracks': 21, 'total_discs': 1}, 'deezer', 'Radiohead')
        info = ed.enriched_track_info(_tracklist('Lucky')[0], album, 'deezer', as_album=True)
        assert info['_is_explicit_album_download'] is True
        assert info['_explicit_album_context']['name'] == 'Live'
        assert info['_explicit_album_context']['image_url'] == 'http://img'
        assert info['_explicit_artist_context']['name'] == 'Radiohead'
        assert info['source'] == 'deezer' and info['album']['source'] == 'deezer'
        assert info['track_number'] == 1

    def test_a_single_is_not_forced_into_album_routing(self):
        album = ed.album_context({'id': 'a1', 'name': 'Lucky', 'album_type': 'single'}, 'deezer', 'Radiohead')
        info = ed.enriched_track_info({'id': 't', 'name': 'Lucky'}, album, 'deezer', as_album=False)
        assert '_is_explicit_album_download' not in info
        assert info['artists'] == [{'name': 'Radiohead'}]


class TestSingleTrackDetails:
    def test_spotify_never_falls_back(self):
        calls = {}

        class Client:
            def get_track_details(self, track_id, allow_fallback=True):
                calls['fallback'] = allow_fallback
                return {'id': track_id}

        assert ed.single_track_details('spotify', 'x', client_for_source=lambda s: Client()) == {'id': 'x'}
        assert calls['fallback'] is False

    def test_unavailable_provider_is_none_not_a_crash(self):
        assert ed.single_track_details('discogs', 'x', client_for_source=lambda s: None) is None
