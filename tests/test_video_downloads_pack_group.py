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

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_PAGE = Path("webui/static/video/video-downloads-page.js")


def _node():
    for candidate in ("node", "/mnt/c/Program Files/nodejs/node.exe"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None


# ── the server's verdict ─────────────────────────────────────────────────────

def _ctx(**kw):
    return json.dumps(kw)


_PACK_ROW = {"id": 3911, "kind": "show", "title": "Star Wars: Visions",
             "search_ctx": _ctx(scope="season", title="Star Wars: Visions", season=1)}
_EP_ROW = {"id": 3912, "kind": "show", "title": "Star Wars: Visions",
           "search_ctx": _ctx(scope="episode", title="Star Wars: Visions", season=1, episode=1)}


def test_the_pack_verdict_is_the_monitors_own():
    """Not a lookalike reimplementation — literally the function that decides
    whether a finished folder gets mapped."""
    from core.video.season_pack import is_pack_download
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

_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync('webui/static/video/video-downloads-page.js', 'utf8');
const grab = (name, end) => src.slice(src.indexOf('function ' + name), src.indexOf(end));
const code = grab('parseCtx', 'function fact(') + grab('isPackRow', 'function makeGroup(');
const F = new Function(code + '; return {isPackRow, groupKey, splitGroup, groupWorthy};')();
const pack = %s, eps = %s, legacy = %s;
console.log(JSON.stringify({
  packIsPack:   F.isPackRow(pack),
  epIsPack:     F.isPackRow(eps[0]),
  sameKey:      F.groupKey(pack) === F.groupKey(eps[0]),
  packKey:      F.groupKey(pack),
  headId:       (F.splitGroup([pack].concat(eps)).pack || {}).id,
  episodeCount: F.splitGroup([pack].concat(eps)).episodes.length,
  packAlone:    F.groupWorthy([pack]),
  loneEpisode:  F.groupWorthy([eps[0]]),
  twoEpisodes:  F.groupWorthy([eps[0], eps[1]]),
  movieKey:     F.groupKey({kind: 'movie', title: 'M', search_ctx: '{}'}),
  legacyIsPack: F.isPackRow(legacy),
  serverWins:   F.isPackRow({kind: 'show', is_pack: false,
                  search_ctx: JSON.stringify({scope: 'season', season: 1})}),
}));
"""


def _drive():
    pack = dict(_PACK_ROW, is_pack=True)
    eps = [dict(_EP_ROW, id=3911 + n, is_pack=False,
                search_ctx=_ctx(scope="episode", title="Star Wars: Visions", season=1, episode=n))
           for n in range(1, 9)]
    legacy = {k: v for k, v in pack.items() if k != "is_pack"}
    script = _HARNESS % (json.dumps(pack), json.dumps(eps), json.dumps(legacy))
    out = subprocess.run([_node(), "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_pack_lands_in_the_same_group_as_its_episodes():
    """THE bug: groupKey returned null for the pack, so it rendered alone."""
    d = _drive()
    assert d["packIsPack"] is True and d["epIsPack"] is False
    assert d["sameKey"] is True
    assert d["packKey"] == "g:Star Wars: Visions:s1"


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_pack_is_the_head_and_the_episodes_are_the_body():
    d = _drive()
    assert d["headId"] == 3911
    assert d["episodeCount"] == 8, "the pack must not be counted among its own episodes"


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_a_pack_with_no_episodes_yet_is_still_a_batch():
    """Right after the grab the pack is the ONLY row for that season. Under the
    old >=2 rule it fell through to a bare card — which is what Boulder saw."""
    d = _drive()
    assert d["packAlone"] is True


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_ordinary_rows_are_untouched():
    """Additive: one episode still stands alone, two still group, a movie never
    groups at all."""
    d = _drive()
    assert d["loneEpisode"] is False
    assert d["twoEpisodes"] is True
    assert d["movieKey"] is None


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_server_verdict_wins_over_the_fallback():
    """If the server says not-a-pack, a season-looking search_ctx must not
    override it — otherwise the fallback quietly becomes the real rule."""
    assert _drive()["serverWins"] is False


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_a_payload_without_is_pack_still_renders():
    """Browsers cache JS; a new page against an older response must not regress
    to the broken layout."""
    assert _drive()["legacyIsPack"] is True


# ── the regression the head-not-member split could introduce ────────────────

def _page() -> str:
    return _PAGE.read_text(encoding="utf-8")


def test_cancelling_a_batch_still_cancels_the_pack():
    """The cancel handler harvests ids from the group BODY. Moving the pack to the
    head took it out of the body — so a batch whose episodes don't exist yet (the
    pack is still downloading) would have cancelled nothing at all."""
    src = _page()
    assert "el._packActiveId = packActive ? pack.id : null;" in src
    handler = src[src.index("var gcan = e.target.closest("):src.index("var gtog = e.target.closest(")]
    assert "gel._packActiveId" in handler


def test_the_cancel_button_appears_while_only_the_pack_is_running():
    src = _page()
    assert "var wantAct = (act || packActive)" in src


def test_the_head_alone_keeps_the_group_visible():
    """Filtering counts visible CARDS; with the pack no longer a card, a
    downloading pack with no episodes yet would have hidden its own group."""
    src = _page()
    assert "var headVis = parts.pack ? matches(parts.pack.status) : false;" in src
    assert "g.style.display = (visN || headVis) ? '' : 'none';" in src


def test_the_tally_counts_episodes_not_the_batch():
    """`done + '/' + total` over members would read 8/9 for a complete season of
    eight — the batch counted as one of its own children."""
    src = _page()
    fn = src[src.index("function patchGroup("):src.index("function makeCard(")]
    assert "var total = eps.length" in fn
    assert "members.length" not in fn, "the tally must be over episodes only"


def test_the_bar_shows_the_packs_own_progress_while_it_downloads():
    """Averaging episode progress before any episode exists reads 0% for a pack
    that is 60% downloaded."""
    fn = _page()
    fn = fn[fn.index("function patchGroup("):fn.index("function makeCard(")]
    assert "packActive ? Math.max(0, Math.min(100, pack.progress || 0))" in fn


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_page_still_parses():
    out = subprocess.run([_node(), "--check", str(_PAGE)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
