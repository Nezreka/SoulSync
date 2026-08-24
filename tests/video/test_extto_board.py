"""Fresh Releases as a refreshed-and-cached board.

The tab used to pull EXT.to on the render path. Enriching each row from its own
detail page made that untenable — every detail page is its own Cloudflare
challenge, and a bad minute on ext.to costs 40 seconds a page — so the work moved
to ``refresh_board``, run by the hourly automation or the tab's Refresh button,
with the result stored and served.

These cover the things that make that affordable and honest: fetch each release
once, never re-fetch one we already matched, stop before the run runs away, and
say what was left out.
"""

from __future__ import annotations

import json

import pytest

from core.video import extto_board
from core.video.extto_detail import is_extto_url, parse_detail

# Trimmed from the real pages. The two categories share almost no labels, which
# is exactly why the parser reads whatever is there instead of a fixed schema.
MOVIE_HTML = """
<img class="detail-torrent-image" src="/upload_files/resize_cache/all/424/174_260_2/x.jpg"
     title="Evil Dead Burn (2026) HQ HDRip - 720p - x264">
<ul class="detail-page-info-list">
  <li><strong>Movie:</strong> <a href="/evil-dead-burn-m209369/"><span>Evil Dead Burn</span></a></li>
  <li><strong>Detected quality:</strong> <span>720p (WEB-DL, x264)</span></li>
  <li><strong>IMDb link:</strong> <a href="https://www.imdb.com/title/tt31170389/">31170389</a></li>
  <li><strong>IMDb rating:</strong> 6.5 (26,014 votes) <a href="/browse/?imdb_id=tt31170389">Search</a></li>
  <li><strong>Genres:</strong> <a href="/movies/genre/horror/"><span>Horror</span></a>,
      <a href="/movies/genre/fantasy/"><span>Fantasy</span></a></li>
  <li><strong>Cast:</strong> <a href="/movies/actors/luciane-buchanan/"><span>Luciane Buchanan</span></a>,
      <a href="/movies/actors/hunter-doohan/"><span>Hunter Doohan</span></a> and others</li>
  <li><strong>Release year:</strong> 2026</li>
  <li><strong>Runtime:</strong> 110 minutes</li>
  <li><strong>Budget:</strong> $20,000,000</li>
</ul>
"""

TV_HTML = """
<img class="detail-torrent-image" src="/static/img/no-torrent-image.png" title="Lanterns S01E02">
<ul class="detail-page-info-list">
  <li><strong>Original name:</strong> Lanterns</li>
  <li><strong>Type:</strong> Scripted</li>
  <li><strong>IMDb link:</strong> <a href="https://www.imdb.com/title/tt26545992/">26545992</a></li>
  <li><strong>IMDb rating:</strong> 8 (9,940 votes)</li>
  <li><strong>Created by:</strong> Damon Lindelof, Tom King</li>
  <li><strong>Networks:</strong> HBO</li>
  <li><strong>Schedule:</strong> Sundays at 21:00</li>
</ul>
"""


def test_a_movie_page_and_a_tv_page_both_parse_despite_sharing_almost_no_labels():
    movie = parse_detail(MOVIE_HTML, url="https://ext.to/evil-dead-burn-1/")
    assert movie["title"] == "Evil Dead Burn"
    assert movie["imdb_id"] == "tt31170389"
    assert movie["imdb_rating"] == 6.5 and movie["imdb_votes"] == 26014
    assert movie["year"] == 2026 and movie["runtime_minutes"] == 110
    assert movie["quality"] == "720p (WEB-DL, x264)"
    assert movie["genres"] == ["Horror", "Fantasy"]
    assert movie["poster_url"].startswith("https://ext.to/upload_files/")
    # 'A, B and others' — the trailing prose is not a cast member
    assert movie["cast"] == ["Luciane Buchanan", "Hunter Doohan"]

    tv = parse_detail(TV_HTML, url="https://ext.to/lanterns-1/")
    assert tv["title"] == "Lanterns", "the TV title label is 'Original name', not 'Movie'"
    assert tv["imdb_id"] == "tt26545992" and tv["imdb_rating"] == 8.0
    # a TV page states none of these, and must not invent them
    assert tv["year"] is None and tv["runtime_minutes"] is None and tv["genres"] == []
    # ...but every fact it DID state survives, so an unseen category still renders
    assert [f["label"] for f in tv["facts"]] == [
        "Original name", "Type", "IMDb link", "IMDb rating", "Created by", "Networks", "Schedule"]


def test_the_no_artwork_placeholder_is_not_treated_as_a_poster():
    """ext.to serves a grey 'no image' tile for a lot of TV. Rendering it would put
    a broken-looking box on the card, so it reads as no poster and the UI draws
    its own initial instead."""
    assert parse_detail(TV_HTML)["poster_url"] is None
    assert parse_detail(MOVIE_HTML)["poster_url"] is not None


@pytest.mark.parametrize("url,ok", [
    ("https://ext.to/x-1/", True),
    ("https://search.extto.com/y-2/", True),
    ("https://EXT.TO/z-3/", True),
    ("http://evil.com/x", False),
    ("https://ext.to.evil.com/x", False),      # the lookalike a bare endswith allows
    ("file:///etc/passwd", False),
    ("", False),
])
def test_only_extto_urls_are_fetchable(url, ok):
    """The detail fetch takes a URL from the browser. Without this it is an open
    proxy: anything could ask SoulSync's FlareSolverr to fetch anything and hand
    back the body."""
    assert is_extto_url(url) is ok


class _DB:
    """The two persistence seams refresh_board uses."""

    def __init__(self, cached=None):
        self.cache = dict(cached or {})
        self.settings = {}
        self.stored = []

    def extto_detail_cached(self, url):
        return self.cache.get(url)

    def extto_detail_store(self, url, detail):
        self.cache[url] = detail
        self.stored.append(url)

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value):
        self.settings[key] = value


def _board(*urls, periods=("day", "week", "month")):
    """A board where every release appears in EVERY period, like a fresh one does."""
    rows = [{"title": "R%d" % i, "url": u} for i, u in enumerate(urls)]
    return {"configured": True, "source": "EXT.to", "total": len(urls),
            "sections": {"movies": {p: [dict(r) for r in rows] for p in periods}}}


def _patch(monkeypatch, board, fetched):
    monkeypatch.setattr("core.video.extto_fresh.extto_fresh_releases", lambda **k: board)
    calls = []

    def _fetch(url, **kw):
        calls.append(url)
        return {"ok": True, "detail": {"title": fetched, "url": url}}

    monkeypatch.setattr("core.video.extto_detail.fetch_detail", _fetch)
    return calls


def test_a_release_in_three_periods_is_matched_once_not_three_times(monkeypatch):
    """A title posted today sits in day AND week AND month. Enriching per slot
    would pay for the same detail page three times for one card's worth of facts."""
    db = _DB()
    calls = _patch(monkeypatch, _board("https://ext.to/a-1/"), "A")
    res = extto_board.refresh_board(db, flaresolverr="http://fs")
    assert res["ok"] and res["rows"] == 1
    assert calls == ["https://ext.to/a-1/"], "the same release was fetched per period"
    # ...and the facts still reach every copy of it
    sections = json.loads(db.settings[extto_board.BOARD_SETTING])["sections"]
    for period in ("day", "week", "month"):
        assert sections["movies"][period][0]["detail"]["title"] == "A"


def test_a_release_we_matched_before_costs_no_network(monkeypatch):
    """This is what makes an hourly cadence affordable: the board mostly repeats
    itself, and a release's detail page never meaningfully changes."""
    from core.video.extto_detail import PARSE_VERSION
    known = {"https://ext.to/a-1/": {"title": "A (cached)", "v": PARSE_VERSION}}
    db = _DB(cached=known)
    calls = _patch(monkeypatch, _board("https://ext.to/a-1/", "https://ext.to/b-2/"), "B")
    res = extto_board.refresh_board(db, flaresolverr="http://fs")
    assert calls == ["https://ext.to/b-2/"], "a cached release was fetched again"
    assert res["cached"] == 1 and res["fetched"] == 1
    sections = json.loads(db.settings[extto_board.BOARD_SETTING])["sections"]
    assert sections["movies"]["day"][0]["detail"]["title"] == "A (cached)"


def test_a_run_stops_before_it_runs_away_and_says_what_it_left(monkeypatch):
    """A cold cache against a big board would otherwise sit on ext.to for as long
    as it takes. The cap bounds one run; the rest is picked up next time, and the
    deferral is REPORTED rather than passed off as a complete board."""
    db = _DB()
    urls = ["https://ext.to/r%d-%d/" % (i, i) for i in range(6)]
    calls = _patch(monkeypatch, _board(*urls), "X")
    lines = []
    res = extto_board.refresh_board(db, flaresolverr="http://fs", max_new_details=2,
                                    log=lines.append)
    assert len(calls) == 2, "the cap did not bound the run"
    assert res["fetched"] == 2 and res["skipped"] == 4
    assert any("next run" in ln for ln in lines), "a partial board reported as complete"


def test_a_release_whose_detail_page_fails_keeps_a_plain_card(monkeypatch):
    db = _DB()
    monkeypatch.setattr("core.video.extto_fresh.extto_fresh_releases",
                        lambda **k: _board("https://ext.to/a-1/"))
    monkeypatch.setattr("core.video.extto_detail.fetch_detail",
                        lambda url, **kw: {"ok": False, "error": "Cloudflare"})
    res = extto_board.refresh_board(db, flaresolverr="http://fs")
    assert res["ok"] and res["failed"] == 1
    row = json.loads(db.settings[extto_board.BOARD_SETTING])["sections"]["movies"]["day"][0]
    assert "detail" not in row, "a failed match must not leave a half-built detail"


def test_an_unconfigured_or_failing_board_does_not_overwrite_the_last_good_one(monkeypatch):
    """A refresh that could not reach EXT.to must leave the stored board alone —
    replacing it with nothing would empty the tab on one bad hour."""
    db = _DB()
    db.settings[extto_board.BOARD_SETTING] = json.dumps({"sections": {"movies": {"day": [1]}}})
    monkeypatch.setattr("core.video.extto_fresh.extto_fresh_releases",
                        lambda **k: {"configured": True, "error": "Cloudflare challenge"})
    res = extto_board.refresh_board(db, flaresolverr="http://fs")
    assert not res["ok"]
    assert json.loads(db.settings[extto_board.BOARD_SETTING])["sections"]["movies"]["day"] == [1]


def test_the_stored_board_round_trips_with_its_timestamp():
    db = _DB()
    db.settings[extto_board.BOARD_SETTING] = json.dumps({"sections": {"movies": {}}, "total": 3})
    db.settings[extto_board.BOARD_AT_SETTING] = "2026-08-24 15:04:11"
    board = extto_board.load_board(db)
    assert board["total"] == 3 and board["fetched_at"] == "2026-08-24 15:04:11"
    # a corrupt snapshot degrades to an empty board rather than breaking the tab
    db.settings[extto_board.BOARD_SETTING] = "{not json"
    assert extto_board.load_board(db)["sections"] == {}


def test_the_refresh_is_seeded_as_a_video_automation():
    """Registering the action type only puts it in the BUILDER. The Automations
    page lists seeded rows, so an action that is registered but not in
    SYSTEM_AUTOMATIONS simply never appears — which is exactly how this shipped
    the first time. Hourly, because that is the board's own turnover and matched
    releases are cached."""
    import core.automation_engine as ae
    row = next((a for a in ae.SYSTEM_AUTOMATIONS
                if a.get("action_type") == "video_extto_fresh_refresh"), None)
    assert row is not None, "the refresh is registered but never seeded — it will not show up"
    assert row["owned_by"] == "video"
    assert row["trigger_type"] == "schedule"
    assert row["trigger_config"] == {"interval": 1, "unit": "hours"}
    # staggered off the boot path like its neighbours, so a restart does not fire
    # every video automation at once
    assert row["initial_delay"] >= 900
    assert row["action_config"]["max_new_details"] >= 1


# ── orphaned system rows ─────────────────────────────────────────────────────
class _AutoDB:
    """Just the automation seams the sweep touches."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}

    def get_automations(self):
        return [dict(r) for r in self.rows.values()]

    def update_automation(self, aid, **fields):
        self.rows[aid].update(fields)
        return True

    def delete_automation(self, aid):
        # mirrors the real guard: a system row cannot be deleted
        if self.rows.get(aid, {}).get("is_system"):
            return False
        return self.rows.pop(aid, None) is not None


def _engine_with(rows, handlers):
    import core.automation_engine as ae
    eng = ae.AutomationEngine.__new__(ae.AutomationEngine)
    eng.db = _AutoDB(rows)
    eng._action_handlers = {h: {} for h in handlers}
    return eng


def test_a_system_row_whose_action_no_longer_exists_is_removed():
    """A system row is seeded by the engine and the API refuses to delete it (403),
    so a renamed or dropped action strands it forever: it cannot run, the user
    cannot remove it, and it logs 'No handler for action: <type>' on every fire."""
    eng = _engine_with([
        {"id": 1, "name": "Live one", "action_type": "video_extto_fresh_refresh", "is_system": 1},
        {"id": 2, "name": "Stranded", "action_type": "video_extto_fresh_refresh_GONE", "is_system": 1},
    ], handlers={"video_extto_fresh_refresh"})
    eng._fix_orphaned_system_actions()
    left = {r["action_type"] for r in eng.db.get_automations()}
    assert left == {"video_extto_fresh_refresh"}, "the stranded row survived"


def test_the_sweep_never_touches_a_users_own_automation():
    """Only system-seeded rows are in scope. A user's automation for an action we
    happen not to have registered is theirs, not ours."""
    eng = _engine_with([
        {"id": 1, "name": "Mine", "action_type": "something_custom", "is_system": 0},
    ], handlers={"video_extto_fresh_refresh"})
    eng._fix_orphaned_system_actions()
    assert len(eng.db.get_automations()) == 1


def test_the_sweep_does_nothing_while_no_handlers_are_registered():
    """Startup registers handlers before start() runs this. If that order ever
    changed, an empty registry must NOT read as 'every automation is orphaned' —
    that would wipe the table."""
    eng = _engine_with([
        {"id": 1, "name": "Live", "action_type": "video_rss_sync", "is_system": 1},
        {"id": 2, "name": "Live too", "action_type": "video_seeding_sweep", "is_system": 1},
    ], handlers=set())
    eng._fix_orphaned_system_actions()
    assert len(eng.db.get_automations()) == 2, "an empty registry emptied the table"


def test_every_seeded_action_has_a_handler_so_the_sweep_can_only_hit_dead_rows():
    """The sweep's whole safety argument: nothing legitimately seeded is ever
    handler-less, so 'no handler' means genuinely dead."""
    import core.automation_engine as ae
    from core.automation.handlers import register_all
    from tests.automation.test_handler_registration import _RecordingEngine, _build_deps
    eng = _RecordingEngine()
    register_all(_build_deps(eng))
    orphans = {a["action_type"] for a in ae.SYSTEM_AUTOMATIONS} - set(eng.action_handlers)
    assert not orphans, "seeded actions with no handler — the sweep would delete them: %s" % orphans


# ── posters ──────────────────────────────────────────────────────────────────
TV_WITH_TMDB_POSTER = """
<div class="poster-block">
  <div class="serial_poster__border1" style="background-image:url(https://image.tmdb.org/t/p/w300/x.jpg);"></div>
  <a href="/lanterns-s49500/"><img src="https://image.tmdb.org/t/p/w300_and_h450_bestv2/g.jpg" alt="Lanterns"></a>
</div>
<img class="detail-torrent-image" src="/static/img/no-torrent-image.png" title="Lanterns S01E02">
<ul class="detail-page-info-list"><li><strong>Original name:</strong> Lanterns</li></ul>
"""


def test_a_tv_poster_comes_from_tmdb_not_the_grey_placeholder():
    """TV pages carry a .poster-block with a TMDB url while detail-torrent-image is
    the 'no artwork' tile. TMDB is preferred wherever it exists: it is not behind
    Cloudflare, the image proxy already allowlists it, and it is a bigger image."""
    d = parse_detail(TV_WITH_TMDB_POSTER, url="https://ext.to/lanterns-1/")
    assert d["poster_url"] == "https://image.tmdb.org/t/p/w300_and_h450_bestv2/g.jpg"


def test_a_movie_poster_falls_back_to_the_extto_hosted_art():
    """Movie pages have no .poster-block, so their art is ext.to-hosted — which is
    why the image proxy needs Cloudflare clearance for that host."""
    d = parse_detail(MOVIE_HTML)
    assert d["poster_url"].startswith("https://ext.to/upload_files/")


def test_extto_posters_are_proxied_same_origin_never_hotlinked():
    """ext.to sends Cross-Origin-Resource-Policy: same-origin on its art, so a
    direct <img src="https://ext.to/..."> is refused by the BROWSER
    (ERR_BLOCKED_BY_RESPONSE.NotSameOrigin) even though the URL is valid. Every
    poster therefore goes through our own origin."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "webui" / "static" / "video" / "video-search.js"
          ).read_text(encoding="utf-8")
    art = js.split("function freshArtHTML")[1].split("function freshRowHTML")[0]
    assert "/api/video/img?u=" in art, "posters are hotlinked — the browser will block them"
    assert "encodeURIComponent" in art
    assert "src=\"' + esc(d.poster_url)" not in art, "a raw upstream URL is still used as src"


def test_the_image_proxy_allows_extto_without_allowing_a_lookalike_domain():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "api" / "video" / "poster.py"
           ).read_text(encoding="utf-8")
    allow = src.split("ok = host == \"image.tmdb.org\"")[1][:400]
    assert '"ext.to"' in allow, "ext.to posters would 404 at the proxy"
    # exact-or-subdomain matching, not a bare endswith that 'ext.to.evil.com' passes
    assert 'host == s or host.endswith("." + s)' in allow


def test_a_parser_improvement_reaches_releases_that_were_already_matched(monkeypatch):
    """Matched releases are cached to make the hourly refresh cheap, which means an
    entry can outlive the parser that produced it. Teaching the parser to prefer the
    TMDB poster on TV pages fixed nothing for anything already on the board: the
    refresh served the old parse back and never looked at the page again. A payload
    stamped by an older parser must read as a MISS."""
    from core.video.extto_detail import PARSE_VERSION
    stale = {"https://ext.to/a-1/": {"title": "A", "poster_url": None, "v": PARSE_VERSION - 1}}
    db = _DB(cached=stale)
    calls = _patch(monkeypatch, _board("https://ext.to/a-1/"), "A (re-parsed)")
    res = extto_board.refresh_board(db, flaresolverr="http://fs")
    assert calls == ["https://ext.to/a-1/"], "a stale-parser entry was served from cache"
    assert res["fetched"] == 1 and res["cached"] == 0
    sections = json.loads(db.settings[extto_board.BOARD_SETTING])["sections"]
    assert sections["movies"]["day"][0]["detail"]["title"] == "A (re-parsed)"


def test_an_unstamped_legacy_entry_is_re_matched(monkeypatch):
    """Entries written before the stamp existed have no 'v' at all."""
    db = _DB(cached={"https://ext.to/a-1/": {"title": "old"}})
    calls = _patch(monkeypatch, _board("https://ext.to/a-1/"), "fresh")
    extto_board.refresh_board(db, flaresolverr="http://fs")
    assert calls == ["https://ext.to/a-1/"]
