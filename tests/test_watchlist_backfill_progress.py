"""The source-matching phase has to be visible and interruptible.

A watchlist scan does not begin with the artist loop. It first matches every
watchlisted artist against every other metadata provider, one network lookup per
artist per provider with a sleep between each. For somebody who has just added a
few hundred artists that is the longest part of the scan by far - 379 artists
across five providers is 1,895 lookups.

That phase used to report nothing and ignore cancellation. The page showed
"0 / 379 artists" the whole time, because progress is only written by the artist
loop, and Cancel did nothing, because the only cancel check lived in that same
loop. From outside it looked like a scan that started and hung (#1240).
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from core.watchlist_scanner import WatchlistScanner


def _artist(n):
    return types.SimpleNamespace(
        id=n, artist_name=f"Artist {n}",
        spotify_artist_id=None, itunes_artist_id=None, deezer_artist_id=None,
        discogs_artist_id=None, musicbrainz_artist_id=None,
        # read as an argument when the artist loop is entered
        last_scan_timestamp=None,
    )


@pytest.fixture
def scanner(monkeypatch):
    sc = WatchlistScanner.__new__(WatchlistScanner)
    # `database` is a lazy property with no setter; fill its backing field so
    # nothing here can reach a real database.
    sc._database = MagicMock()
    sc.database_path = ':memory:'
    # no sleeping in tests - the pacing is not what is under test here
    monkeypatch.setattr("core.watchlist_scanner.time.sleep", lambda *_a: None)
    return sc


def _match_always(name):
    return f"id-for-{name}"


def test_the_backfill_reports_progress_as_it_goes(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "_match_to_itunes", _match_always, raising=False)
    seen = []

    ok = scanner._backfill_missing_ids(
        [_artist(i) for i in range(5)], "itunes",
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert ok is True
    # one before each artist, and a final one at completion
    assert seen == [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


def test_a_cancel_is_honoured_inside_the_backfill(scanner, monkeypatch):
    """Before this, cancel was only read between ARTISTS in the scan loop -
    a loop that had not started yet, so there was no way to stop at all."""
    looked_up = []

    def _match(name):
        looked_up.append(name)
        return "x"

    monkeypatch.setattr(scanner, "_match_to_itunes", _match, raising=False)

    ok = scanner._backfill_missing_ids(
        [_artist(i) for i in range(20)], "itunes",
        cancel_check=lambda: len(looked_up) >= 3,
    )

    assert ok is False
    assert len(looked_up) == 3, "it kept going after the cancel"


def test_cancel_is_checked_before_the_lookup_not_after(scanner, monkeypatch):
    """A cancel must land within one artist, not one provider pass."""
    monkeypatch.setattr(scanner, "_match_to_itunes", _match_always, raising=False)

    ok = scanner._backfill_missing_ids(
        [_artist(i) for i in range(50)], "itunes", cancel_check=lambda: True,
    )

    assert ok is False
    scanner.database.update_watchlist_itunes_id.assert_not_called()


def test_nothing_to_do_completes_rather_than_returning_none(scanner):
    """The caller reads the return value to decide whether to stop."""
    already = _artist(1)
    already.itunes_artist_id = "have-it"
    assert scanner._backfill_missing_ids([already], "itunes") is True


def test_an_unknown_provider_completes(scanner):
    assert scanner._backfill_missing_ids([_artist(1)], "not-a-provider") is True


def test_a_failing_lookup_does_not_stop_the_pass(scanner, monkeypatch):
    def _boom(name):
        if name == "Artist 1":
            raise RuntimeError("provider down")
        return "ok"

    monkeypatch.setattr(scanner, "_match_to_itunes", _boom, raising=False)
    seen = []

    ok = scanner._backfill_missing_ids(
        [_artist(i) for i in range(4)], "itunes",
        on_progress=lambda d, t: seen.append(d),
    )

    assert ok is True
    assert seen[-1] == 4, "the pass did not run to the end"


def test_a_broken_progress_callback_cannot_break_the_scan(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "_match_to_itunes", _match_always, raising=False)

    def _bad(_done, _total):
        raise RuntimeError("ui blew up")

    assert scanner._backfill_missing_ids([_artist(1)], "itunes", on_progress=_bad) is True


def test_a_backfill_that_answers_nothing_does_not_abort_the_scan(monkeypatch):
    """None means "said nothing", not "cancelled".

    The caller reads the return value to decide whether to stop the whole scan.
    Reading a bare None as a cancel aborted every scan before the artist loop -
    which is what a stub, a subclass, or any older override returns.
    """
    from core.watchlist_scanner import WatchlistScanner

    sc = WatchlistScanner.__new__(WatchlistScanner)
    sc._database = MagicMock()
    sc.database_path = ':memory:'
    monkeypatch.setattr("core.watchlist_scanner.time.sleep", lambda *_a: None)
    monkeypatch.setattr(sc, "_apply_global_watchlist_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "_backfill_missing_ids", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "_watchlist_source_priority", lambda: ['itunes'])
    monkeypatch.setattr(sc, "_get_lookback_period_setting", lambda: 'all')
    # the artist loop stops at the first artist; all we need to know is that it
    # was REACHED rather than skipped by a false cancel
    reached = []
    monkeypatch.setattr(sc, "get_artist_discography_for_watchlist",
                        lambda *_a, **_k: reached.append(1) or None)

    state = {}
    sc.scan_watchlist_artists([_artist(1)], scan_state=state)

    assert reached, "the scan aborted before the artist loop"
    assert state.get('status') != 'cancelled'


# ---------------------------------------------------------------------------
# cancelling mid-artist
# ---------------------------------------------------------------------------

def test_cancel_lands_inside_the_album_loop_not_just_between_artists(monkeypatch):
    """An artist with many albums used to be unstoppable.

    cancel_check was read once per ARTIST. An artist with thirty albums meant
    thirty fetches and thirty pacing sleeps before the scan would even look at
    the request, and a slow provider turned that into minutes of a scan that
    would not stop (#1240: "I can't cancelled this").
    """
    from core.watchlist_scanner import WatchlistScanner

    sc = WatchlistScanner.__new__(WatchlistScanner)
    sc._database = MagicMock()
    sc.database_path = ':memory:'
    sc._database.has_fresh_similar_artists.return_value = True
    monkeypatch.setattr("core.watchlist_scanner.time.sleep", lambda *_a: None)
    monkeypatch.setattr(sc, "_apply_global_watchlist_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "_backfill_missing_ids", lambda *_a, **_k: True)
    monkeypatch.setattr(sc, "_watchlist_source_priority", lambda: [])
    monkeypatch.setattr(sc, "_get_lookback_period_setting", lambda: 'recent')
    monkeypatch.setattr(sc, "get_artist_image_url", lambda *_a, **_k: '')

    albums = [types.SimpleNamespace(id=f"al{i}", name=f"Album {i}") for i in range(30)]
    monkeypatch.setattr(sc, "get_artist_discography_for_watchlist", lambda *_a, **_k: albums)

    fetched = []

    def _fetch(album_id, album_name=''):
        fetched.append(album_id)
        return None          # no track data; the loop just moves on

    # lazy property, no setter - fill the backing field
    sc._metadata_service = types.SimpleNamespace(get_album=_fetch)

    # cancel once three albums have been looked at
    sc.scan_watchlist_artists(
        [_artist(1)],
        scan_state={},
        cancel_check=lambda: len(fetched) >= 3,
    )

    assert len(fetched) == 3, (
        f"kept fetching albums after the cancel: {len(fetched)} of {len(albums)}"
    )


def test_a_cancelled_artist_skips_its_discovery_work(monkeypatch):
    """No point spending a similar-artists lookup on an artist we abandoned."""
    from core.watchlist_scanner import WatchlistScanner

    sc = WatchlistScanner.__new__(WatchlistScanner)
    sc._database = MagicMock()
    sc.database_path = ':memory:'
    monkeypatch.setattr("core.watchlist_scanner.time.sleep", lambda *_a: None)
    monkeypatch.setattr(sc, "_apply_global_watchlist_overrides", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "_backfill_missing_ids", lambda *_a, **_k: True)
    monkeypatch.setattr(sc, "_watchlist_source_priority", lambda: [])
    monkeypatch.setattr(sc, "_get_lookback_period_setting", lambda: 'recent')
    monkeypatch.setattr(sc, "get_artist_image_url", lambda *_a, **_k: '')

    albums = [types.SimpleNamespace(id="al0", name="Album 0")]
    monkeypatch.setattr(sc, "get_artist_discography_for_watchlist", lambda *_a, **_k: albums)
    sc._metadata_service = types.SimpleNamespace(get_album=lambda *_a, **_k: None)

    similar_calls = []
    monkeypatch.setattr(sc, "update_similar_artists",
                        lambda *_a, **_k: similar_calls.append(1))

    sc.scan_watchlist_artists([_artist(1)], scan_state={}, cancel_check=lambda: True)

    assert similar_calls == []
