"""A season pack renders as the batch it is, not as a sibling of its own episodes.

Boulder, after the first live pack import: *"not loving how its appearing to me on
downloads page. its a bit weak compared to music side where it has batches and
groupings."*

Read off his live rows, the cause was narrow. The page already groups a show's
episodes into one card keyed on show + season — that machinery works. But
``groupKey`` required an episode number, and a pack row carries a season and no
episode, so it fell out of its own group:

    #3911  scope=season   E=None   groupKey=None      <- the pack
    #3912  scope=episode  E=1      groupKey=g:…:s1    ┐
    …                                                 ├ these eight DID group
    #3919  scope=episode  E=8      groupKey=g:…:s1    ┘

So the batch rendered as a lonely card showing a raw release name, beside a group
of the episodes it had just produced.

Two decisions worth stating:

**The verdict comes from the server.** ``is_pack`` is annotated by
``core.video.season_pack.is_pack_download`` — the same function the monitor uses
to decide whether to map a finished folder. Re-deriving it in JavaScript would be
a second answer to one question, and the two would drift the first time the row
shape changed. The client keeps a search_ctx fallback only so a payload from an
older build still renders.

**The pack is the head, not a member.** It is the batch: counting it among its own
episodes would report "8/9 done" for a complete season of eight, and drawing it as
a card would put a ninth "episode" in the list that is really the parent.
"""

from __future__ import annotations

from pathlib import Path

_LIST = (
    Path(__file__).resolve().parent.parent
    / "webui" / "src" / "routes" / "active-downloads" / "-ui" / "vdl-list.tsx"
).read_text(encoding="utf-8")


# ── the server's verdict ─────────────────────────────────────────────────────

def test_the_pack_verdict_is_the_monitors_own():
    """Not a lookalike reimplementation — literally the function that decides
    whether a finished folder gets mapped."""
    from core.video.season_pack import is_pack_download
    _PACK_ROW = {"id": 3911, "kind": "show", "title": "Star Wars: Visions",
                 "search_ctx": '{"scope": "season", "title": "Star Wars: Visions", "season": 1}'}
    _EP_ROW = {"id": 3912, "kind": "show", "title": "Star Wars: Visions",
               "search_ctx": '{"scope": "episode", "title": "Star Wars: Visions", "season": 1, "episode": 1}'}
    assert is_pack_download(_PACK_ROW) is True
    assert is_pack_download(_EP_ROW) is False


def test_the_active_endpoint_annotates_every_row():
    """Every row gets the key, not just packs — an undefined `is_pack` would send
    the client down its legacy fallback for rows the server did answer for."""
    import inspect
    src = Path(inspect.getfile(__import__("api.video.downloads", fromlist=["x"]))).read_text(
        encoding="utf-8")
    fn = src[src.index("def _annotate_packs("):src.index("def _annotate_upgrade_watches(")]
    assert "from core.video.season_pack import is_pack_download" in fn
    assert 'r["is_pack"] = bool(is_pack_download(r))' in fn
    assert "_annotate_packs(rows)" in src, "and it must actually be called"


def test_a_broken_annotation_cannot_blank_the_downloads_page():
    """The page is how you see a stuck download; it must survive one odd row."""
    import inspect
    src = Path(inspect.getfile(__import__("api.video.downloads", fromlist=["x"]))).read_text(
        encoding="utf-8")
    fn = src[src.index("def _annotate_packs("):src.index("def _annotate_upgrade_watches(")]
    assert fn.count("except Exception") >= 2, "import AND per-row must both be guarded"


# ── the page's grouping decisions ────────────────────────────────────────────
#
# The legacy page's grouping functions (parseCtx / isPackRow / groupKey /
# splitGroup / groupWorthy, formerly driven through a node harness against
# webui/static/video/video-downloads-page.js) were ported 1:1 to
# webui/src/routes/active-downloads/-vdl.helpers.ts (vdlParseCtx /
# vdlIsPackRow / vdlGroupKey / vdlSplitGroup / vdlGroupWorthy) for the unified
# downloads page (PR #1651). The harness cases now live as vitest specs, which
# run in CI's webui check — verified equivalent case-by-case:
#
#   webui/src/routes/active-downloads/-vdl.helpers.test.ts
#     'season-pack grouping' › 'detects the pack row (server flag, then ctx fallbacks)'
#        ← packIsPack true / epIsPack false, ctx fallback, and the server-wins
#          case (explicit is_pack:false + season ctx → false)
#     'season-pack grouping' › 'keys same show+season together, nothing else'
#        ← sameKey true, packKey shape "g:<media>:s1", movieKey null
#     'season-pack grouping' › 'splits the pack head from the episode cards'
#        ← headId is the pack, episodeCount excludes the pack
#     'season-pack grouping' › 'a lone pack is still worth a group card'
#        ← packAlone true, loneEpisode false, twoEpisodes true
#
# (The old node --check "the page still parses" case is covered by the vitest
# suite itself — a syntax error fails collection — plus oxlint --type-check in
# CI's webui check.)
#
# What remains here are the component-level contracts: the grouping must
# actually be WIRED into the group card the way the old page wired it.


def test_cancelling_a_batch_still_cancels_the_pack():
    """The cancel handler harvests ids from the group BODY. Moving the pack to the
    head took it out of the body — so a batch whose episodes don't exist yet (the
    pack is still downloading) would have cancelled nothing at all."""
    assert "if (pack && packActive && !ids.includes(pack.id)) ids.push(pack.id);" in _LIST


def test_the_cancel_button_appears_while_only_the_pack_is_running():
    assert "{act || packActive ? (" in _LIST


def test_the_head_alone_keeps_the_group_visible():
    """Filtering counts visible CARDS; with the pack no longer a card, a
    downloading pack with no episodes yet would have hidden its own group."""
    assert "const headVisible = pack ? vdlMatchesFilter(pack.status, filter) : false;" in _LIST
    assert "visibleEps.length || headVisible ? '' : 'none'" in _LIST


def test_the_tally_counts_episodes_not_the_batch():
    """`done + '/' + total` over members would read 8/9 for a complete season of
    eight — the batch counted as one of its own children."""
    assert "const total = episodes.length;" in _LIST
    assert "${done}/${total} imported" in _LIST


def test_the_bar_shows_the_packs_own_progress_while_it_downloads():
    """Averaging episode progress before any episode exists reads 0% for a pack
    that is 60% downloaded."""
    assert "const pct = packActive ? clampPct(pack?.progress)" in _LIST
