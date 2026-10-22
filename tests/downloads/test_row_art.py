"""Downloads page row art (core/downloads/row_art.py): the artist's photo on
every row, the library's album cover when the task carried none, and a name
cache so the 2s poll doesn't hit the db every time."""

from __future__ import annotations

from core.downloads import row_art as ra
from core.downloads import status as st
from database.music_database import MusicDatabase


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _lookup(artists=(), albums=(), clock=None, calls=None):
    calls = calls if calls is not None else {'artists': [], 'albums': []}

    def qa(names):
        calls['artists'].append(sorted(names))
        return [r for r in artists if r[0] in names]

    def qb(titles):
        calls['albums'].append(sorted(titles))
        return [r for r in albums if r[1] in titles]

    return ra.RowArtLookup(qa, qb, normalize=lambda u: f"n:{u}", clock=clock or _Clock()), calls


def test_rows_get_the_artist_photo_case_blind_and_first_artist_only():
    lookup, _ = _lookup(artists=[('marcøs', 'm.jpg'), ('leo birenberg', 'leo.jpg')])
    items = [{'artist': 'MARCØS'}, {'artist': 'Leo Birenberg & Zach Robinson'}, {'artist': 'Nobody'}]
    ra.attach_row_art(items, lookup)
    assert items[0]['artist_image'] == 'n:m.jpg'
    assert items[1]['artist_image'] == 'n:leo.jpg'
    assert 'artist_image' not in items[2]


def test_cover_fills_only_empty_art_and_only_from_the_same_artist():
    lookup, _ = _lookup(albums=[('marcøs', 'greatest hits', 'mh.jpg'), ('other', 'greatest hits', 'x.jpg')])
    items = [
        {'artist': 'MARCØS', 'album': 'Greatest Hits', 'artwork': ''},
        {'artist': 'MARCØS', 'album': 'Greatest Hits', 'artwork': 'own.jpg'},
        {'artist': 'Someone Else', 'album': 'Greatest Hits', 'artwork': ''},
    ]
    ra.attach_row_art(items, lookup)
    assert items[0]['artwork'] == 'n:mh.jpg'
    # the task's own art always wins
    assert items[1]['artwork'] == 'own.jpg'
    # a same-titled album by someone else is not this cover
    assert items[2]['artwork'] == ''


def test_answers_and_misses_are_cached_until_the_ttl():
    clock = _Clock()
    lookup, calls = _lookup(artists=[('a', 'a.jpg')], clock=clock)
    ra.attach_row_art([{'artist': 'A'}, {'artist': 'B'}], lookup)
    ra.attach_row_art([{'artist': 'A'}, {'artist': 'B'}], lookup)
    # the second poll asked the db nothing, the miss for B included
    assert calls['artists'] == [['a', 'b']]
    clock.t = ra._TTL_SECONDS + 1
    ra.attach_row_art([{'artist': 'A'}], lookup)
    assert calls['artists'][-1] == ['a']


def test_a_failing_lookup_never_costs_the_rows():
    def boom(_):
        raise RuntimeError('db gone')

    lookup = ra.RowArtLookup(boom, boom)
    items = [{'artist': 'A', 'album': 'X', 'artwork': ''}]
    ra.attach_row_art(items, lookup)
    assert items == [{'artist': 'A', 'album': 'X', 'artwork': ''}]


def test_the_db_queries_match_library_and_watchlist_by_lowercased_name(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES (1, 'MARCØS', '/library/a.jpg')")
    conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES (2, 'Bare', '')")
    conn.execute("INSERT INTO albums (id, artist_id, title, thumb_url) VALUES (1, 1, 'Marco''s House', '/library/c.jpg')")
    conn.execute("INSERT INTO watchlist_artists (artist_name, image_url) VALUES ('Watched One', 'http://w.jpg')")
    conn.commit()
    names = dict(db.get_artist_thumbs_by_names(['marcøs', 'watched one', 'bare', 'missing']))
    assert names == {'marcøs': '/library/a.jpg', 'watched one': 'http://w.jpg'}
    assert db.get_album_thumbs_by_titles(["marco's house"]) == [('marcøs', "marco's house", '/library/c.jpg')]
    assert db.get_artist_thumbs_by_names([]) == []


def test_the_unified_response_runs_the_art_hook_over_every_row():
    seen = []

    def hook(items):
        seen.append(len(items))
        for i in items:
            i['artist_image'] = 'x'

    deps = st.StatusDeps(
        config_manager=None, docker_resolve_path=lambda p: p,
        find_completed_file=lambda *a, **k: (None, None), make_context_key=lambda u, f: f,
        submit_post_processing=lambda *a: None, get_cached_transfer_data=lambda: {},
        get_persistent_download_history=lambda limit: [{'id': 1, 'title': 'T', 'artist_name': 'A'}],
        attach_row_art=hook,
    )
    out = st.build_unified_downloads_response(100, deps)
    assert seen == [1]
    assert out['downloads'][0]['artist_image'] == 'x'
