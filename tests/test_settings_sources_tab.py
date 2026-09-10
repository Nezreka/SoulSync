"""The Sources tab: every download source configurable in one place.

The per-source config used to live inside the Downloads tab's "Source Settings"
section, hidden behind a cog on the hybrid-order widget, revealed one at a time.
Two problems with that, one cosmetic and one not:

  * You had to know the cog was there at all.
  * ``showCfg`` was ``activeSources.has(src) && ...``, so a source's settings only
    existed on screen if that source was ALREADY enabled. Setting up Tidal meant
    switching Tidal on first, saving a half-configured source into the chain, and
    only then being allowed to fill in the account details.

The ordering widget deliberately stays on Downloads; only the configuration moved.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

_SOURCE_CONTAINERS = (
    "soulseek-settings-container",
    "youtube-settings-container",
    "tidal-download-settings-container",
    "qobuz-settings-container",
    "hifi-download-settings-container",
    "deezer-download-settings-container",
    "amazon-download-settings-container",
    "soundcloud-download-settings-container",
    "lidarr-download-settings-container",
    # the real config moved here from the Downloads tab; these three are
    # shared with the video side, which downloads through them too
    "prowlarr-settings-container",
    "torrent-client-settings-container",
    "usenet-client-settings-container",
)


def _strip_comments(css: str) -> str:
    """CSS with the /* */ comments removed.

    These checks assert a declaration is absent, and the comment explaining WHY
    it is absent quotes the very string being looked for. Reading the comments
    as if they were rules makes the guard fail on its own explanation.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def index():
    return _read("webui/index.html")


@pytest.fixture(scope="module")
def js():
    return _read("webui/static/settings.js")


def test_the_tab_exists(index):
    assert 'data-tab="sources"' in index
    assert "switchSettingsTab('sources')" in index


def test_the_tab_is_available_on_both_sides(index):
    """It started music-only, which stopped being right the moment the shared
    config moved in: Prowlarr, the torrent client and the usenet client are the
    video side's entire acquisition stack. A music-only tab would have taken
    them away from it. Which TILES show is filtered per side instead.
    """
    line = next(ln for ln in index.splitlines() if 'data-tab="sources"' in ln)
    assert "data-music-only" not in line
    group = next(ln for ln in index.splitlines() if 'data-stg="sources"' in ln and "settings-group" in ln)
    assert "data-music-only" not in group


def test_the_sources_tab_shows_every_tile_on_both_sides(js):
    """REVERSED. This used to assert the opposite: the video side saw only a
    SHARED_SOURCES subset - 4 tiles out of 13 - on the theory that Tidal and
    Soulseek mean nothing to video.

    They mean plenty. This is a settings page. Hiding Tidal from someone who
    happens to be standing on the video side only means they cannot fix their
    Tidal credentials without switching sides first, and which side you are on
    is not a reason to be unable to see a setting. The tab is shared, so it
    renders the same thing on both sides - same tiles, same grouping.

    buildSourceTiles must therefore not branch on the side at all: not to filter
    the list, and not to group it differently either."""
    fn = js.split("function buildSourceTiles(", 1)[1].split("\nwindow.", 1)[0]
    assert "data-side" not in fn, "buildSourceTiles still branches on the side"
    assert "SHARED_SOURCES" not in fn
    assert "SHARED_SOURCES = new Set" not in js, "the dead subset is still declared"


def test_the_shared_tabs_carry_no_side_gating(index):
    """The Sources, Downloads and Library tabs are fully shared: identical
    content on both sides. The invariant that keeps them that way is that nothing on either tab
    carries data-music-only or data-video-only.

    This is the rule that was actually broken before. A blanket CSS rule hid
    every music element on the downloads tab unless it carried data-shared, and
    data-shared appeared exactly zero times in the markup - so the shared
    download-chain widget, the whole point of the exercise, was deleted from the
    video side and nobody could see why."""
    import re as _re

    # comments explaining the gating are not gating. without stripping them this
    # test fails on its own explanatory notes, which is a very silly way to fail.
    markup = _re.sub(r"<!--.*?-->", "", index, flags=_re.S)

    def tab_of(pos):
        at = markup.rfind('data-stg="', 0, pos)
        m = _re.search(r'data-stg="([a-z]+)"', markup[at:at + 32]) if at != -1 else None
        return m.group(1) if m else None

    offenders = []
    for m in _re.finditer(r'<[^>]*data-(?:music|video)-only[^>]*>', markup):
        own = _re.search(r'data-stg="([a-z]+)"', m.group(0))
        tab = own.group(1) if own else tab_of(m.start())
        if tab in ("sources", "downloads", "library"):
            offenders.append((tab, m.group(0)[:88]))
    assert not offenders, f"side gating on a shared tab: {offenders}"

    # and the escape hatch that existed only to punch through the old rule
    assert "data-shared" not in markup


def test_every_source_config_lives_on_the_sources_tab(index):
    """A group nested under another tab's body would stay hidden no matter what
    its own data-stg said — the parent is display:none."""
    body = index.split('data-stg="sources"', 1)[1].split("end Sources tab", 1)[0]
    for cid in _SOURCE_CONTAINERS:
        assert f'id="{cid}"' in body, f"{cid} did not move to the Sources tab"


def test_no_source_config_is_left_behind_on_downloads(index):
    # Exactly one home each; a duplicated id would break getElementById too.
    for cid in _SOURCE_CONTAINERS:
        assert index.count(f'id="{cid}"') == 1, f"{cid} appears more than once"


def test_a_source_no_longer_has_to_be_enabled_to_be_configured(js):
    """The regression that matters. If updateDownloadSourceUI goes back to
    driving these containers off the active-source set, configuring a source
    you have not switched on becomes impossible again."""
    fn = js.split("function updateDownloadSourceUI(", 1)[1].split("\n}", 1)[0]
    assert "showCfg" not in fn, "per-source config is gated on the source being active again"
    for cid in _SOURCE_CONTAINERS:
        assert f"getElementById('{cid}')" not in fn or "style.display" not in fn.split(cid, 1)[1][:120], \
            f"updateDownloadSourceUI still sets display on {cid}"


def test_the_chain_widget_stayed_on_downloads(index):
    """Only the config moved. The order lives with the rest of the download
    behaviour, and is the thing being redesigned separately."""
    assert 'id="hybrid-settings-container"' in index
    before = index.split('id="hybrid-settings-container"', 1)[0]
    # the nearest preceding tab marker is the Downloads one
    tags = re.findall(r'data-stg="([a-z]+)"', before)
    assert tags[-1] == "downloads", f"chain widget ended up under {tags[-1]}"


def test_the_cog_crosses_to_the_tab(js):
    fn = js.split("function toggleHybridSourceConfig(", 1)[1].split("\n}", 1)[0]
    assert "switchSettingsTab('sources')" in fn
    assert "openSourceModal(" in fn


def test_the_tiles_refresh_with_the_chain(js):
    # Same state drives both; a stale tile is a tile that lies about status.
    fn = js.split("function buildHybridSourceList(", 1)[1].split("\n}", 1)[0]
    assert "buildSourceTiles()" in fn


def test_every_source_has_a_tile(js):
    """Tiles are rendered, not hand-written, so the check is that the renderer
    covers every source that owns a config panel."""
    fn = js.split("function buildSourceTiles(", 1)[1].split("\nwindow.", 1)[0]
    assert "HYBRID_SOURCES" in fn
    assert "EXTRA_SOURCE_TILES" in fn      # Prowlarr is not a chain link
    assert "SOURCE_CONFIG_ID_BY_SRC" in fn
    markup = js.split("function _srcTileMarkup(", 1)[1].split("\n}", 1)[0]
    assert "src-tile-name" in markup and "src-tile-art" in markup


def test_the_panel_is_moved_not_cloned(js):
    """Cloning would put a second element with the same id in the document, and
    every getElementById in saveSettings would then read whichever one the
    browser returned first — silently saving the copy nobody typed into."""
    fn = js.split("function openSourceModal(", 1)[1].split("\nwindow.", 1)[0]
    assert "appendChild(panel)" in fn
    assert "cloneNode" not in fn


def test_closing_puts_the_panel_back(js):
    """It has to go home before the overlay hides, or the next open would move
    a node that is already inside the hidden modal."""
    fn = js.split("function closeSourceModal(", 1)[1].split("\nwindow.", 1)[0]
    assert "home.appendChild(panel)" in fn
    assert fn.index("home.appendChild(panel)") < fn.index("overlay.hidden = true")


def test_closing_lands_a_pending_autosave(js):
    """The page auto-saves 2s after a change. Edit a field, close the modal, and
    the timer would otherwise still be counting with nothing on screen to say
    anything was pending."""
    fn = js.split("function closeSourceModal(", 1)[1].split("\nwindow.", 1)[0]
    assert "settingsAutoSaveTimer" in fn and "saveSettings(" in fn


def test_escape_closes_it(js):
    assert "e.key === 'Escape'" in js and "closeSourceModal()" in js


def test_the_modal_is_a_dialog(index):
    block = index.split('id="source-config-modal"', 1)[1].split("</div>", 6)[0]
    assert 'role="dialog"' in block and 'aria-modal="true"' in block


def test_the_panels_park_in_a_hidden_home(index):
    """They are real page markup, not templates — they need somewhere to live
    that is not on screen when no modal is open."""
    assert 'id="source-config-home" hidden' in index
    home = index.split('id="source-config-home"', 1)[1].split("end source-config-home", 1)[0]
    for cid in _SOURCE_CONTAINERS:
        assert f'id="{cid}"' in home, f"{cid} is not parked in the home div"


def test_the_tiles_refresh_when_the_modal_closes(js):
    # Changing a setting can change "needs setup" or the chain chip.
    fn = js.split("function closeSourceModal(", 1)[1].split("\nwindow.", 1)[0]
    assert "buildSourceTiles()" in fn


def test_the_styling_reuses_the_pages_own_vocabulary():
    """Unified, not a second design language bolted on."""
    css = _read("webui/static/style.css")
    block = css.split("/* ── Sources tab", 1)[1]
    assert "--accent-rgb" in block
    for state in ("hss-ok", "hss-fail", "hss-testing"):
        assert f".src-tile-dot.{state}" in block


def test_the_dot_colours_match_the_chain_rows_exactly():
    # If these drift, the same source shows two different colours on two tabs.
    css = _read("webui/static/style.css")
    for state, colour in (("hss-ok", "#34d27b"), ("hss-fail", "#ff5f57")):
        chain = re.search(rf"\.hybrid-source-status\.{state}\s*{{([^}}]*)}}", css)
        tile = re.search(rf"\.src-tile-dot\.{state}\s*{{([^}}]*)}}", css)
        assert chain and tile
        assert colour in chain.group(1) and colour in tile.group(1)


def test_no_dead_width_rule_pretends_to_set_the_page_width():
    """The @media (min-width: 1440px) block used to cap .settings-content, and
    a cap there does nothing: the layout override further down sets
    `max-width: 100% !important` on that element and caps its CHILDREN instead.
    A rule that looks like it sets the width but cannot is worse than none —
    it is where I went first, and the page did not move."""
    css = _strip_comments(_read("webui/static/style.css"))
    big = css.split("@media (min-width: 1440px)", 1)[1]
    big = big[:big.index("\n}")]
    assert ".settings-content" not in big or "max-width" not in big


def test_the_tab_bar_cannot_hide_tabs():
    """It was width: fit-content with overflow-x: auto and the scrollbar hidden,
    so one tab too many and the extras were unreachable AND invisible."""
    css = _strip_comments(_read("webui/static/style.css"))
    bar = css.split(".stg-tabbar {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in bar
    assert "width: fit-content" not in bar


def test_the_columns_wrap_instead_of_overflowing():
    css = _strip_comments(_read("webui/static/style.css"))
    cols = css.split(".settings-columns {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in cols


# ---------------------------------------------------------------------------
# A working source must not be painted as a broken one
# ---------------------------------------------------------------------------

def test_broken_cookies_do_not_fail_a_reachable_youtube():
    """A missing cookie file drew a RED light on YouTube. YouTube serves
    anonymous requests perfectly well from most connections — the source
    downloads fine — so red said "this is broken, go fix it" about the one
    thing that was working."""
    src = _read("core/connection_test.py")
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    ok_path = branch.split("if run_async(yt.check_connection()):", 1)[1].split("# It genuinely", 1)[0]
    assert "return True" in ok_path
    assert "WARNING_MARKER" in ok_path


def test_an_unreachable_youtube_still_fails():
    src = _read("core/connection_test.py")
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    fail_path = branch.split("# It genuinely", 1)[1]
    assert "return False" in fail_path


def test_the_cookie_problem_is_named_on_both_paths():
    src = _read("core/connection_test.py")
    branch = src.split('elif service == "youtube":', 1)[1].split("elif service ==", 1)[0]
    assert branch.count("_problem") >= 3


def test_the_client_records_a_warning_state(js):
    assert "_ssLastTestWarned" in js
    fn = js.split("function _ssTestConn(", 1)[1].split("\n}", 1)[0]
    assert "startsWith" in fn


def test_a_warning_gets_its_own_dot_colour():
    css = _strip_comments(_read("webui/static/style.css"))
    # the bare declaration, not the :has() ring rule that mentions the same class
    warn = re.search(r"^\.src-tile-dot\.hss-warn\s*{([^}]*)}", css, re.M)
    assert warn, "no .src-tile-dot.hss-warn rule"
    assert "#f0b429" in warn.group(1)   # amber, not the red it used to draw
    assert "#ff5f57" not in warn.group(1)


def test_a_broken_source_is_visible_at_the_tile_not_just_the_dot():
    """The tile used to carry this on border-color. The visual pass dropped
    borders in favour of tonal surfaces, and this signal came within one edit of
    going with them - the dot tests below stayed green either way, because the
    dot is a separate element. So pin the tile-level ring, not the dot.

    The alert also has to outrank .src-tile.is-active, which sets a box-shadow of
    its own: a source that is IN the chain and failing is exactly the one you
    need to see, so ordering in the file is load-bearing here.
    """
    css = _strip_comments(_read("webui/static/style.css"))
    for state, colour in (("hss-fail", "255, 95, 87"), ("hss-warn", "240, 180, 41")):
        m = re.search(r"^\.src-tile:has\(\.src-tile-dot\.%s\)\s*{([^}]*)}" % state, css, re.M)
        assert m, "no tile-level ring for %s" % state
        assert "box-shadow" in m.group(1), "%s ring is not drawn" % state
        assert colour in m.group(1), "%s ring lost its colour" % state
        assert css.index(m.group(0)) > css.index(".src-tile.is-active {"), (
            "the %s ring must come after .src-tile.is-active or the chain "
            "gradient wins and a failing in-chain source looks healthy" % state
        )


def test_usenet_has_a_tile(js):
    """It is its own link in the chain and shares the Prowlarr panel with
    torrent. It had no entry at all, so a usenet user saw nothing to click."""
    m = js.split("const SOURCE_CONFIG_ID_BY_SRC = {", 1)[1].split("};", 1)[0]
    assert "usenet:" in m
    assert "torrent:" in m


def test_the_page_width_is_one_number():
    """The nav row and the columns have to agree or the tabs and the content
    below them line up differently."""
    css = _strip_comments(_read("webui/static/style.css"))
    assert "--settings-max-width" in css
    for sel in ("#settings-page .settings-nav-row {", "#settings-page .settings-columns {"):
        block = css.split(sel, 1)[1].split("}", 1)[0]
        assert "var(--settings-max-width)" in block, sel
        assert "920px" not in block


def test_sources_gets_the_full_width_like_logs():
    # A grid uses width by fitting more tiles; a form just stretches its inputs.
    css = _strip_comments(_read("webui/static/style.css"))
    assert 'data-stg="sources"' in css.split(":has(", 1)[1][:4000] or 'data-stg="sources"' in css


# ---------------------------------------------------------------------------
# Layout and the per-source test
# ---------------------------------------------------------------------------

def test_the_tiles_are_grouped_not_one_ragged_wrap(js):
    """Eleven tiles in a single auto-fill grid wrapped 7-then-4 and read as an
    accident. Two labelled groups make the same wrap look deliberate."""
    fn = js.split("function buildSourceTiles(", 1)[1].split("\nwindow.", 1)[0]
    assert "In your chain" in fn and "Available" in fn
    assert "src-group" in fn


def test_the_chain_group_is_in_chain_order(js):
    """Registry order put #4 before #2, which makes the numbers on the chips
    look wrong even though they are right."""
    fn = js.split("function buildSourceTiles(", 1)[1].split("\nwindow.", 1)[0]
    assert "order.indexOf(a.id) - order.indexOf(b.id)" in fn


def test_a_small_group_does_not_stretch_across_the_page():
    css = _strip_comments(_read("webui/static/style.css"))
    row = css.split(".src-tile-row {", 1)[1].split("}", 1)[0]
    assert "auto-fit" in row
    # a max on the track, or four tiles span the whole width of the page
    assert "190px" in row or "px)" in row.split("minmax(", 1)[1]


def test_there_is_a_per_source_test_button(index, js):
    """The summary toast gave a count and the dot gave a colour. Neither told
    you WHAT was wrong with a source you could see was unhappy."""
    assert 'id="src-modal-test"' in index
    assert 'id="src-modal-result"' in index
    assert "function testOneSource(" in js


def test_the_test_shows_what_the_server_said(js):
    fn = js.split("async function testOneSource(", 1)[1].split("\nwindow.", 1)[0]
    assert "_ssLastTestMessage[srcId]" in fn
    assert "out.textContent" in fn


def test_the_test_updates_the_dot_and_the_tiles(js):
    fn = js.split("async function testOneSource(", 1)[1].split("\nwindow.", 1)[0]
    assert "_hybridSourceStatus[srcId] = state" in fn
    assert "buildSourceTiles()" in fn


def test_a_source_with_no_probe_says_so_rather_than_failing(js):
    """YouTube used to be `() => Promise.resolve(true)`; a source with nothing
    to test must not read as a source that failed a test."""
    fn = js.split("async function testOneSource(", 1)[1].split("\nwindow.", 1)[0]
    assert "no connection test" in fn.lower()


def test_opening_a_source_carries_its_last_result_in(js):
    # An amber tile should still explain itself once you are inside.
    fn = js.split("function openSourceModal(", 1)[1].split("\nwindow.", 1)[0]
    assert "src-modal-result" in fn


def test_the_result_line_is_announced(index):
    block = index.split('id="src-modal-result"', 1)[0][-160:]
    assert 'role="status"' in index.split('id="src-modal-result"', 1)[1][:120] or 'role="status"' in block


# ---------------------------------------------------------------------------
# Consolidation: the source config that was scattered across Downloads/Advanced
# ---------------------------------------------------------------------------

_MOVED_IN = {
    "prowlarr-settings-container": "prowlarr-url",
    "torrent-client-settings-container": "torrent-client-type",
    "usenet-client-settings-container": "usenet-client-type",
}


def _panel_span(index: str, container_id: str) -> str:
    """Everything inside one panel, walking div depth.

    The obvious version — split on the next `<div id="` — stops at the panel's
    first nested element with an id, which is a couple of lines in. It reported
    the yt-dlp block as missing when it was there.
    """
    start = index.index(f'id="{container_id}"')
    i, depth, opened = index.rindex("<div", 0, start), 0, False
    while i < len(index):
        nxt_open = index.find("<div", i)
        nxt_close = index.find("</div>", i)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            opened = True
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
            if opened and depth == 0:
                return index[start:i]
    return index[start:]


@pytest.mark.parametrize("container,marker", sorted(_MOVED_IN.items()))
def test_the_scattered_config_moved_into_its_panel(index, container, marker):
    """Indexers, the torrent client and the usenet client each had their own
    collapsible section on the Downloads tab, while the Sources tab showed a
    tile that just said "configure it over there". The config is in the tile
    now."""
    assert f'id="{container}"' in index
    assert marker in _panel_span(index, container), f"{marker} did not move into {container}"


def test_the_old_downloads_tab_section_is_gone(index):
    # Two homes for one setting is how they drift apart.
    assert 'id="indexers-downloaders-section"' not in index
    assert 'id="prowlarr-source-redirect"' not in index


def test_nothing_still_points_at_the_removed_wrappers(js):
    """updateDownloadSourceUI used to show/hide those by id. Left in place they
    would be silent no-ops that read as working code."""
    for dead in ("indexers-downloaders-section", "prowlarr-source-redirect",
                 "torrent-tile", "usenet-tile"):
        assert dead not in js, dead


def test_the_status_dots_survived_the_move(index):
    """They lived in the section headers the modal replaced. _setIndStatusDot is
    null-safe, so losing them would not crash — the connection light would just
    stop working, which is worse than a crash."""
    for dot, container in (("prowlarr-status-dot", "prowlarr-settings-container"),
                           ("torrent-client-status-dot", "torrent-client-settings-container"),
                           ("usenet-client-status-dot", "usenet-client-settings-container")):
        assert f'id="{dot}"' in _panel_span(index, container), dot


def test_prowlarr_gets_a_tile_and_a_test(js):
    """It is not a link in the download chain, so it is not in HYBRID_SOURCES —
    but it is very much something you configure in order to download."""
    extra = js.split("const EXTRA_SOURCE_TILES = [", 1)[1].split("]", 1)[0]
    assert "'prowlarr'" in extra
    probes = js.split("const HYBRID_SOURCE_PROBE = {", 1)[1].split("};", 1)[0]
    assert "prowlarr:" in probes


def test_prowlarr_does_not_claim_a_chain_position(js):
    """order.indexOf('prowlarr') is always -1, so the generic chip would label
    the indexer "not in chain" — which reads as something being wrong."""
    fn = js.split("function _srcTileMarkup(", 1)[1].split("\n}", 1)[0]
    assert "src.id === 'prowlarr'" in fn
    assert "torrent &amp; usenet" in fn


def test_torrent_and_usenet_open_their_own_config(js):
    """Both used to open the same redirect panel that pointed elsewhere."""
    m = js.split("const SOURCE_CONFIG_ID_BY_SRC = {", 1)[1].split("};", 1)[0]
    assert "torrent: 'torrent-client-settings-container'" in m
    assert "usenet: 'usenet-client-settings-container'" in m
    assert "prowlarr-source-redirect" not in m


def test_ytdlp_moved_in_with_youtube(index):
    """It was under Advanced. yt-dlp going stale is the single most common cause
    of YouTube downloads failing, so it belongs where somebody debugging YouTube
    will look."""
    panel = _panel_span(index, "youtube-settings-container")
    assert "ytdlp-update-btn" in panel
    assert "ytdlp-installed" in panel
    assert 'id="ytdlp-hint-badge"' in panel      # the badge lived in the old header


def test_the_moved_ytdlp_block_left_the_advanced_tab(index):
    block = index.split('id="ytdlp-update-btn"', 1)[0][-2500:]
    assert 'data-stg="advanced"' not in block


def test_the_filesystem_note_is_still_reachable(index):
    """It explains that torrent/usenet write to their OWN folders, which is the
    single most common import failure. It belonged to the section that was
    dissolved, so it had to land somewhere deliberate."""
    assert "ind-hero-warning" in index
    sources = index.split('data-stg="sources"', 1)[1].split("end Sources tab", 1)[0]
    assert "ind-hero-warning" in sources


def test_indexers_are_their_own_group(js):
    """Not a source you pick — the catalogue the torrent and usenet links
    search. Left in "Available" it read as something you could add to the
    chain, which is not a thing Prowlarr can be."""
    fn = js.split("function buildSourceTiles(", 1)[1].split("\nwindow.", 1)[0]
    assert "section('Indexers'" in fn
    # one code path now, not two. this used to require 2 because the video side
    # rendered its own grouping; the tab is shared and renders once for both.
    assert fn.count("section('Indexers'") == 1, "the side branch is back"


def test_an_indexer_cannot_land_in_the_chain_groups(js):
    """The chain groups partition on order.includes(), and Prowlarr is never in
    the order — so without splitting it out first it falls into Available every
    time."""
    fn = js.split("function buildSourceTiles(", 1)[1].split("\nwindow.", 1)[0]
    assert "const sources = configurable.filter(src => !isIndexer(src))" in fn
    for group in ("const inChain = sources", "const available = sources"):
        assert group in fn, group


@pytest.mark.parametrize("container", sorted(_MOVED_IN))
def test_a_moved_panel_carries_no_stale_tab_attribute(index, container):
    """switchSettingsTab sets display:none on every [data-stg] whose value is
    not the active tab. The moved wrappers still said "downloads", so on the
    Sources tab the panel's own contents were hidden and the modal opened
    completely empty — which is exactly how it shipped and what Boulder hit on
    the very first click.

    The modal owns visibility now. A tab attribute inside a panel can only
    hide it.
    """
    panel = _panel_span(index, container)
    assert 'data-stg="downloads"' not in panel
    assert 'data-stg=' not in panel, "a tab attribute inside a panel can only hide it"


@pytest.mark.parametrize("container,marker", sorted(_MOVED_IN.items()))
def test_a_moved_panel_still_has_its_content(index, container, marker):
    # The empty-modal bug looked identical to the content not having moved.
    panel = _panel_span(index, container)
    assert len(panel) > 1000, f"{container} looks empty"
    assert marker in panel


def test_the_indexers_tile_can_actually_light_up(js):
    """It stayed grey however healthy Prowlarr was.

    The tile colour reads _hybridSourceStatus. testAllSources walks the download
    CHAIN, and Prowlarr is not a link in it — it is what the torrent and usenet
    links search through — so the loop never reached it. It was already being
    probed as a prerequisite for those two, and the result was thrown away.
    """
    fn = js.split("async function testAllSources(", 1)[1].split("\nwindow.", 1)[0]
    probe = fn.split("_hybridSourceStatus.prowlarr = 'testing'", 1)[1].split("}", 1)[0]
    assert "_ssTestConn('prowlarr')" in probe
    assert "_hybridSourceStatus.prowlarr = good" in probe


def test_the_indexer_probe_reaches_the_video_side(js):
    """`sources` is built from the MUSIC chain dropdown, so on the video side it
    says nothing about whether Prowlarr matters — and there it is the main path,
    not an extra."""
    fn = js.split("async function testAllSources(", 1)[1].split("\nwindow.", 1)[0]
    assert "_onVideoSide" in fn
    cond = next(ln for ln in fn.splitlines() if "sources.has('torrent')" in ln)
    assert "_onVideoSide" in cond


def test_the_indexer_result_redraws_its_tile(js):
    """The per-source loop redraws while walking the chain, which Prowlarr is
    not part of — so without this the dot would only appear on the next redraw
    triggered by something else."""
    fn = js.split("async function testAllSources(", 1)[1].split("\nwindow.", 1)[0]
    block = fn.split("_hybridSourceStatus.prowlarr = 'testing'", 1)[1].split("for (const id of sources)", 1)[0]
    assert "buildSourceTiles()" in block


def test_an_inverted_mark_is_not_then_dimmed_into_grey():
    """Tidal, Qobuz and SoundCloud ship as thin black OUTLINE svgs - stroke only,
    an 800px viewBox drawn at 30px. They are inverted to white so they are not
    invisible on a dark tile.

    The bug this pins: opacity MULTIPLIES down the tree. The mark carried 0.92 of
    its own while the idle rule put 0.55 on the parent, so a source that was not
    in the chain drew its white hairlines at 0.5 - which on a dark tile is grey,
    and looks exactly like the inversion was never applied at all. It was; it was
    being eaten one rule further up. Both halves have to stay put, which is why
    this checks the parent exemption and not just the filter.
    """
    css = _strip_comments(_read("webui/static/style.css"))

    for sel in (r"\.src-tile-art img\.is-inverted", r"\.dlchain-mark\.is-inverted"):
        m = re.search(r"^%s\s*{([^}]*)}" % sel, css, re.M)
        assert m, "no invert rule for %s" % sel
        assert "brightness(0) invert(1)" in m.group(1)
        op = re.search(r"opacity:\s*([\d.]+)", m.group(1))
        assert op and float(op.group(1)) == 1, (
            "an inverted mark must carry full opacity; anything less multiplies "
            "with the parent dimming and turns white line art grey"
        )

    # and the idle dimming has to let these through
    assert ".src-tile-art:has(img.is-inverted)" in css, (
        "no exemption from .src-tile:not(.is-active) dimming for line-art marks"
    )
    assert ".dlchain-tile-art:has(.dlchain-mark.is-inverted)" in css


def test_the_library_tab_speaks_one_card_language():
    """It used to be three loose blocks of video settings followed by seven
    music cards - and the music half was hidden on the video side entirely, so
    neither side ever saw the whole tab.

    Now every section is an expandable card and they are ordered by SUBJECT
    rather than by side: music paths beside video folders, music post-processing
    beside video organization (which is where video's post-processing lives).
    Grouping by which side a setting came from is an implementation detail
    leaking into the layout.
    """
    index = _read("webui/index.html")
    markup = re.sub(r"<!--.*?-->", "", index, flags=re.S)

    titles = []
    for m in re.finditer(r'<div class="settings-section-header[^"]*"[^>]*data-stg="library"[^>]*>', markup):
        t = re.search(r"<h3[^>]*>(.*?)</h3>", markup[m.end():m.end() + 400], re.S)
        if t:
            titles.append(re.sub(r"<[^>]+>", "", t.group(1)).strip())

    assert titles == [
        "Paths &amp; Organization", "Video Folders", "Post-Processing",
        "Video Organization", "Video Preferences", "Listening Stats",
        "Discovery", "Import", "Filtering", "Playlists",
    ], titles

    # no bare settings-group left stranded outside a card on this tab
    for m in re.finditer(r'<div class="settings-group"[^>]*data-stg="library"[^>]*>', markup):
        before = markup[:m.start()]
        assert before.rstrip().endswith('>'), "a library group is not inside a card"

    # every card's body must be its header's next sibling or the toggle misses it
    for m in re.finditer(r'<div class="settings-section-header[^"]*"[^>]*data-stg="library"[^>]*>', markup):
        depth = 0
        for mm in re.finditer(r"<(/?)div\b[^>]*?(/?)>", markup[m.start():]):
            if mm.group(2) == "/":
                continue
            depth += -1 if mm.group(1) else 1
            if depth == 0:
                end = m.start() + mm.end()
                break
        assert markup[end:].lstrip().startswith('<div class="settings-section-body'), (
            "a library card's body is not its header's next sibling"
        )


def test_no_settings_selector_is_defined_twice_at_the_top_level():
    """Two top-level rules for one selector is how a test ends up asserting
    against the rule the browser is ignoring. That happened twice in one day
    here: a first-in-chain rule and a section-body rule each had a second copy
    further down, and the guards written for them passed while the page did the
    opposite of what they claimed.

    Media-query overrides are exempt - repeating a selector inside a breakpoint
    is the whole point of one.
    """
    css = _read("webui/static/style.css")
    blanked = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), css, flags=re.S)

    media, seen, dupes = [], {}, []
    for m in re.finditer(r"@media[^{]*\{|([^{}]+)\{([^{}]*)\}|\}", blanked):
        tok = m.group(0)
        if tok.startswith("@media"):
            media.append(1)
            continue
        if tok == "}":
            if media:
                media.pop()
            continue
        if media:
            continue
        head = m.group(1)
        head = head[max(head.rfind("}"), head.rfind(";")) + 1:].strip()
        # only single-selector rules; a grouped selector legitimately repeats names
        if not head.startswith("#settings-page") or "," in head or "\n" in head:
            continue
        if head in seen:
            dupes.append(head)
        seen[head] = True

    # #settings-page itself is allowed to repeat: those blocks only declare
    # custom properties, which is how the token layers are kept separate.
    dupes = [d for d in dupes if d != "#settings-page"]
    assert not dupes, f"defined more than once at top level: {sorted(set(dupes))}"


def test_the_settings_page_sizes_text_from_one_scale():
    """Type on this page was in three unit systems at once: card titles in px,
    labels and sub-headings in em, help text in its own em. em compounds - a
    label two levels deep rendered smaller than the identical label one level
    up, for no reason anyone chose.

    One rem scale means a label is the same size wherever it sits. The five
    steps are declared as tokens; every text rule points at one.

    Two families are deliberately NOT on it, listed here so the next person
    knows it was a decision rather than an oversight:

      * the Connections tab's own components - .stg-service*, .stg-accordion*
        and .api-service-frame. That tab has its own layout language and has not
        been through this pass; resizing its text from here would leave it
        half-converted, which looks worse than leaving it alone.
      * the page title and the monospace callback URL, which are one-offs.

    Shrink that list as those tabs get done.
    """
    css = _read("webui/static/style.css")

    for token in ('--stg-t-section', '--stg-t-body', '--stg-t-label',
                  '--stg-t-sub', '--stg-t-micro'):
        assert f'{token}:' in css, f'{token} is not declared'

    leftovers = []
    for m in re.finditer(r'(?m)^(#settings-page [^{\n]+)\{([^}]*)\}', css):
        sel = m.group(1).strip()
        if any(x in sel for x in ('stg-service', 'stg-accordion', 'api-service-frame',
                                  'header-title', 'callback-url')):
            continue
        for size in re.findall(r'font-size:\s*([0-9.]+em)', m.group(2)):
            leftovers.append((sel[:60], size))
    assert not leftovers, f"still sizing text in em: {leftovers}"


def test_every_section_toggle_is_reachable_without_a_mouse(index, js):
    """31 collapsible sections were <div onclick>. Tab skipped all of them,
    Enter and Space did nothing, and the :focus-visible rules written for them
    could never fire - the element was not focusable, so there was no focus to
    style.

    They carry role="button" and tabindex="0" now, with a delegated keydown
    handler supplying the half a real <button> would have given for free.

    aria-expanded is checked separately and on purpose: an attribute that is set
    once in the markup and never updated is WORSE than no attribute, because a
    screen reader would confidently announce "collapsed" for a section the user
    just opened. So the handler has to write it back.
    """
    headers = re.findall(r'<div class="settings-section-header[^"]*"[^>]*>', index)
    assert headers, "no section headers found"

    for h in headers:
        if 'settings-section-static' in h:
            continue          # a heading that toggles nothing needs no button role
        assert 'tabindex="0"' in h, f"not focusable: {h[:90]}"
        assert 'role="button"' in h, f"no button role: {h[:90]}"
        assert 'aria-expanded' in h, f"no expanded state: {h[:90]}"

    # a collapsed section must not claim to be open
    for h in headers:
        if 'settings-section-static' in h or 'aria-expanded' not in h:
            continue
        collapsed = 'settings-section-header collapsed' in h
        expected = 'false' if collapsed else 'true'
        assert f'aria-expanded="{expected}"' in h, f"wrong initial state: {h[:90]}"

    assert "e.key !== 'Enter'" in js, "no keyboard activation"
    assert "setAttribute('aria-expanded'" in js, "aria-expanded is never updated"
    assert "e.preventDefault()" in js, "Space would scroll the page instead of toggling"

