"""Inbox artist news: the provider hook, and news items behaving like inbox items."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.discovery import inbox
from database.music_database import MusicDatabase

TODAY = date(2026, 9, 26)


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    d.add_artist_to_watchlist('sp-tool', 'Tool', profile_id=1, source='spotify')
    d.add_artist_to_watchlist('sp-soen', 'Soen', profile_id=1, source='spotify')
    return d


@pytest.fixture(autouse=True)
def _clean_providers():
    for name in inbox.list_news_providers():
        inbox.unregister_news_provider(name)
    yield
    for name in inbox.list_news_providers():
        inbox.unregister_news_provider(name)


def _days(n):
    return (TODAY + timedelta(days=n)).isoformat()


def _news(db, provider='wire', **over):
    item = {'title': 'Tour announced', 'url': 'https://news/1',
            'published_at': _days(-2), 'summary': 'big tour', **over}
    inbox.register_news_provider(provider, lambda name, ctx: [item])
    return inbox.collect_news(db, 1)


def test_artist_news_is_an_inbox_kind_newest_first(db):
    inbox.register_news_provider('wire', lambda name, ctx: [
        {'title': 'Old news', 'published_at': _days(-9)},
        {'title': 'Fresh news', 'published_at': _days(-1)}])
    assert inbox.collect_news(db, 1) == 4  # one per watchlist artist per item
    rows = inbox.list_items(db, 1)
    assert [(r['kind'], r['title']) for r in rows] == [
        ('artist_news', 'Fresh news'), ('artist_news', 'Fresh news'),
        ('artist_news', 'Old news'), ('artist_news', 'Old news')]
    assert rows[0]['payload']['provider'] == 'wire'


def test_no_providers_means_off_not_failed(db):
    assert inbox.collect_news(db, 1) is None
    result = inbox.refresh(db, 1, today=TODAY,
                           concerts=lambda name: {'events': [], 'error': 'nope'})
    assert result['sources']['news'] == {'state': 'off'}
    assert inbox.unanswered(inbox.status(db, 1)) == ['concerts']


def test_a_news_item_carries_its_link_and_acts_like_an_inbox_item(db):
    assert _news(db) == 2
    item = next(r for r in inbox.list_items(db, 1) if r['artist_name'] == 'Tool')
    assert item['payload']['url'] == 'https://news/1'
    assert item['payload']['summary'] == 'big tour'
    # a refresh never resets what someone chose, news included
    assert inbox.set_state(db, 1, item['id'], 'dismissed')
    assert inbox.collect_news(db, 1) == 2  # upserts refresh the rows, none new
    assert len(inbox._rows(db, 1, ('unread', 'dismissed'))) == 2
    assert inbox.unread_count(db, 1) == 1


def test_malformed_provider_items_are_skipped_not_stored(db):
    inbox.register_news_provider('wire', lambda name, ctx: [
        'not a dict', None, {'title': ''}, {'title': '  '},
        {'title': 'Real news', 'published_at': _days(-1)}])
    assert inbox.collect_news(db, 1) == 2
    assert [r['title'] for r in inbox.list_items(db, 1)] == ['Real news', 'Real news']


def test_one_provider_failing_keeps_what_answered(db):
    def broken(name, ctx):
        raise ConnectionError('provider down')

    def good(name, ctx):
        return [{'title': 'Tour announced', 'published_at': _days(-1)}]

    assert inbox.collect_news(db, 1, {'broken': broken, 'good': good}) == 2
    assert inbox.unread_count(db, 1) == 2


def test_every_provider_failing_is_a_failed_source(db):
    def broken(name, ctx):
        raise ConnectionError('provider down')

    with pytest.raises(inbox.SourceFailed):
        inbox.collect_news(db, 1, {'broken': broken})
    result = inbox.refresh(db, 1, today=TODAY)
    assert result['sources']['news']['state'] == 'off'  # nothing registered
    inbox.register_news_provider('broken', broken)
    result = inbox.refresh(db, 1, today=TODAY)
    assert result['sources']['news']['state'] == 'failed'
    assert inbox.unanswered(inbox.status(db, 1)) == ['news']


def test_stale_unread_news_is_pruned_but_saved_and_dateless_stay(db):
    inbox.register_news_provider('wire', lambda name, ctx: [
        {'title': 'Ancient', 'published_at': _days(-45)},
        {'title': 'Kept', 'published_at': _days(-45)},
        {'title': 'Dateless'}])
    assert inbox.collect_news(db, 1) == 6
    for row in inbox.list_items(db, 1):
        if row['title'] == 'Kept' and row['artist_name'] == 'Tool':
            inbox.set_state(db, 1, row['id'], 'saved')
    inbox.refresh(db, 1, today=TODAY)
    left = {(r['title'], r['state']) for r in inbox.list_items(db, 1, 'new')}
    assert all(t != 'Ancient' for t, _ in left)
    assert ('Dateless', 'unread') in left
    assert {r['title'] for r in inbox.list_items(db, 1, 'saved')} == {'Kept'}
