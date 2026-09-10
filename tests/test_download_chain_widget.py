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


def _rule(css: str, selector: str) -> str:
    """The body of a TOP-LEVEL rule.

    Anchored to the start of a line, because the mobile overrides repeat these
    selectors indented inside a media query — and they now sit earlier in the
    file, so a plain split found the override and reported the base rule as
    missing its own declarations.
    """
    m = re.search(rf"^{re.escape(selector)}\s*{{([^}}]*)}}", css, re.M)
    assert m, f"no top-level rule for {selector}"
    return m.group(1)


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


def test_there_is_exactly_one_empty_slot(js):
    """The question a person has looking at this is "where does the next one
    go". A row of identical empty boxes answers it worse than a single obvious
    one, so only the end of the chain gets a slot."""
    fn = js.split("function renderDownloadChain(", 1)[1].split("\nwindow.", 1)[0]
    assert fn.count('id="dlchain-slot"') == 1
    assert "dlchain-slot" in fn


def test_the_slot_says_what_goes_in_it_and_why(js):
    """A thin strip with dim text reads as a divider. The automation builder's
    slot is big, dashed and explains itself, and that is the thing in this app
    people already understand."""
    fn = js.split("function renderDownloadChain(", 1)[1].split("\nwindow.", 1)[0]
    assert "Drag a source here" in fn
    assert "tried first" in fn            # empty chain
    assert "tried after" in fn            # naming the source above it
    assert "dlchain-slot-title" in fn and "dlchain-slot-hint" in fn


def test_the_slot_is_a_target_not_a_divider():
    css = _read("webui/static/style.css")
    block = css.split("/* ── Download chains", 1)[1]
    css_all = _read("webui/static/style.css")
    slot = _rule(css_all, ".dlchain-slot")
    assert "min-height" in slot
    assert "dashed" in slot
    # empty chain gets the bigger one, the way the builder's first slot is
    assert "min-height" in _rule(css_all, ".dlchain-slot.first")


def test_clicking_is_offered_as_well_as_dragging(index):
    """Drag-only is a trap on a touchpad, and the tiles already take a click."""
    assert "drag one across, or click it" in index


def test_the_pool_uses_source_tiles(js):
    """The thing you drag should look like the thing you configure on the
    Sources tab — same logo, same card shape."""
    fn = js.split("function _dlchainTile(", 1)[1].split("\n}", 1)[0]
    assert "dlchain-tile-art" in fn and "dlchain-tile-name" in fn
    assert "m.icon" in fn and "m.emoji" in fn


def test_a_step_says_where_it_sits(js):
    """"Fallback 2" beats "then": it says what the position MEANS, not just
    that there is one above it."""
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "Tried first" in fn
    assert "Fallback" in fn
    assert "dlchain-step-rank" in fn


def test_the_logo_carries_the_step(js):
    """It is the fastest thing to recognise and the only part that differs at a
    glance, so it gets real size and a well of its own rather than sitting in
    the text line at 20px."""
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "dlchain-step-art" in fn
    css = _read("webui/static/style.css")
    art = _rule(css, ".dlchain-step-art")
    # it fills the card and stands in a well of its own. The height is a range,
    # not a magic number: the point is that the mark is a real graphic rather
    # than something sitting in the text line at 20px, and pinning the exact
    # value only made a later density pass fail for no reason.
    # It no longer stretches - the name and role take the free space now - but
    # it keeps a real size and a well of its own rather than sitting in the text
    # line at 20px. The height is a floor, not a magic number.
    assert "flex-shrink: 0" in art
    height = int(re.search(r"height:\s*(\d+)px", art).group(1))
    assert height >= 28, f"the mark has shrunk back into the text line ({height}px)"
    assert "border-radius" in art and "background" in art


def test_the_first_link_reads_as_the_primary():
    """It is the one that usually answers, so it should not look identical to
    a fallback three deep."""
    css = _read("webui/static/style.css")
    block = css.split("/* ── Download chains", 1)[1]
    assert ".dlchain-step:first-child {" in block
    first = block.split(".dlchain-step:first-child {", 1)[1].split("}", 1)[0]
    assert "--accent-rgb" in first


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
    css_all = _read("webui/static/style.css")
    conn = _rule(css_all, ".dlchain-connector")
    assert "width: 2px" in conn                      # same as .flow-connector
    assert "--accent-rgb" in conn
    assert ".dlchain-connector::after" in block       # the arrowhead
    assert "dashed" in _rule(css_all, ".dlchain-slot")


def test_the_source_dropdown_is_no_longer_a_control(index):
    """The chain IS that setting: one source in it means "<that source> only",
    two or more means hybrid. The select stays because saveSettings reads it to
    persist download_source.mode — it is the transport, not a control — but two
    controls for one setting is how they drift apart."""
    assert 'id="download-source-mode"' in index          # still the transport
    before = index.split('id="download-source-mode"', 1)[0]
    group = before[before.rindex('<div class="form-group'):]
    assert "hidden" in group.split(">", 1)[0], "the dropdown is still on screen"


def test_the_audiobook_mode_dropdown_is_hidden_too(index):
    assert 'id="audiobook-download-mode"' in index
    before = index.split('id="audiobook-download-mode"', 1)[0]
    group = before[before.rindex('<div class="form-group'):]
    assert "hidden" in group.split(">", 1)[0]


def test_the_old_audiobook_arrow_list_is_hidden(index):
    """renderAudiobookHybrid still writes into it, so it stays in the DOM."""
    assert 'id="audiobook-hybrid-rows"' in index
    line = next(ln for ln in index.splitlines() if 'id="audiobook-hybrid-rows"' in ln)
    assert "hidden" in line


def test_mode_is_still_persisted(js):
    """Hiding the select must not stop it being saved — it is how
    download_source.mode reaches the server at all."""
    body = js.split("async function saveSettings", 1)[1]
    assert "mode: document.getElementById('download-source-mode').value" in body


def test_the_widget_drives_the_hidden_transports(js):
    """Both hidden selects are written by the chain adapters; nothing else sets
    them any more, so a chain change that skipped this would save the old mode."""
    spec = js.split("const DLCHAIN_KINDS = {", 1)[1].split("\n};", 1)[0]
    assert "getElementById('download-source-mode')" in spec
    assert "getElementById('audiobook-download-mode')" in spec


def test_the_dark_brand_marks_are_inverted(js):
    """Tidal, Qobuz and SoundCloud ship dark-foreground marks that vanish
    against the dark UI. `brightness(0) invert(1)` is this app's existing recipe
    for "render this image as pure white" — the equalizer and auto-sync icons
    already use it."""
    marks = js.split("const INVERT_BRAND_MARKS = new Set([", 1)[1].split("]", 1)[0]
    for brand in ("tidal", "qobuz", "soundcloud"):
        assert f"'{brand}'" in marks, brand
    css = _read("webui/static/style.css")
    rule = _rule(css, ".dlchain-mark.is-inverted")
    assert "brightness(0) invert(1)" in rule


def test_both_the_pool_and_the_chain_invert(js):
    """A logo that reads in one column and vanishes in the other is worse than
    either on its own."""
    for fn_name in ("_dlchainTile", "_dlchainStep"):
        fn = js.split(f"function {fn_name}(", 1)[1].split("\n}", 1)[0]
        assert "INVERT_BRAND_MARKS.has(id)" in fn, fn_name


def test_a_step_says_which_source_it_is(js):
    """REVERSED, deliberately. This test used to assert the opposite: cards were
    logo-only and narrow, because a full-width row holding one 30px mark was
    mostly empty space.

    That fixed emptiness inside the card and created it outside: a 190px card
    centred in a ~750px column left most of the column blank, which is what made
    the panel look unfinished. The column is a rail now, the row is full width,
    and it carries the source name and its role - a logo alone could not say
    which step was which anyway, since Amazon Music renders as an emoji shopping
    cart and Usenet as a newspaper.

    If logo-only is wanted back, narrow the rail rather than the card."""
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "dlchain-step-name" in fn
    assert "dlchain-step-role" in fn
    assert "dlchain-step-art" in fn
    css = _read("webui/static/style.css")
    step = _rule(css, ".dlchain-step")
    assert "width: 100%" in step, "the row should fill its rail"
    cols = _rule(css, ".dlchain-columns")
    assert "1.55fr" in cols, "the pool, not the chain, should hold the extra width"


def test_the_name_still_reaches_a_pointer_and_a_reader(js):
    """Dropping the visible text cannot mean dropping the information."""
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "title=" in fn and "aria-label=" in fn
    assert "Tried first" in fn and "Fallback" in fn


def test_there_are_arrows_as_well_as_dragging(js):
    """Dragging is precise work on a touchpad and impossible on a phone."""
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "dlchainMove(" in fn
    assert "dlchain-move" in fn
    move = js.split("function dlchainMove(", 1)[1].split("\nwindow.", 1)[0]
    assert "_dlchainCommit()" in move


def test_the_end_arrows_are_disabled(js):
    """An arrow that does nothing is worse than no arrow."""
    fn = js.split("function _dlchainStep(", 1)[1].split("\n}", 1)[0]
    assert "position === 1 ? ' disabled'" in fn
    assert "position === total ? ' disabled'" in fn


def test_moving_past_either_end_is_a_no_op(js):
    move = js.split("function dlchainMove(", 1)[1].split("\nwindow.", 1)[0]
    assert "j < 0 || j >= _dlchainOrder.length" in move


def test_it_works_on_a_phone():
    """Dragging is not realistic on a touch screen, so the arrows are the
    primary control there and need a real tap target."""
    css = _read("webui/static/style.css")
    block = css.split("/* ── Download chains", 1)[1]
    assert "@media (max-width: 560px)" in block
    phone = block.split("@media (max-width: 560px)", 1)[1].split("\n}", 1)[0]
    assert ".dlchain-step { width: 100%" in phone
    assert ".dlchain-move" in phone and ".dlchain-btn" in phone
    assert ".dlchain-tabs" in phone


def test_the_behaviour_settings_are_their_own_group(index):
    """REVERSED. These used to sit inside the pool's column, and this test
    asserted that position, because the column was empty below the tiles.

    Filling space is not a reason to group controls. Stream source, concurrency
    and the search timeout are general download settings with nothing to do with
    the chain, and putting them in the pool's column made the lower half of the
    panel read as a jumble. They are their own titled group below the chain now,
    and the pool is simply allowed to be as tall as its contents.

    The claim is position: after BOTH columns, not inside either one."""
    assert 'id="dlchain-behaviour"' in index
    assert "Download behaviour" in index
    pool_at = index.index('id="dlchain-pool"')
    chain_at = index.index('id="dlchain-list"')
    group_at = index.index('id="dlchain-behaviour"')
    assert group_at > chain_at > pool_at, "the group is still tangled in a column"


def test_the_behaviour_group_is_music_only(js):
    """They are download_source.* - music-wide. Shown under a video or audiobook
    chain they would claim to apply there.

    It has to hide the WRAPPER, not the inner block: the group carries a
    "Download behaviour" heading now, and hiding only the fields would leave
    that heading floating above nothing on the other two tabs."""
    fn = js.split("function renderDownloadChain(", 1)[1].split("\nwindow.", 1)[0]
    assert "dlchain-behaviour" in fn, "hides the inner block, orphaning the heading"
    assert "kind !== 'music'" in fn


def test_moving_them_did_not_orphan_their_settings(js, index):
    """They are read by saveSettings by id, so the move is safe only as long as
    every id still exists — which is exactly the wipe this page already had."""
    for el in ("stream-source", "max-concurrent-downloads", "source-search-timeout",
               "test-all-sources-btn"):
        assert f'id="{el}"' in index, el


def test_grouping_can_never_lose_a_source(js):
    """The pool is grouped now. A hard-coded group map plus eleven sources is a
    setup where adding a twelfth to HYBRID_SOURCES and forgetting DLCHAIN_GROUPS
    makes it silently unavailable - it would not appear in the pool, so it could
    never be added to a chain, and nothing would say why.

    _dlchainPoolHtml therefore ends with an "Other" group built from whatever is
    left over. This test pins that fallback, and separately checks that every id
    currently in HYBRID_SOURCES is actually placed, so the grouping stays
    deliberate rather than drifting into Other one source at a time.
    """
    fn = js.split("function _dlchainPoolHtml(", 1)[1].split("\n}", 1)[0]
    assert "'Other'" in fn, "no leftover group: a new source would vanish"
    assert "!seen.has(id)" in fn, "the Other group is not built from the leftovers"

    grouped = set(re.findall(r"'([a-z_]+)'", js.split("const DLCHAIN_GROUPS", 1)[1].split("];", 1)[0]))
    listed = set(re.findall(r"id: '([a-z_]+)'", js.split("const HYBRID_SOURCES", 1)[1].split("];", 1)[0]))
    missing = listed - grouped
    assert not missing, f"these sources fall into Other: {sorted(missing)}"


def test_hiding_a_tile_actually_hides_it():
    """.dlchain-tile is display:flex and .dlchain-group is a block, so setting
    .hidden on them does nothing without an explicit [hidden] rule - a class
    rule with display beats the browser's own [hidden] styling. The filter sets
    .hidden on both, so without these two rules filtering would appear to do
    nothing at all. This exact trap already shipped once on this page with
    .hybrid-source-list.
    """
    css = _read("webui/static/style.css")
    for sel in (".dlchain-tile[hidden]", ".dlchain-group[hidden]"):
        assert "display: none" in _rule(css, sel), f"{sel} does not actually hide"


def test_the_filter_survives_a_rerender(js):
    """Adding a source re-renders the pool. If the query lived in the input only,
    the freshly built tiles would come back unfiltered while the box still showed
    the text - so the state sits outside the render and is re-applied after it.
    """
    assert "let _dlchainQuery" in js
    render = js.split("function renderDownloadChain(", 1)[1].split("\n}", 1)[0]
    assert "_dlchainApplyFilter()" in render, "render does not re-apply the filter"
    apply_fn = js.split("function _dlchainApplyFilter(", 1)[1].split("\n}", 1)[0]
    assert "dataset.src" in apply_fn, "filter matches display names only"
    assert "dlchain-no-match" in apply_fn, "a query matching nothing says nothing"


def test_a_hidden_form_group_is_actually_hidden(index):
    """The "Download Source" select is marked hidden in the markup: the chain
    widget replaced it, and it survives only because saveSettings reads it to
    persist download_source.mode. It was on screen anyway, at the top of the
    section, duplicating the chain underneath it.

    Cause: #settings-page .form-group sets display:flex, and a class rule with
    display outranks the browser's own [hidden] styling, so the attribute did
    nothing. Exactly the trap .hybrid-source-list hit on this same page. Any
    form-group the page hides is affected, not just this one.
    """
    css = _read("webui/static/style.css")
    rule = _rule(css, "#settings-page .form-group[hidden]")
    assert "display: none" in rule and "!important" in rule

    # and the thing that made it visible is still the reason we need the guard
    base = _rule(css, "#settings-page .form-group")
    assert "display: flex" in base

    # the select itself stays in the DOM - it is the save/load transport
    assert 'id="download-source-mode"' in index


def test_the_behaviour_rows_stack_on_a_phone():
    """The settings page stacks its form rows with

        #settings-page .form-group { flex-direction: column !important }

    inside its own narrow media query. That does nothing to the behaviour rows,
    because they are display:grid - flex-direction is not a grid property. Left
    alone they stay a three-column grid with a 140px minimum label column on a
    360px screen, which pushes the whole page sideways.

    So the grid has to be collapsed explicitly at the same breakpoint. This also
    checks the desktop rule really is a grid, because the day it goes back to
    flex is the day this override becomes the thing that breaks the layout.
    """
    css = _read("webui/static/style.css")

    base = _rule(css, "#settings-page .dlchain-behaviour .form-group")
    assert "display: grid" in base, "no longer a grid - revisit the phone override"

    narrow = css.split("@media (max-width: 560px)", 1)
    assert len(narrow) > 1, "the phone breakpoint is gone"
    body = narrow[1]
    assert "#settings-page .dlchain-behaviour .form-group" in body, (
        "the behaviour rows are never collapsed for a phone"
    )
    stacked = body.split("#settings-page .dlchain-behaviour .form-group", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 1fr" in stacked


def test_the_phone_drop_slot_is_not_taller_than_the_desktop_one():
    """The phone rules were written when the chain was roomier, and kept a 74px
    slot after the desktop one was compacted to 54px - so the target grew on the
    smaller screen. Whatever the values become, the phone one may not exceed the
    desktop one."""
    css = _read("webui/static/style.css")
    desktop = int(re.search(r"min-height:\s*(\d+)px", _rule(css, ".dlchain-slot")).group(1))
    body = css.split("@media (max-width: 560px)", 1)[1]
    phone = int(re.search(r"\.dlchain-slot\s*{[^}]*?min-height:\s*(\d+)px", body, re.S).group(1))
    assert phone <= desktop, f"phone slot {phone}px is taller than desktop {desktop}px"


def test_the_widget_is_shared_with_the_video_side(index):
    """The whole point of building it this way. The video side hides music
    controls with

        body[data-side="video"] [data-music-only] { display: none !important }

    so a single data-music-only anywhere up the tree deletes this widget from
    the video side silently - it just would not be there, with nothing to say
    why. Pin that neither the widget, its section, nor the Sources tab sits
    under one.
    """
    import re as _re

    def ancestors(pos):
        stack = []
        for m in _re.finditer(r"<(/?)(div|section|main)\b([^>]*?)(/?)>", index[:pos], _re.I):
            if m.group(4) == "/":
                continue
            if m.group(1):
                if stack:
                    stack.pop()
            else:
                stack.append(m.group(0))
        return stack

    for needle in ('id="download-chain-widget"',
                   'settings-section-static',
                   '<div class="settings-group" data-stg="sources">'):
        at = index.index(needle)
        gated = [a for a in ancestors(at) if "data-music-only" in a]
        assert not gated, f"{needle} is hidden on the video side by {gated}"
        # ...and its OWN tag, which the ancestor walk cannot see: the position
        # lands inside the tag, so h[:pos] ends mid-tag and the regex never
        # matches it. Putting the attribute straight on the element is the most
        # likely way to break this, and the ancestor check alone sailed past it.
        own = index[index.rfind("<", 0, at):index.index(">", at) + 1]
        assert "data-music-only" not in own, f"{needle} is itself marked music-only"

    # and the tab buttons that reach them
    for tab in ("downloads", "sources"):
        btn = index.split(f'data-tab="{tab}"', 1)[0].rsplit("<button", 1)[1]
        assert "data-music-only" not in btn, f"the {tab} tab button is music-only"


def test_the_chain_opens_on_the_side_you_are_standing_on(js):
    """_dlchainKind defaults to 'music', so a video user opening Downloads got
    the MUSIC chain and the music-only behaviour group beneath it - on a widget
    whose entire purpose is being shared by both sides.

    An explicit tab pick still wins; the side only supplies the default."""
    assert "_dlchainSyncKindToSide" in js
    fn = js.split("function _dlchainSyncKindToSide(", 1)[1].split("\n}", 1)[0]
    assert "data-side" in fn and "video" in fn

    load = js.split("async function _dlchainLoad(", 1)[1].split("\n}", 1)[0]
    assert "_dlchainSyncKindToSide()" in load, "the default is never applied"

    switch = js.split("function switchDownloadChain(", 1)[1].split("\n}", 1)[0]
    assert "_dlchainKindChosen = true" in switch, "a manual pick would be overridden"


def test_the_downloads_tab_lets_the_shared_section_through_on_video():
    """The check above was not enough, and reported a false pass.

    Side gating is not only data-music-only. video-side.css also carries blanket
    per-tab rules, and this one:

        body[data-side="video"] [data-stg="downloads"]:not([data-video-only]):not([data-shared])

    hides EVERY music-side element on the Downloads tab. The chain widget carries
    no data-music-only, so an ancestor scan says "visible" - while that rule was
    deleting the whole Source Settings section on the video side. The widget has a
    Video tab and was built to be shared, and it was not reachable from the video
    side at all.

    data-shared is the opt-out the rule already provides. It has to be on the
    header, the body AND the group: the selector matches each of them
    independently, so one bare wrapper still hides everything inside it.
    """
    css = _read("webui/static/video/video-side.css")
    assert ':not([data-shared])' in css, "the opt-out this test relies on is gone"

    index = _read("webui/index.html")
    for needle in ('<div class="settings-section-header settings-section-static" data-stg="downloads"',
                   '<div class="settings-section-body" data-stg="downloads"',
                   '<div class="settings-group source-settings-wrapper" data-stg="downloads"'):
        at = index.index(needle)
        tag = index[at:index.index('>', at) + 1]
        assert 'data-shared' in tag, (
            "this wrapper is hidden on the video side by the downloads blanket "
            "rule, which takes the whole chain widget with it: " + needle[:60]
        )


def test_every_source_has_a_brand_colour():
    """Colour on these cards comes from the SOURCE, not from one page-wide tint.
    Each card sets --brand from its data-src and the logo well, the wash and the
    hover edge all read from it - so Tidal is cyan and SoundCloud is orange, and
    you can tell them apart without reading.

    A source with no --brand falls back to the user's accent, which does not look
    broken but does quietly undo the point: add a twelfth source and it is the
    only grey card on the page. So every id that can appear on a card needs one,
    and a colour defined for an id that no longer exists is dead weight.
    """
    js = _read("webui/static/settings.js")
    css = _read("webui/static/style.css")

    hybrid = set(re.findall(r"id: '([a-z_]+)'", js.split("const HYBRID_SOURCES", 1)[1].split("];", 1)[0]))
    extra = set(re.findall(r"id: '([a-z_]+)'", js.split("EXTRA_SOURCE_TILES", 1)[1].split("];", 1)[0]))
    video = set(re.findall(r"'([a-z]+)'", js.split("sources: () => ['soulseek'", 1)[0][-1:] or "")) or {
        "soulseek", "torrent", "usenet", "extto"}
    needed = hybrid | extra | video
    defined = set(re.findall(r'\[data-src="([a-z_]+)"\]', css))

    assert not (needed - defined), f"no brand colour for: {sorted(needed - defined)}"
    assert not (defined - needed), f"brand colour for sources that do not exist: {sorted(defined - needed)}"

    # the fallback has to exist too, or a new source renders with no --brand at all
    assert "--brand: var(--accent-rgb)" in css


def test_first_in_chain_wears_the_accent_not_its_brand():
    """Brand says WHO, accent says WHAT. "Runs first" is a state, so it has to
    look the same whichever source happens to be sitting in that slot - if the
    top card wore its own brand instead, the highlight would change colour every
    time you reordered, and stop reading as a state at all."""
    css = _read("webui/static/style.css")
    rule = re.search(r'#settings-page \.dlchain-step:first-child\s*{([^}]*)}', css)
    assert rule, "the first step has no rule of its own"
    body = rule.group(1)
    assert "--accent-rgb" in body, "the first step no longer carries the accent"
    first_bg = body.split("background:", 1)[1].split(";", 1)[0]
    assert "--accent-rgb" in first_bg, "its fill must lead with the accent, not the brand"

