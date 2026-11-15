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


# the chain, the tiles and the sources model are React since step 7a
# (SETTINGS_PORT.md): the contracts below now read those files
_TABS = "webui/src/features/settings/tabs/"


@pytest.fixture(scope="module")
def model():
    return _read(_TABS + "sources-model.ts")


@pytest.fixture(scope="module")
def tab():
    return _read(_TABS + "sources-tab.tsx")


@pytest.fixture(scope="module")
def downloads():
    return _read(_TABS + "downloads-tab.tsx")


def _body(src: str, marker: str) -> str:
    """a function's text, from marker to its matching closing brace"""
    start = src.index(marker)
    # the body's brace: after the parameter list (which may hold destructuring
    # and type braces) or an arrow
    m = re.compile(r"\)\s*\{|=>\s*\{").search(src, start)
    assert m, f"{marker} has no body"
    depth = 0
    for i in range(m.end() - 1, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"{marker} has no end")


def _kinds(model: str) -> str:
    return model.split("export const CHAIN_KINDS", 1)[1].split("\n};", 1)[0]


def _chain(tab: str) -> str:
    """the chain widget component, up to the tiles that follow it"""
    return tab.split("export function ChainWidget(", 1)[1].split("// ── the Sources tab's tiles", 1)[0]


def _step(tab: str) -> str:
    return _body(tab, "const step = (id: string, position: number, total: number) =>")


def test_the_widget_has_somewhere_to_render(index, tab):
    """the chain renders into a React slot on the Downloads tab, and keeps its ids"""
    assert 'data-stg-react="downloads" data-stg-part="chain"' in index
    for el in ("download-chain-widget", "dlchain-tabs", "dlchain-pool", "dlchain-list"):
        assert f'id="{el}"' in tab, el


@pytest.mark.parametrize("kind", _KINDS)
def test_every_media_type_is_a_tab(model, kind):
    assert f"\n  {kind}: {{" in _kinds(model)


@pytest.mark.parametrize("kind", _KINDS)
def test_each_kind_can_read_and_write_itself(model, kind):
    """the source list is in CHAIN_KINDS; reading is loadChain, writing commit"""
    block = _kinds(model).split(f"\n  {kind}: {{", 1)[1].split("\n  },", 1)[0]
    assert "sources:" in block, f"{kind} has no source list"
    load = _body(model, "async function loadChain()")
    commit = _body(model, "function commit(order: string[])")
    reads = {"music": "model.getState().music", "video": "readVideo()",
             "audiobooks": "audiobookChain()"}[kind]
    writes = {"music": "writeMusic(order)", "video": "writeVideo(",
              "audiobooks": "writeAudiobooks(order)"}[kind]
    assert reads in load, f"{kind} cannot load its chain"
    assert writes in commit, f"{kind} cannot save its chain"


def test_video_saves_to_its_own_store(model):
    """Video settings live in video.db, not app_config, so it cannot ride the
    settings page's save — it has to post to its own endpoint."""
    video = _body(model, "async function writeVideo(")
    assert "/api/video/downloads/config" in video
    assert "store." not in video


def test_music_and_audiobooks_ride_the_page_save(model):
    """Both are app_config keys the settings POST already handles. Writing them
    a second way would be a second source of truth. music goes through the
    settings store, audiobooks too (step 8c)."""
    music = _body(model, "function writeMusic(")
    assert "store.patch('download_source'" in music
    assert "/api/" not in music, "music should not post on its own"
    commit = _body(model, "function commit(order: string[])")
    books = commit.split("kind === 'audiobooks'", 1)[1].split("\n", 1)[0]
    assert "writeAudiobooks" in books and "/api/" not in books
    assert "store.patch('audiobooks'" in _body(model, "function writeAudiobooks(")


def test_music_keeps_feeding_the_state_the_rest_of_the_page_reads(model):
    """the tiles and Test all read the music chain from the model, so a chain
    edit has to update it as well as save it, or the tiles go stale."""
    music = _body(model, "function writeMusic(")
    assert "set({ music: order.slice(), mode, secondary: pair.secondary })" in music


def test_one_source_means_single_mode(model):
    """The stored `mode` is still a real field the backends read, so a chain of
    one has to write the source name rather than 'hybrid'."""
    music = _body(model, "function writeMusic(")
    assert "order.length > 1 ? 'hybrid'" in music
    video = _body(model, "async function writeVideo(")
    assert "order.length > 1" in video


def test_the_chain_cannot_be_emptied(tab):
    """An empty chain means nothing can download at all, which is never what a
    drag was trying to say. The video side already refused it; now all three do."""
    fn = _body(_chain(tab), "const remove = (id: string) =>")
    assert "full.length <= 1" in fn
    assert "return" in fn


def test_dropping_between_columns_works_both_ways(tab):
    chain = _chain(tab)
    assert "if (src && full.includes(src)) remove(src)" in chain  # chain -> pool removes
    assert "commit([...full, src])" in chain                     # pool -> chain appends


def test_the_video_chain_is_re_read_on_arrival(tab):
    """It lives in video.db and can be changed from the video side, so a value
    rendered earlier in the session may already be stale. the chain re-reads
    itself each time the Downloads tab is opened."""
    chain = _chain(tab)
    arrival = chain.split("if (!active || !opened) return;", 1)[1].split("}, [opened, active])", 1)[0]
    assert "model.loadChain()" in arrival


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


def test_the_legacy_music_list_is_gone(index, js):
    """REPLACED in step 7a. the hidden legacy list stayed only because
    buildHybridSourceList owned the connection probing the tiles read. that
    state is in the React sources model now, so the list, its two hidden
    selects and the code that fed them are gone, not hidden."""
    for el in ("hybrid-source-list", "hybrid-primary-source", "hybrid-secondary-source",
               "hybrid-settings-container"):
        assert f'id="{el}"' not in index, el
    assert "function buildHybridSourceList" not in js


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

def test_the_chain_renders_as_a_flow(tab):
    """Each source is tried in turn, so the arrow between steps is the "then
    try" — a plain list does not say that."""
    chain = _chain(tab)
    assert "dlchain-connector" in chain
    assert "step(id, i + 1, order.length)" in chain


def test_there_is_exactly_one_empty_slot(tab):
    """The question a person has looking at this is "where does the next one
    go". A row of identical empty boxes answers it worse than a single obvious
    one, so only the end of the chain gets a slot."""
    chain = _chain(tab)
    assert chain.count('id="dlchain-slot"') == 1
    # it sits after the steps, not inside the per-step render
    assert "dlchain-slot" not in _step(tab)


def test_the_slot_says_what_goes_in_it_and_why(tab):
    """A thin strip with dim text reads as a divider. The automation builder's
    slot is big, dashed and explains itself, and that is the thing in this app
    people already understand."""
    chain = _chain(tab)
    assert "Drag a source here" in chain
    assert "tried first" in chain            # empty chain
    assert "tried after" in chain            # naming the source above it
    assert "dlchain-slot-title" in chain and "dlchain-slot-hint" in chain


def test_the_slot_is_a_target_not_a_divider():
    css = _read("webui/static/style.css")
    block = css.split("/* ── Download chains", 1)[1]
    css_all = _read("webui/static/style.css")
    slot = _rule(css_all, ".dlchain-slot")
    assert "min-height" in slot
    assert "dashed" in slot
    # empty chain gets the bigger one, the way the builder's first slot is
    assert "min-height" in _rule(css_all, ".dlchain-slot.first")


def test_clicking_is_offered_as_well_as_dragging(tab):
    """Drag-only is a trap on a touchpad, and the tiles already take a click.
    (the chain's markup is in sources-tab.tsx now: index.html still holds the
    same hint for the subtitle provider chain, which would pass this falsely)"""
    chain = _chain(tab)
    assert "drag one across, or click it" in chain
    assert "onClick={() => add(id)}" in _body(chain, "const tile = (id: string) =>")


def test_the_pool_uses_source_tiles(tab):
    """The thing you drag should look like the thing you configure on the
    Sources tab — same logo, same card shape."""
    fn = _body(_chain(tab), "const tile = (id: string) =>")
    assert "dlchain-tile-art" in fn and "dlchain-tile-name" in fn
    assert "<Mark meta={meta}" in fn
    mark = _body(tab, "function Mark(")
    assert "meta.icon" in mark and "meta.emoji" in mark


def test_a_step_says_where_it_sits(tab):
    """"Fallback 2" beats "then": it says what the position MEANS, not just
    that there is one above it."""
    fn = _step(tab)
    assert "Tried first" in fn
    assert "Fallback" in fn
    assert "dlchain-step-rank" in fn


def test_the_logo_carries_the_step(tab):
    """It is the fastest thing to recognise and the only part that differs at a
    glance, so it gets real size and a well of its own rather than sitting in
    the text line at 20px."""
    assert "dlchain-step-art" in _step(tab)
    css = _read("webui/static/style.css")
    art = _rule(css, ".dlchain-step-art")
    # a real graphic with a well of its own; the height is a floor, not a
    # magic number
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


def test_dropping_on_a_step_reorders(tab):
    """Insert-before is how you move something up the chain."""
    fn = _step(tab)
    assert "next.indexOf(id)" in fn
    assert "splice(" in fn


def test_a_near_miss_still_does_the_obvious_thing(tab):
    """Dropping in the chain column but not on a step or the slot appends,
    rather than silently doing nothing."""
    chain = _chain(tab)
    lst = chain.split('id="dlchain-list"', 1)[1].split('id="dlchain-slot"', 1)[0]
    assert "onDrop=" in lst and "commit([...full, src])" in lst


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


def test_the_source_dropdown_is_gone(index, js):
    """REPLACED in step 7a. the chain IS that setting: one source in it means
    "<that source> only", two or more means hybrid. the hidden select only
    stayed as the transport saveSettings read download_source.mode from. the
    model holds the mode now and saves it, so the select is gone."""
    assert 'id="download-source-mode"' not in index
    assert "download-source-mode" not in js


def _mode_group_is_hidden() -> bool:
    """the audiobook mode select's form-group is hidden (the React panel,
    step 8c: a HintGroup given `hidden`, which HintGroup puts on its div)"""
    panel = _read(_TABS + "library-audiobooks.tsx")
    before = panel.split('id="audiobook-download-mode"', 1)[0]
    group = before[before.rindex("<HintGroup"):]
    card = _read(_TABS + "library-card.tsx")
    return ("\n          hidden\n" in group
            and '<div className="form-group" hidden={hidden}>' in card)


def test_the_audiobook_mode_dropdown_is_hidden_too():
    assert _mode_group_is_hidden()


def test_the_old_audiobook_arrow_list_is_hidden():
    """kept, empty and hidden, in the React Audiobooks panel (step 8c)"""
    panel = _read(_TABS + "library-audiobooks.tsx")
    line = next(ln for ln in panel.splitlines() if 'id="audiobook-hybrid-rows"' in ln)
    assert "hidden" in line


def test_mode_is_still_persisted(model, js):
    """the select going must not stop download_source.mode being saved: the
    chain saves mode, the legacy primary and secondary, and the order, and the
    page save no longer sends any of them."""
    music = _body(model, "function writeMusic(")
    for key in ("mode,", "hybrid_primary: pair.primary", "hybrid_secondary: pair.secondary",
                "hybrid_order: order.slice()"):
        assert key in music, key
    body = js.split("async function saveSettings", 1)[1]
    # step 7g: every music download_source key is React now, so the page save
    # has no top-level download_source block at all (audiobooks keep theirs,
    # nested deeper)
    assert not re.search(r"^        download_source: \{", body, re.M)
    # and no music chain key anywhere in it
    code = re.sub(r"//[^\n]*", "", body)
    for key in ("hybrid_primary:", "hybrid_secondary:"):
        assert key not in code, key


def test_the_widget_drives_the_audiobook_transport(model, js):
    """REPLACED in step 7a, again in 8c: the audiobook chain is a settings key
    now, written through the store with the mode it implies; the bridge
    settings.js gave the chain is gone."""
    commit = _body(model, "function commit(order: string[])")
    assert "writeAudiobooks(order)" in commit
    write = _body(model, "function writeAudiobooks(")
    assert "'download_source.mode': order.length > 1 ? 'hybrid' : order[0] || 'torrent'" in write
    assert "setAudiobookChain" not in js


def test_the_dark_brand_marks_are_inverted(model):
    """Tidal, Qobuz and SoundCloud ship dark-foreground marks that vanish
    against the dark UI. `brightness(0) invert(1)` is this app's existing recipe
    for "render this image as pure white" — the equalizer and auto-sync icons
    already use it."""
    marks = model.split("export const INVERT_BRAND_MARKS = new Set([", 1)[1].split("]", 1)[0]
    for brand in ("tidal", "qobuz", "soundcloud"):
        assert f"'{brand}'" in marks, brand
    css = _read("webui/static/style.css")
    rule = _rule(css, ".dlchain-mark.is-inverted")
    assert "brightness(0) invert(1)" in rule


def test_both_the_pool_and_the_chain_invert(tab):
    """A logo that reads in one column and vanishes in the other is worse than
    either on its own."""
    assert "INVERT_BRAND_MARKS.has(id)" in tab.split("const markClass", 1)[1].split("\n\n", 1)[0]
    tile = _body(_chain(tab), "const tile = (id: string) =>")
    for name, fn in (("pool", tile), ("chain", _step(tab))):
        assert "markClass(id, 'dlchain-mark')" in fn, name


def test_a_step_says_which_source_it_is(tab):
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
    fn = _step(tab)
    assert "dlchain-step-name" in fn
    assert "dlchain-step-role" in fn
    assert "dlchain-step-art" in fn
    css = _read("webui/static/style.css")
    step = _rule(css, ".dlchain-step")
    assert "width: 100%" in step, "the row should fill its rail"
    cols = _rule(css, ".dlchain-columns")
    assert "1.55fr" in cols, "the pool, not the chain, should hold the extra width"


def test_the_name_still_reaches_a_pointer_and_a_reader(tab):
    """Dropping the visible text cannot mean dropping the information."""
    fn = _step(tab)
    assert "title={" in fn and "aria-label={" in fn
    assert "Tried first" in fn and "Fallback" in fn


def test_there_are_arrows_as_well_as_dragging(tab):
    """Dragging is precise work on a touchpad and impossible on a phone."""
    fn = _step(tab)
    assert "move(id, -1)" in fn and "move(id, 1)" in fn
    assert "dlchain-move" in fn
    move = _body(_chain(tab), "const move = (id: string, delta: number) =>")
    assert "commit(next)" in move


def test_the_end_arrows_are_disabled(tab):
    """An arrow that does nothing is worse than no arrow."""
    fn = _step(tab)
    assert "disabled={position === 1}" in fn
    assert "disabled={position === total}" in fn


def test_moving_past_either_end_is_a_no_op(tab):
    move = _body(_chain(tab), "const move = (id: string, delta: number) =>")
    assert "j < 0 || j >= next.length" in move


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


def test_the_behaviour_settings_are_their_own_group(index, tab, downloads):
    """REVERSED. These used to sit inside the pool's column, and this test
    asserted that position, because the column was empty below the tiles.

    Filling space is not a reason to group controls. Stream source, concurrency
    and the search timeout are general download settings with nothing to do with
    the chain, and putting them in the pool's column made the lower half of the
    panel read as a jumble. They are their own titled group below the chain now,
    and the pool is simply allowed to be as tall as its contents.

    The claim is position: after BOTH columns, not inside either one. both are
    React slots now (steps 5 and 7a), so the claim is the order of the slots,
    and that the chain widget closes both columns before the slot ends."""
    chain_at = index.index('data-stg-react="downloads" data-stg-part="chain"')
    group_at = index.index('data-stg-react="downloads" data-stg-part="behaviour"')
    assert group_at > chain_at, "the group is above the chain"
    chain = _chain(tab)
    assert chain.index('id="dlchain-pool"') < chain.index('id="dlchain-list"')
    assert "dlchain-behaviour" not in chain, "the group is tangled in the chain again"

    # the group is rendered through the shared Section, whose header is
    # followed by its body (the collapse handler walks header.nextElementSibling),
    # inside the #dlchain-behaviour wrapper
    behaviour = _body(downloads, "function Behaviour(")
    assert 'id="dlchain-behaviour"' in behaviour
    assert 'title="Download Behaviour"' in behaviour
    parts = _read(_TABS + "parts.tsx")
    header = parts.index('className="settings-section-header collapsed"')
    # step 8a: the body takes an extra class (bodyClass) when a section needs one
    body = parts.index("'settings-section-body collapsed'")
    assert header < body and parts.count("settings-section-header") == 1


def test_the_behaviour_group_is_music_only(downloads):
    """They are download_source.* - music-wide. Shown under a video or audiobook
    chain they would claim to apply there.

    It has to hide the WRAPPER, not the inner block: the group carries a
    "Download behaviour" heading now, and hiding only the fields would leave
    that heading floating above nothing on the other two tabs."""
    behaviour = _body(downloads, "function Behaviour(")
    wrapper = behaviour.split('<div id="dlchain-behaviour"', 1)[1].split(">", 1)[0]
    assert "hidden={kind !== 'music'}" in wrapper, "hides the inner block, orphaning the heading"


def test_moving_them_did_not_orphan_their_settings(js, index):
    """They are read by saveSettings by id, so the move is safe only as long as
    every id still exists — which is exactly the wipe this page already had."""
    # React renders them now (downloads-tab.tsx, SETTINGS_PORT.md step 5) and
    # saves only what changes, so a missing id can no longer wipe a value
    tab = (Path(__file__).resolve().parent.parent / "webui" / "src" / "features" / "settings"
           / "tabs" / "downloads-tab.tsx").read_text(encoding="utf-8")
    for el in ("stream-source", "max-concurrent-downloads", "source-search-timeout",
               "test-all-sources-btn"):
        assert f'id="{el}"' in tab, el


def test_grouping_can_never_lose_a_source(model, tab):
    """The pool is grouped now. A hard-coded group map plus eleven sources is a
    setup where adding a twelfth to HYBRID_SOURCES and forgetting DLCHAIN_GROUPS
    makes it silently unavailable - it would not appear in the pool, so it could
    never be added to a chain, and nothing would say why.

    The pool therefore ends with an "Other" group built from whatever is
    left over. This test pins that fallback, and separately checks that every id
    currently in HYBRID_SOURCES is actually placed, so the grouping stays
    deliberate rather than drifting into Other one source at a time.
    """
    chain = _chain(tab)
    assert "label: 'Other'" in chain, "no leftover group: a new source would vanish"
    assert "!seen.has(id)" in chain, "the Other group is not built from the leftovers"

    grouped = set(re.findall(r"'([a-z_]+)'", model.split("export const DLCHAIN_GROUPS", 1)[1].split("];", 1)[0]))
    listed = set(re.findall(r"id: '([a-z_]+)'", model.split("export const HYBRID_SOURCES", 1)[1].split("];", 1)[0]))
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


def test_the_filter_survives_a_rerender(tab):
    """Adding a source re-renders the pool. If the query lived in the input only,
    the freshly built tiles would come back unfiltered while the box still showed
    the text - so the query is component state the tiles are rendered from.
    """
    chain = _chain(tab)
    assert "const [query, setQuery] = useState('')" in chain
    tile = _body(chain, "const tile = (id: string) =>")
    assert "hidden={!hit(id)}" in tile, "the tiles are not filtered on render"
    hit = _body(chain, "const hit = (id: string) =>")
    assert "id.toLowerCase().includes(q)" in hit, "filter matches display names only"
    assert 'id="dlchain-no-match"' in chain, "a query matching nothing says nothing"


def test_a_hidden_form_group_is_actually_hidden(index):
    """The "Download Source" select used to be marked hidden in the markup and
    was on screen anyway: #settings-page .form-group sets display:flex, and a
    class rule with display outranks the browser's own [hidden] styling, so the
    attribute did nothing. Exactly the trap .hybrid-source-list hit on this same
    page. Any form-group the page hides is affected, so the guard stays though
    that select is gone (step 7a): the audiobook mode select is still one.
    """
    css = _read("webui/static/style.css")
    rule = _rule(css, "#settings-page .form-group[hidden]")
    assert "display: none" in rule and "!important" in rule

    # and the thing that made it visible is still the reason we need the guard
    base = _rule(css, "#settings-page .form-group")
    assert "display: flex" in base

    # a hidden form-group the page still has
    assert _mode_group_is_hidden()


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


def test_the_widget_is_shared_with_the_video_side(index, tab):
    """The whole point of building it this way. The video side hides music
    controls with

        body[data-side="video"] [data-music-only] { display: none !important }

    so a single data-music-only anywhere up the tree deletes this widget from
    the video side silently - it just would not be there, with nothing to say
    why. Pin that neither the widget, its section, nor the Sources tab sits
    under one. the widget and its section are React now: their slot is checked
    in index.html, and the markup the slot gets in sources-tab.tsx.
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

    for needle in ('data-stg-react="downloads" data-stg-part="chain"',
                   'data-stg-react="sources" data-stg-part="tiles"',
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

    # nothing the React chain or tiles render is music-only either
    assert "data-music-only" not in tab

    # and the tab buttons that reach them
    for name in ("downloads", "sources"):
        btn = index.split(f'data-tab="{name}"', 1)[0].rsplit("<button", 1)[1]
        assert "data-music-only" not in btn, f"the {name} tab button is music-only"


def test_the_chain_opens_on_the_side_you_are_standing_on(model):
    """the chain kind defaults to 'music', so a video user opening Downloads got
    the MUSIC chain and the music-only behaviour group beneath it - on a widget
    whose entire purpose is being shared by both sides.

    An explicit tab pick still wins; the side only supplies the default."""
    fn = _body(model, "function syncKindToSide()")
    assert "data-side" in fn and "video" in fn
    assert "if (model.getState().kindChosen) return;" in fn

    load = _body(model, "async function loadChain()")
    assert "syncKindToSide();" in load, "the default is never applied"

    switch = model.split("switchChain(kind) {", 1)[1].split("\n    },", 1)[0]
    assert "kindChosen: true" in switch, "a manual pick would be overridden"


def test_the_downloads_tab_is_shared_without_needing_a_marker():
    """REVERSED, and this is the good kind of reversal.

    video-side.css used to hide every music element on the downloads tab unless
    it carried data-shared. Nothing carried it, so the shared chain widget - the
    entire point - never appeared on the video side. The first fix was to add
    data-shared to three wrappers.

    The better fix is that the tab is simply shared. The blanket rule is gone and
    so is data-shared: a shared tab needs no marker announcing it, and a marker
    that must be remembered on every new wrapper is a bug waiting to be written
    again. The per-tab rules for the tabs that genuinely differ still stand."""
    css = _read("webui/static/video/video-side.css")
    # strip comments first: the note explaining why the rule was removed mentions
    # both strings, and matching your own explanation is not a test.
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for shared in ('downloads', 'sources', 'library', 'quality'):
        assert f'data-stg="{shared}"' not in rules, f"the {shared} tab is being gated again"
    assert "data-shared" not in rules
    # Connections is the last tab still genuinely side-specific. Library left
    # this list when it grew folders and templates of its own; Quality left it
    # when the gating turned out to be hiding two cards both called "Quality"
    # that could never appear on screen together.
    for tab in ("connections",):
        assert f'[data-stg="{tab}"]' in css, f"lost the {tab} rule"

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
    model = _read(_TABS + "sources-model.ts")
    css = _read("webui/static/style.css")

    hybrid = set(re.findall(r"id: '([a-z_]+)'", model.split("export const HYBRID_SOURCES", 1)[1].split("];", 1)[0]))
    extra = set(re.findall(r"id: '([a-z_]+)'", model.split("export const EXTRA_SOURCE_TILES", 1)[1].split("];", 1)[0]))
    video_spec = _kinds(model).split("\n  video: {", 1)[1].split("\n  },", 1)[0]
    video = set(re.findall(r"'([a-z]+)'", video_spec.split("sources: () => [", 1)[1].split("]", 1)[0]))
    assert video == {"soulseek", "torrent", "usenet", "extto"}
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

