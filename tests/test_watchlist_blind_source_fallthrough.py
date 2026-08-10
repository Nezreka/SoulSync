"""A provider that can't resolve an artist must not end the source chain.

Reported as "no songs added to wishlist with Spotify no auth metadata source":
21 watchlist artists, 0 wishlist entries, no errors in the log. His log showed

    deezer_client - get_artist_albums_list - Retrieved 0 albums for artist 1490256993

for every artist, and those are iTunes-shaped IDs. Deezer can't resolve them
(iTunes and Deezer artist IDs are both bare integers, so they get confused for
one another), so it returned nothing.

`_get_artist_discography_with_client` returned [] for that, and
`get_artist_discography_for_watchlist` treats [] as SUCCESS — "the artist has
no new releases" — which returns immediately and never tries the remaining
sources. A blind provider sitting first in the priority order therefore starved
the wishlist silently, while iTunes (which did know the artist) was never asked.

The distinction that was missing: an empty RAW discography means the source
can't resolve the ID; an empty list AFTER the lookback filter means there's
genuinely nothing new. Only the first should fall through.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.watchlist_scanner as ws
from database.music_database import WatchlistArtist

OLD = {'id': 'old', 'name': 'Old Album', 'release_date': '2001-01-01'}
NEW = {'id': 'new', 'name': 'New Album', 'release_date': '2026-07-20'}


class _Client:
    def __init__(self, name, albums, calls):
        self.name, self.albums, self._calls = name, albums, calls

    def get_artist_albums(self, artist_id, **kwargs):
        self._calls.append(self.name)
        return list(self.albums)

    def search_artists(self, *a, **k):
        return []


@pytest.fixture()
def scan(monkeypatch):
    """Drive the REAL discography fetch/filter; stub only leaf helpers."""
    def _run(catalogue, preferred=None):
        calls: list[str] = []
        clients = {
            name: (_Client(name, albums, calls) if albums is not None else None)
            for name, albums in catalogue.items()
        }
        monkeypatch.setattr(ws, 'get_client_for_source', lambda s, **kw: clients.get(s))

        scanner = ws.WatchlistScanner.__new__(ws.WatchlistScanner)
        scanner._get_lookback_period_setting = lambda: '30'
        scanner._get_rescan_cutoff = lambda: None
        scanner._rescan_cutoff_log_marker = None
        scanner.is_album_after_timestamp = lambda alb, cut: datetime.fromisoformat(
            alb['release_date']).replace(tzinfo=timezone.utc) > cut
        scanner._is_future_release = lambda alb, now: False
        scanner._get_artist_image_for_source = lambda *a, **k: None

        artist = WatchlistArtist(
            id=1, spotify_artist_id='4Zspotify', artist_name='YOASOBI',
            date_added=datetime.now(), deezer_artist_id='1490256993',
            itunes_artist_id='1490256993', preferred_metadata_source=preferred,
        )
        return scanner.get_artist_discography_for_watchlist(artist), calls
    return _run


# deezer is blind (0 raw albums); itunes knows the artist and has a new release
BLIND = {'spotify': None, 'deezer': [], 'itunes': [OLD, NEW]}
# deezer knows the artist, but nothing inside the lookback window
NOTHING_NEW = {'spotify': None, 'deezer': [OLD], 'itunes': [OLD, NEW]}


@pytest.mark.parametrize("preferred", [None, 'spotify'])
def test_blind_provider_falls_through_to_one_that_knows_the_artist(scan, preferred):
    """THE regression. Works the same with or without a per-artist override —
    the override only changes which provider goes blind first."""
    result, calls = scan(BLIND, preferred)

    assert calls == ['deezer', 'itunes'], "the chain stopped at the blind provider"
    assert result is not None
    assert result.source == 'itunes'
    assert [a['name'] for a in result.albums] == ['New Album'], \
        "the release iTunes knew about never reached the wishlist"


def test_genuine_nothing_new_still_stops_at_the_first_source(scan):
    """The fast path must not regress into querying every provider.

    Deezer resolves the artist and simply has nothing inside the lookback
    window. That is a real answer, not a failure, so the scan must accept it
    and stop — otherwise every quiet artist costs an API call per source on
    every incremental scan.
    """
    result, calls = scan(NOTHING_NEW)

    assert calls == ['deezer'], "walked on past a source that answered correctly"
    assert result is not None
    assert result.source == 'deezer'
    assert result.albums == []


def test_per_artist_override_still_wins(scan):
    result, calls = scan(BLIND, 'itunes')
    assert calls == ['itunes']
    assert result.source == 'itunes'


def test_every_source_blind_reports_failure_rather_than_false_success(scan):
    """If nobody can resolve the artist, that is not 'no new releases'."""
    result, calls = scan({'spotify': None, 'deezer': [], 'itunes': []})
    assert calls == ['deezer', 'itunes']
    assert result is None, "a total resolution failure was reported as success"
