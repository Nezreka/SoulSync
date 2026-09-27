"""The discovery inbox (plan phase 6)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.discovery import inbox
from core.discovery.feedback import record
from database.music_database import MusicDatabase

TODAY = date(2026, 9, 26)


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    d.add_artist_to_watchlist('sp-tool', 'Tool', profile_id=1, source='spotify')
    d.add_artist_to_watchlist('sp-soen', 'Soen', profile_id=1, source='spotify')
    return d


def _album(db, artist, name, released, pid=1, cover=''):
    db.cache_discovery_recent_album({
        'album_spotify_id': f'al-{artist}-{name}', 'artist_name': artist, 'album_name': name,
        'release_date': released, 'album_cover_url': cover, 'album_type': 'album',
        'artist_spotify_id': f'sp-{artist}'}, source='spotify', profile_id=pid)


def _days(n):
    return (TODAY + timedelta(days=n)).isoformat()


def _titles(db, view='new', pid=1):
    return [(r['kind'], r['title']) for r in inbox.list_items(db, pid, view)]


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def test_watchlist_releases_are_news_and_future_ones_are_upcoming(db):
    _album(db, 'Tool', 'Fear Inoculum', _days(-3))
    _album(db, 'Soen', 'Memorial', _days(-60))           # too old to be news
    _album(db, 'Soen', 'Reliance', _days(20))
    _album(db, 'Deftones', 'Private Music', _days(-1))   # not on this watchlist
    inbox.collect_releases(db, 1, TODAY)
    assert _titles(db) == [('new_release', 'Fear Inoculum'), ('upcoming', 'Reliance')]


def test_the_new_view_orders_news_newest_first_and_whats_coming_soonest_first(db):
    for name, n in (('A', -9), ('B', -2), ('C', 40), ('D', 5)):
        _album(db, 'Tool', name, _days(n))
    inbox.collect_releases(db, 1, TODAY)
    assert [t for _, t in _titles(db)] == ['B', 'A', 'D', 'C']


def test_concerts_are_off_until_ticketmaster_is_set_up(db, monkeypatch):
    monkeypatch.setattr('core.concerts_client.ticketmaster_configured', lambda: False)
    assert inbox.collect_concerts(db, 1) is None


def test_concerts_keep_the_artists_that_answered(db):
    def lookup(name):
        if name == 'Tool':
            return {'configured': True, 'events': [
                {'datetime': f'{_days(30)}T20:00:00Z', 'venue': 'Forum', 'city': 'LA',
                 'tickets_url': 'https://tm/1'}]}
        return {'configured': True, 'events': [], 'error': 'rate limited'}
    assert inbox.collect_concerts(db, 1, lookup) == 1
    item = inbox.list_items(db, 1)[0]
    assert (item['kind'], item['title'], item['artist_name']) == ('concert', 'Forum · LA', 'Tool')
    assert item['payload']['url'] == 'https://tm/1'


def test_a_source_that_does_not_answer_is_reported_and_the_rest_still_land(db):
    _album(db, 'Tool', 'Fear Inoculum', _days(-3))
    result = inbox.refresh(db, 1, today=TODAY,
                           concerts=lambda name: {'events': [], 'error': 'Ticketmaster returned 503'})
    assert result['sources']['releases'] == {'state': 'ok', 'count': 1}
    assert result['sources']['concerts']['state'] == 'failed'
    assert inbox.unanswered(inbox.status(db, 1)) == ['concerts']
    assert _titles(db) == [('new_release', 'Fear Inoculum')]


# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------

def test_a_refresh_never_resets_what_someone_chose(db):
    _album(db, 'Tool', 'Fear Inoculum', _days(-3))
    inbox.collect_releases(db, 1, TODAY)
    item = inbox.list_items(db, 1)[0]
    assert inbox.set_state(db, 1, item['id'], 'dismissed')
    inbox.collect_releases(db, 1, TODAY)
    assert inbox.list_items(db, 1) == []
    assert inbox.unread_count(db, 1) == 0


def test_saved_items_leave_new_and_stay_in_saved(db):
    _album(db, 'Tool', 'Fear Inoculum', _days(-3))
    inbox.collect_releases(db, 1, TODAY)
    item = inbox.list_items(db, 1)[0]
    inbox.set_state(db, 1, item['id'], 'saved')
    assert _titles(db) == []
    assert _titles(db, 'saved') == [('new_release', 'Fear Inoculum')]
    assert not inbox.set_state(db, 1, item['id'], 'deleted')


def test_a_saved_rec_starts_saved_and_is_not_news(db):
    assert inbox.save_rec(db, 1, {'type': 'artist', 'name': 'Karnivool'},
                          {'kind': 'listened', 'seeds': [{'name': 'Tool'}]})
    assert inbox.save_rec(db, 1, {'type': 'track', 'name': 'Lotus', 'artist_name': 'Soen'})
    assert inbox.save_rec(db, 1, {'type': 'track', 'name': 'Lotus'}) is None   # no artist
    assert inbox.unread_count(db, 1) == 0
    assert {t for _, t in _titles(db, 'saved')} == {'Karnivool', 'Lotus'}


def test_dismiss_all_clears_only_the_new(db):
    _album(db, 'Tool', 'A', _days(-3))
    _album(db, 'Tool', 'B', _days(-2))
    inbox.collect_releases(db, 1, TODAY)
    inbox.save_rec(db, 1, {'type': 'artist', 'name': 'Karnivool'})
    assert inbox.dismiss_all_unread(db, 1) == 2
    assert _titles(db) == [] and len(_titles(db, 'saved')) == 1


def test_added_is_observed_not_clicked(db):
    _album(db, 'Tool', 'Fear Inoculum', _days(-3))
    inbox.save_rec(db, 1, {'type': 'artist', 'name': 'Karnivool'})
    inbox.refresh(db, 1, today=TODAY, concerts=lambda n: {'events': []})
    assert inbox.unread_count(db, 1) == 1
    # it's in the library now, and the saved artist got watched
    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name) VALUES (9, 'Tool')")
    conn.execute("INSERT INTO albums (id, title, artist_id) VALUES (90, 'Fear Inoculum', 9)")
    conn.commit()
    conn.close()
    db.add_artist_to_watchlist('sp-kv', 'Karnivool', profile_id=1, source='spotify')
    inbox.refresh(db, 1, today=TODAY, concerts=lambda n: {'events': []})
    assert inbox.unread_count(db, 1) == 0
    assert _titles(db, 'saved') == []


def test_whats_passed_is_pruned(db):
    _album(db, 'Tool', 'Soon', _days(2))
    inbox.collect_releases(db, 1, TODAY)
    inbox.collect_concerts(db, 1, lambda n: {'events': [
        {'datetime': _days(1), 'venue': 'Forum', 'city': 'LA'}] if n == 'Tool' else []})
    assert len(inbox.list_items(db, 1)) == 2
    # three days later: the album is out (it comes back as news) and the gig is over
    inbox._prune(db, 1, TODAY + timedelta(days=3))
    assert inbox.list_items(db, 1) == []


# ---------------------------------------------------------------------------
# what never shows, and whose it is
# ---------------------------------------------------------------------------

def test_blocked_and_not_now_artists_never_show(db):
    _album(db, 'Tool', 'Fear Inoculum', _days(-3))
    _album(db, 'Soen', 'Memorial', _days(-3))
    inbox.collect_releases(db, 1, TODAY)
    db.add_blocklist_entry(1, 'artist', 'Tool')
    record(db, 1, 'not_now', {'type': 'album', 'name': 'Memorial', 'artist_name': 'Soen'})
    assert inbox.list_items(db, 1) == []
    assert inbox.unread_count(db, 1) == 0


def test_the_inbox_is_per_profile(db):
    other = db.create_profile(name='sam')
    _album(db, 'Tool', 'Fear Inoculum', _days(-3), pid=other)
    inbox.collect_releases(db, other, TODAY)       # Tool isn't on sam's watchlist
    assert inbox.list_items(db, other) == []
    db.add_artist_to_watchlist('sp-tool', 'Tool', profile_id=other, source='spotify')
    inbox.collect_releases(db, other, TODAY)
    assert len(inbox.list_items(db, other)) == 1
    assert inbox.list_items(db, 1) == []
    item = inbox.list_items(db, other)[0]
    assert not inbox.set_state(db, 1, item['id'], 'dismissed'), "one profile changed another's"


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(db, monkeypatch):
    import web_server
    import api.discover_routes as routes

    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: db)
    monkeypatch.setattr(routes, 'get_database', lambda *a, **k: db)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr('core.concerts_client.ticketmaster_configured', lambda: False)
    # refresh inline so the test sees its result
    started = []

    def inline(database, pid):
        started.append(pid)
        inbox.refresh(database, pid)
        return True
    monkeypatch.setattr(inbox, 'refresh_in_background', inline)
    web_server.app.config['TESTING'] = True
    c = web_server.app.test_client()
    c.started = started
    return c


def test_opening_a_stale_inbox_refreshes_it(client, db):
    _album(db, 'Tool', 'Fear Inoculum', (date.today() - timedelta(days=2)).isoformat())
    body = client.get('/api/discover/inbox').get_json()
    assert client.started == [1]
    assert [i['title'] for i in body['items']] == ['Fear Inoculum']
    assert body['counts'] == {'unread': 1}
    assert body['sources']['concerts'] == {'state': 'off'}
    assert body['unanswered'] == []
    # fresh now: the next open doesn't refresh again
    client.get('/api/discover/inbox')
    assert client.started == [1]


def test_the_inbox_routes(client, db):
    _album(db, 'Tool', 'A', (date.today() - timedelta(days=2)).isoformat())
    _album(db, 'Tool', 'B', (date.today() - timedelta(days=1)).isoformat())
    items = client.get('/api/discover/inbox').get_json()['items']
    assert client.get('/api/discover/inbox/counts').get_json()['unread'] == 2
    assert client.post(f"/api/discover/inbox/{items[0]['id']}/state",
                       json={'state': 'saved'}).get_json()['success']
    assert client.post(f"/api/discover/inbox/{items[0]['id']}/state",
                       json={'state': 'gone'}).status_code == 400
    saved = client.get('/api/discover/inbox?view=saved').get_json()['items']
    assert [i['title'] for i in saved] == [items[0]['title']]
    assert client.post('/api/discover/inbox/dismiss-all').get_json()['dismissed'] == 1
    assert client.get('/api/discover/inbox/counts').get_json()['unread'] == 0


def test_saving_from_the_menu_lands_in_saved(client, db):
    body = client.post('/api/discover/feedback', json={
        'action': 'save', 'entity': {'type': 'artist', 'name': 'Karnivool'},
        'explanation': {'kind': 'listened', 'seeds': [{'name': 'Tool'}]}}).get_json()
    assert body['success']
    saved = client.get('/api/discover/inbox?view=saved').get_json()['items']
    assert [(i['kind'], i['title']) for i in saved] == [('saved_rec', 'Karnivool')]


def test_the_badge_count_refreshes_a_stale_inbox(client, db):
    """So the badge can say something's new without Discover being opened."""
    _album(db, 'Tool', 'Fear Inoculum', (date.today() - timedelta(days=2)).isoformat())
    assert client.get('/api/discover/inbox/counts').get_json()['unread'] == 1
    assert client.started == [1]
    client.get('/api/discover/inbox/counts')
    assert client.started == [1], 'a fresh inbox refreshed again'
