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
    "prowlarr-source-redirect",
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


def test_the_tab_is_music_only(index):
    """The video side has its own source dropdown; these are music sources."""
    line = next(ln for ln in index.splitlines() if 'data-tab="sources"' in ln)
    assert "data-music-only" in line


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
    assert "HYBRID_SOURCES.filter" in fn
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
