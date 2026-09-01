"""EXT.to's Fresh Releases board feeds the RSS wishlist matcher.

Boulder's idea, and the pieces already existed: the board is scraped hourly by
the ``video_extto_fresh_refresh`` automation and cached, its rows carry the
magnet straight off the homepage, and the grab endpoint has taken
``source='extto'`` (filed as a torrent, identity kept in the username) since the
Search page could grab from it.

So the RSS pass reads the SNAPSHOT - it does not scrape. EXT.to sits behind
Cloudflare and the board's hourly cadence is deliberate; polling it every few
minutes would be a much worse bargain. That also sets expectations: this is a
COVERAGE lane, not a speed one. ~57 popular releases refreshed hourly will not
beat Prowlarr to anything; what it adds is releases the user's own indexers
don't carry.
"""

from __future__ import annotations

import pytest

from core.video import rss_sync


def _row(**kw):
    row = {"title": "Some.Show.S01E01.1080p.WEB.h264-GRP", "url": "https://ext.to/t/1",
           "magnet_uri": "magnet:?xt=urn:btih:abc", "size_text": "1.4 GB",
           "seeders": 12, "leechers": 3}
    row.update(kw)
    return row


def _board(rows):
    return {"sections": {"tv": {"day": rows}}}


@pytest.fixture()
def board(monkeypatch):
    """Patch the cached board and the db handle the projector reads."""
    holder = {}

    def _install(rows):
        holder["rows"] = rows
        monkeypatch.setattr("core.video.extto_board.load_board", lambda db: _board(rows))
        monkeypatch.setattr("api.video.get_video_db", lambda: object())
    return _install


# ── the projection ───────────────────────────────────────────────────────────

def test_a_board_row_becomes_a_grabbable_hit(board):
    board([_row()])
    hits = rss_sync.fetch_extto_releases()
    assert len(hits) == 1
    h = hits[0]
    assert h["title"] == "Some.Show.S01E01.1080p.WEB.h264-GRP"
    assert h["protocol"] == "torrent"          # ext.to hits ARE torrents downstream
    assert h["username"] == "EXT.to"           # identity kept, same as a manual pick
    assert h["download_url"].startswith("magnet:") and h["magnet_uri"].startswith("magnet:")
    assert h["size_bytes"] > 0                 # parsed from "1.4 GB"
    assert h["seeders"] == 12 and h["availability"] == 12


def test_rows_without_a_magnet_or_title_are_skipped(board):
    board([_row(magnet_uri=None, download_url=None), _row(title="  ")])
    assert rss_sync.fetch_extto_releases() == []


def test_an_empty_or_unreadable_board_is_not_an_error(board, monkeypatch):
    board([])
    assert rss_sync.fetch_extto_releases() == []
    monkeypatch.setattr("core.video.extto_board.load_board",
                        lambda db: (_ for _ in ()).throw(RuntimeError("cloudflare")))
    assert rss_sync.fetch_extto_releases() == []


def test_the_same_release_in_two_periods_is_counted_once(board):
    """A title posted today sits in day AND week AND month on the board."""
    r = _row()
    monkeypatch_board = {"sections": {"tv": {"day": [r], "week": [r], "month": [r]}}}
    import core.video.extto_board as eb
    orig = eb.load_board
    eb.load_board = lambda db: monkeypatch_board
    try:
        assert len(rss_sync.fetch_extto_releases()) == 1
    finally:
        eb.load_board = orig


# ── the lane inside the pass ─────────────────────────────────────────────────

def test_the_board_rides_along_with_prowlarr(monkeypatch):
    seen = {}

    def _inner(*, fetch, log, extto=None):
        seen["hits"] = list(fetch() or []) + list((extto() if callable(extto) else []) or [])
        return {"status": "completed", "grabbed": 0}

    monkeypatch.setattr(rss_sync, "_rss_pass_inner", _inner)
    rss_sync.rss_pass(fetch=lambda: [{"guid": "p1", "title": "prowlarr hit"}],
                      extto=lambda: [{"guid": "e1", "title": "extto hit"}])
    assert [h["guid"] for h in seen["hits"]] == ["p1", "e1"]


def test_a_release_both_sources_carry_is_not_matched_twice(monkeypatch):
    """Prowlarr may well carry the same release the board shows."""
    hit = {"guid": "same", "title": "A", "protocol": "torrent"}
    monkeypatch.setattr(rss_sync, "fetch_recent_releases", lambda: [dict(hit)])
    # torrent has to be in the chain or the pass skips before it prescreens
    monkeypatch.setattr("core.video.download_config.load",
                        lambda db: {"download_mode": "torrent", "hybrid_order": []})
    monkeypatch.setattr("api.video.get_video_db", lambda: _StubDb())
    out = {}

    def _capture(hits, titles):
        out["n"] = len(hits)
        return []

    monkeypatch.setattr(rss_sync, "_prescreen", _capture)
    res = rss_sync.rss_pass(extto=lambda: [dict(hit)])
    assert res.get("releases") == 1, "the duplicate release was counted twice"


def test_the_lane_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(rss_sync, "fetch_extto_releases",
                        lambda: (_ for _ in ()).throw(AssertionError("board was read")))
    monkeypatch.setattr("api.video.get_video_db", lambda: _StubDb())
    rss_sync.rss_pass(fetch=lambda: [], extto=False)


class _StubDb:
    def get_setting(self, *a, **k):
        return None

    def get_active_video_downloads(self):
        return []

    def movie_wishlist_to_download(self, **k):
        return []

    def episode_wishlist_to_download(self, **k):
        return []
