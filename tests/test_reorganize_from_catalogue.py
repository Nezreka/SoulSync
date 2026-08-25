"""The reorganize plan is computed from the library, not from a provider.

Reorganize moves files. Where they belong is a question about the album the
user already has, so the tracklist it names them after is the one in the
library — the same values the Library page shows.

Asking a metadata source instead is what produced the exceptions that
accumulated around the old planner (`_keep_user_casing` #1078 twice,
`_keep_user_year` #1080), each one added after a report, each one saying the
same thing: where the catalogue and the provider disagreed, the catalogue was
right. It is also why an album with no stored source id could not be
reorganized at all — for an operation that needs no provider.
"""

import core.library_reorganize as lr


def _album(**kw):
    base = {
        'id': 1, 'title': 'Real Album', 'artist_name': 'Real Artist',
        'year': '2021', 'release_date': '2021-05-01', 'track_count': 2,
        'spotify_album_id': None, 'itunes_album_id': None, 'deezer_id': None,
        'discogs_id': None, 'soul_id': None, 'musicbrainz_release_id': None,
    }
    base.update(kw)
    return base


def _track(n, title, disc=1, **kw):
    row = {'id': n, 'title': title, 'track_number': n, 'disc_number': disc,
           'artist_name': 'Real Artist', 'file_path': f'/music/{title}.flac',
           'duration': 200000}
    row.update(kw)
    return row


def _no_provider(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError('the catalogue planner must not consult a provider')
    monkeypatch.setattr(lr, '_resolve_source', _boom)


def test_an_album_with_no_source_id_is_still_planned(monkeypatch):
    _no_provider(monkeypatch)
    plan = lr.plan_album_reorganize(_album(), [_track(1, 'One'), _track(2, 'Two')])
    assert plan['status'] == 'planned'
    assert [it['matched'] for it in plan['items']] == [True, True]


def test_track_names_come_from_the_library(monkeypatch):
    _no_provider(monkeypatch)
    plan = lr.plan_album_reorganize(_album(), [_track(1, 'My Own Title')])
    assert plan['items'][0]['api_track']['name'] == 'My Own Title'
    assert plan['api_album']['name'] == 'Real Album'


def test_disc_count_comes_from_the_catalogue(monkeypatch):
    _no_provider(monkeypatch)
    plan = lr.plan_album_reorganize(
        _album(), [_track(1, 'One', disc=1), _track(2, 'Two', disc=2)])
    assert plan['total_discs'] == 2


def test_a_track_the_library_cannot_name_is_unmatched(monkeypatch):
    _no_provider(monkeypatch)
    plan = lr.plan_album_reorganize(_album(), [_track(1, ''), _track(2, 'Two')])
    assert plan['items'][0]['matched'] is False
    assert plan['items'][0]['reason']
    assert plan['items'][1]['matched'] is True


def test_the_album_keeps_its_own_year(monkeypatch):
    """#1080 was a patch on the provider planner. Reading the catalogue makes
    the user's own release year the value by construction."""
    _no_provider(monkeypatch)
    plan = lr.plan_album_reorganize(_album(release_date='1999-01-01'), [_track(1, 'One')])
    assert plan['api_album']['release_date'] == '1999-01-01'


def test_an_album_with_no_tracks_is_reported_as_such(monkeypatch):
    _no_provider(monkeypatch)
    assert lr.plan_album_reorganize(_album(), [])['status'] == 'no_tracks'
