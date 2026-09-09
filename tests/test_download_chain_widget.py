"""One download-chain editor for all three media types.

Music, video and audiobooks each store the same thing — a mode plus an ordered
list of sources — and each had its own editor:

  * music      a drag list with status dots, cogs and per-source toggles
  * audiobooks arrow buttons and a checkbox, no drag, no status
  * video      a third implementation inside a pop-up on the video side only

Same concept, three looks, three behaviours, three places to keep in step. This
is one widget with three adapters; the adapters are the only part that differs,
because where the chain is read from and written back to is the only real
difference between them.

A chain of one IS single-source mode. There is no separate mode switch, because
mode and order were never independent: a user choosing one source and a user
dragging one source into the chain mean the same thing.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_KINDS = ("music", "video", "audiobooks")


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def js():
    return _read("webui/static/settings.js")


@pytest.fixture(scope="module")
def index():
    return _read("webui/index.html")


def test_the_widget_has_somewhere_to_render(index):
    for el in ("download-chain-widget", "dlchain-tabs", "dlchain-pool", "dlchain-list"):
        assert f'id="{el}"' in index, el


@pytest.mark.parametrize("kind", _KINDS)
def test_every_media_type_is_a_tab(js, kind):
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    assert f"{kind}: {{" in spec


@pytest.mark.parametrize("kind", _KINDS)
def test_each_kind_can_read_and_write_itself(js, kind):
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    block = spec.split(f"{kind}: {{", 1)[1]
    block = block.split("\n    },", 1)[0]
    assert "read:" in block, f"{kind} cannot load its chain"
    assert "write:" in block, f"{kind} cannot save its chain"
    assert "sources:" in block, f"{kind} has no source list"


def test_video_saves_to_its_own_store(js):
    """Video settings live in video.db, not app_config, so it cannot ride the
    settings page's save — it has to post to its own endpoint."""
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    video = spec.split("video: {", 1)[1].split("\n    },", 1)[0]
    assert "/api/video/downloads/config" in video


def test_music_and_audiobooks_ride_the_page_save(js):
    """Both are app_config keys the settings POST already handles. Writing them
    a second way would be a second source of truth."""
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    for kind in ("music", "audiobooks"):
        block = spec.split(f"{kind}: {{", 1)[1].split("\n    },", 1)[0]
        assert "debouncedAutoSaveSettings()" in block, kind
        assert "/api/" not in block, f"{kind} should not post on its own"


def test_music_keeps_feeding_the_state_the_rest_of_the_page_reads(js):
    """saveSettings reads the music chain through getHybridOrder() and two
    hidden selects, and the Sources tiles read _hybridSourceStatus. The widget
    replaced the list that used to own that state, so it has to keep it fed or
    the chain silently stops saving."""
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    music = spec.split("music: {", 1)[1].split("\n    },", 1)[0]
    assert "_hybridSourceOrder" in music
    assert "_hybridSourceEnabled" in music
    assert "_syncHybridHiddenSelects()" in music


def test_one_source_means_single_mode(js):
    """The stored `mode` is still a real field the backends read, so a chain of
    one has to write the source name rather than 'hybrid'."""
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    music = spec.split("music: {", 1)[1].split("\n    },", 1)[0]
    assert "order.length > 1 ? 'hybrid'" in music
    video = spec.split("video: {", 1)[1].split("\n    },", 1)[0]
    assert "order.length > 1" in video


def test_the_chain_cannot_be_emptied(js):
    """An empty chain means nothing can download at all, which is never what a
    drag was trying to say. The video side already refused it; now all three do."""
    fn = js.split("function dlchainRemove(", 1)[1].split("\nwindow.", 1)[0]
    assert "_dlchainOrder.length <= 1" in fn
    assert "return" in fn


def test_dropping_between_columns_works_both_ways(js):
    fn = js.split("function _dlchainWireDrag(", 1)[1].split("\n}\n", 1)[0]
    assert "dlchainRemove(src)" in fn      # chain -> pool removes
    assert "_dlchainOrder.push(src)" in fn  # pool -> chain appends


def test_the_video_chain_is_re_read_on_arrival(js):
    """It lives in video.db and can be changed from the video side, so a value
    rendered earlier in the session may already be stale."""
    fn = js.split("function switchSettingsTab(", 1)[1].split("\n}", 1)[0]
    assert "_dlchainLoad()" in fn


# ---------------------------------------------------------------------------
# The editors it replaced
# ---------------------------------------------------------------------------

def test_the_video_overlay_no_longer_edits_the_chain():
    """Two editors for one setting is the drift this removes. The overlay keeps
    a read-only summary and points at the new tab."""
    vss = _read("webui/static/video/video-service-status.js")
    assert "_vssSetSource = function" not in vss
    assert "_vssMode = function" not in vss
    assert "function wireHybridDrag" not in vss
    assert "Settings" in vss and "Downloads" in vss


def test_nothing_still_calls_the_removed_video_handlers():
    """A dead onclick is a button that looks live and does nothing."""
    for rel in ("webui/static/video/video-service-status.js", "webui/index.html"):
        src = _read(rel)
        for dead in ("_vssSetSource('", "_vssMode('", "wireHybridDrag()"):
            assert dead not in src, f"{dead} still referenced in {rel}"


def test_the_legacy_music_list_is_kept_but_hidden(index):
    """buildHybridSourceList still owns the per-source connection probing the
    Sources tiles read, so the element stays — hidden, not deleted."""
    assert 'id="hybrid-source-list"' in index
    line = next(ln for ln in index.splitlines() if 'id="hybrid-source-list"' in ln)
    assert "hidden" in line


def test_the_styling_reuses_the_pages_own_vocabulary():
    css = _read("webui/static/style.css")
    block = css.split("/* ── Download chains", 1)[1]
    assert "--accent-rgb" in block
    # the two draggable things and the two drop targets
    assert ".dlchain-step.dragging" in block
    assert ".dlchain-tile.dragging" in block
    assert ".dlchain-list.drag-over" in block
    assert ".dlchain-pool.drag-over" in block


# ---------------------------------------------------------------------------
# It reads as a flow, not a list
# ---------------------------------------------------------------------------

def test_the_chain_renders_as_a_flow(js):
    """Each source is tried in turn, so the arrow between steps is the "then
    try" — a plain list does not say that."""
    fn = js.split("function renderDownloadChain(", 1)[1].split("\nwindow.", 1)[0]
    assert "dlchain-connector" in fn
    assert "_dlchainStep(" in fn


def test_there_is_always_somewhere_to_drop(js):
    """The slot stays even when the chain is full, so there is an obvious
    target rather than having to hit a gap between two rows."""
    fn = js.split("function renderDownloadChain(", 1)[1].split("\nwindow.", 1)[0]
    assert "dlchain-slot" in fn
    assert "Drag another source here" in fn
    assert "downloads need at least one" in fn


def test_the_pool_uses_source_tiles(js):
    """The thing you drag should look like the thing you configure on the
    Sources tab — same logo, same card shape."""
    fn = js.split("function _dlchainTile(", 1)[1].split("\n}", 1)[0]
    assert "dlchain-tile-art" in fn and "dlchain-tile-name" in fn
    assert "m.icon" in fn and "m.emoji" in fn


def test_a_step_says_where_it_sits(js):
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "tried first" in fn and "then" in fn
    assert "dlchain-step-rank" in fn


def test_dropping_on_a_step_reorders(js):
    """Insert-before is how you move something up the chain."""
    fn = js.split("function _dlchainWireDrag(", 1)[1].split("\n}\n", 1)[0]
    assert "_dlchainOrder.indexOf(target)" in fn
    assert "splice(" in fn


def test_a_near_miss_still_does_the_obvious_thing(js):
    """Dropping in the chain column but not on a step or the slot appends,
    rather than silently doing nothing."""
    fn = js.split("function _dlchainWireDrag(", 1)[1].split("\n}\n", 1)[0]
    assert "list.addEventListener('drop'" in fn


def test_the_legacy_music_list_is_really_hidden():
    """`hidden` alone did not work: .hybrid-source-list sets `display: flex`,
    and a class rule with display outranks the browser's own [hidden] rule, so
    the old widget rendered underneath the new one."""
    css = _read("webui/static/style.css")
    assert ".hybrid-source-list[hidden]" in css
    rule = css.split(".hybrid-source-list[hidden]", 1)[1].split("}", 1)[0]
    assert "display: none" in rule and "!important" in rule


def test_the_flow_borrows_the_builder_vocabulary():
    """Two builders in one app should not look like two apps."""
    css = _read("webui/static/style.css")
    block = css.split("/* ── Download chains", 1)[1]
    conn = block.split(".dlchain-connector {", 1)[1].split("}", 1)[0]
    assert "width: 2px" in conn                      # same as .flow-connector
    assert "--accent-rgb" in conn
    assert ".dlchain-connector::after" in block       # the arrowhead
    slot = block.split(".dlchain-slot {", 1)[1].split("}", 1)[0]
    assert "dashed" in slot
