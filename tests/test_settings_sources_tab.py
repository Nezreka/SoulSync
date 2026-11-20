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

from tests.support.settings_page import index_with_settings_page

_ROOT = Path(__file__).resolve().parents[1]

_SOURCE_CONTAINERS = (
    "soulseek-settings-container",
    "youtube-settings-container",
    "tidal-download-settings-container",
    "qobuz-settings-container",
    "hifi-download-settings-container",
    "deezer-download-settings-container",
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
    # the Settings page is React (SETTINGS_PORT.md step V4c): read in its place
    return index_with_settings_page()


@pytest.fixture(scope="module")
def js():
    return _read("webui/src/features/settings/shell.ts")


# the tiles, the dots and Test all are React since step 7a (SETTINGS_PORT.md):
# sources-tab.tsx renders the tiles from sources-model.ts. the source modal and
# its panels are still vanilla, and reach the model through window.SoulSyncSources
_TABS = "webui/src/features/settings/tabs/"


@pytest.fixture(scope="module")
def model():
    return _read(_TABS + "sources-model.ts")


@pytest.fixture(scope="module")
def tab():
    return _read(_TABS + "sources-tab.tsx")


def _react_panels() -> str:
    """the panels ported to React (step 7c on), one component per panel: the
    first seven in source-panels.tsx, the last four in source-panels-more.tsx"""
    return _read(_TABS + "source-panels.tsx") + "\n" + _read(_TABS + "source-panels-more.tsx")


def _react_dispatch(react: str) -> str:
    """the two components that pick a slot's panels by part"""
    return "\n".join(react.split(f"export function {name}(", 1)[1].split("\nexport ", 1)[0]
                     for name in ("SourcePanels", "MoreSourcePanels"))


def _panel_text(index: str, container_id: str) -> str:
    """One panel's content, wherever it lives: its vanilla markup in index.html,
    or, once ported, the React component that renders that container."""
    if f'id="{container_id}"' in index:
        return _panel_span(index, container_id)
    react = _react_panels()
    at = react.index(f'id="{container_id}"')
    start = react.rindex("export function ", 0, at)
    end = react.find("\nexport ", at)
    return react[start:end if end != -1 else len(react)]


def _react_slots(index: str, region: str) -> list[str]:
    """the parts of the React panel slots inside a stretch of index.html"""
    return re.findall(r'data-stg-react="sources" data-stg-part="([a-z-]+)"', region)


def _modal(tab: str) -> str:
    """the React source modal (step 7b)"""
    return tab.split("export function SourceModal(", 1)[1].split("\nexport function", 1)[0]


def _tiles(tab: str) -> str:
    """the SourceTiles component"""
    return tab.split("export function SourceTiles(", 1)[1].split("export function SourcesTab(", 1)[0]


def _tile(tab: str) -> str:
    """one tile's markup"""
    return _tiles(tab).split("const tile = (src: SourceMeta) =>", 1)[1].split("const section =", 1)[0]


def _test_all(model: str) -> str:
    return model.split("async function testAll(", 1)[1].split("\n  return {", 1)[0]


def test_the_shared_tabs_carry_no_side_gating(index):
    """The Sources, Downloads, Library and Quality tabs are fully shared:
    identical content on both sides. The invariant that keeps them that way is that nothing on either tab
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
        if tab in ("sources", "downloads", "library", "quality"):
            offenders.append((tab, m.group(0)[:88]))
    assert not offenders, f"side gating on a shared tab: {offenders}"

    # and the escape hatch that existed only to punch through the old rule
    assert "data-shared" not in markup


def test_no_source_config_is_left_behind_on_downloads(index):
    # Exactly one home each; a duplicated id would break getElementById too.
    react = _react_panels()
    for cid in _SOURCE_CONTAINERS:
        count = index.count(f'id="{cid}"') + react.count(f'id="{cid}"')
        assert count == 1, f"{cid} appears {count} times"


def test_the_cog_crosses_to_the_tab(js):
    # the chain is React since step 7a and its rows have no cog: a source's
    # settings open from its tile, in the modal (sources-model.ts openModal).
    # the cog's crossing went with settings.js (step V4b): nothing called it
    model = _read("webui/src/features/settings/tabs/sources-model.ts")
    assert "/** open a source's settings in the modal (a tile, a chain cog) */" in model
    assert "toggleHybridSourceConfig" not in js


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


def test_the_client_records_a_warning_state(model):
    assert "warned:" in model
    fn = model.split("async function testConnection(", 1)[1].split("\n  }", 1)[0]
    assert "startsWith" in fn
    assert "warned: { ...s.warned, [service]:" in fn


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
    m = js.split("SOURCE_CONFIG_ID_BY_SRC: Record<string, string> = {", 1)[1].split("};", 1)[0]
    assert "usenet:" in m
    assert "torrent:" in m


def _max_widths(css: str, selector: str) -> list[str]:
    """Every max-width set on exactly this selector (ignoring :has() variants)."""
    pattern = r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}"
    return [m.strip() for body in re.findall(pattern, css)
            for m in re.findall(r"max-width:\s*([^;]+);", body)]


def test_the_page_width_is_one_number():
    """The nav row and the columns have to agree or the tabs and the content
    below them line up differently. They agree by both filling .settings-content,
    which is the one thing capped, by the one token.

    There used to be two numbers: style.css capped the nav row and columns at
    the token while settings-premier.css capped .settings-content at 1240px and
    lifted the columns' cap with max-width: none !important. The token was dead.
    """
    tokens = _strip_comments(_read("webui/static/tokens.css"))
    assert "--settings-max-width:" in tokens
    premier = _strip_comments(_read("webui/static/settings-premier.css"))
    assert _max_widths(premier, "#settings-page .settings-content") == [
        "var(--settings-max-width) !important"
    ]
    css = _strip_comments(_read("webui/static/style.css"))
    for sel in ("#settings-page .settings-nav-row", "#settings-page .settings-columns"):
        for source in (css, premier):
            assert not _max_widths(source, sel), f"{sel} carries a cap of its own"


def test_sources_gets_the_full_width_like_logs():
    # A grid uses width by fitting more tiles; a form just stretches its inputs.
    # Nothing caps the columns below .settings-content, so every tab, logs and
    # sources included, gets the full column.
    css = _strip_comments(_read("webui/static/style.css"))
    assert ".settings-columns:has(" not in css or "max-width" not in css.split(
        ".settings-columns:has(", 1)[1].split("}", 1)[0]


# ---------------------------------------------------------------------------
# Layout and the per-source test
# ---------------------------------------------------------------------------


def test_a_small_group_does_not_stretch_across_the_page():
    css = _strip_comments(_read("webui/static/style.css"))
    row = css.split(".src-tile-row {", 1)[1].split("}", 1)[0]
    assert "auto-fit" in row
    # a max on the track, or four tiles span the whole width of the page
    assert "190px" in row or "px)" in row.split("minmax(", 1)[1]


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
    now (a React panel since SETTINGS_PORT.md step 7g)."""
    assert f'id="{container}"' in index or f'id="{container}"' in _react_panels()
    assert marker in _panel_text(index, container), f"{marker} did not move into {container}"


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
        assert f'id="{dot}"' in _panel_text(index, container), dot


def test_prowlarr_gets_a_tile_and_a_test(model):
    """It is not a link in the download chain, so it is not in HYBRID_SOURCES —
    but it is very much something you configure in order to download."""
    extra = model.split("export const EXTRA_SOURCE_TILES", 1)[1].split("];", 1)[0]
    assert "'prowlarr'" in extra
    hybrid = model.split("export const HYBRID_SOURCES", 1)[1].split("];", 1)[0]
    assert "'prowlarr'" not in hybrid
    probes = model.split("const probes: Record<string, () => Promise<boolean>> = {", 1)[1].split("};", 1)[0]
    assert "prowlarr:" in probes


def test_torrent_and_usenet_open_their_own_config(js):
    """Both used to open the same redirect panel that pointed elsewhere."""
    m = js.split("SOURCE_CONFIG_ID_BY_SRC: Record<string, string> = {", 1)[1].split("};", 1)[0]
    assert "torrent: 'torrent-client-settings-container'" in m
    assert "usenet: 'usenet-client-settings-container'" in m
    assert "prowlarr-source-redirect" not in m


def test_ytdlp_moved_in_with_youtube(index):
    """It was under Advanced. yt-dlp going stale is the single most common cause
    of YouTube downloads failing, so it belongs where somebody debugging YouTube
    will look."""
    panel = _panel_text(index, "youtube-settings-container")
    assert "ytdlp-update-btn" in panel
    assert "ytdlp-installed" in panel
    assert 'id="ytdlp-hint-badge"' in panel      # the badge lived in the old header


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
    panel = _panel_text(index, container)
    assert 'data-stg="downloads"' not in panel
    assert 'data-stg=' not in panel, "a tab attribute inside a panel can only hide it"


@pytest.mark.parametrize("container,marker", sorted(_MOVED_IN.items()))
def test_a_moved_panel_still_has_its_content(index, container, marker):
    # The empty-modal bug looked identical to the content not having moved.
    panel = _panel_text(index, container)
    assert len(panel) > 1000, f"{container} looks empty"
    assert marker in panel


def test_the_indexers_tile_can_actually_light_up(model):
    """It stayed grey however healthy Prowlarr was.

    The tile colour reads the status in the model. Test all walks the download
    CHAINS, and Prowlarr is not a link in one — it is what the torrent and usenet
    links search through — so the loop never reached it. It was already being
    probed as a prerequisite for those two, and the result was thrown away.
    """
    fn = _test_all(model)
    probe = fn.split("setStatus('prowlarr', 'testing');", 1)[1].split("for (const id of sources)", 1)[0]
    assert "testConnection('prowlarr')" in probe
    assert "setStatus('prowlarr', result('prowlarr'," in probe


def test_the_indexer_probe_reaches_the_video_side(model):
    """`sources` is built from the MUSIC chain's mode, so on the video side it
    says nothing about whether Prowlarr matters — and there it is the main path,
    not an extra."""
    fn = _test_all(model)
    assert "const videoSide = document.body.getAttribute('data-side') === 'video'" in fn
    cond = next(ln for ln in fn.splitlines() if "sources.has('torrent')" in ln)
    assert "videoSide" in cond


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


def _library_cards(markup):
    """the Library tab's card titles and group labels in page order. the
    Folders and Organization cards are React (SETTINGS_PORT.md steps 8a, 8b):
    each slot stands in for its card, and the components are checked to
    render them (the Files & folders group label comes with Folders)."""
    tabs = "webui/src/features/settings/tabs/"
    shell = _read(tabs + "library-card.tsx")
    assert "export const LIBRARY_TAB = 'library';" in shell
    assert re.search(r'className="settings-section-header collapsed"\s+data-stg=\{LIBRARY_TAB\}', shell)
    assert "<h3>{title}</h3>" in shell
    folders = _read(tabs + "library-folders.tsx")
    assert '<LibrarySection title="Folders"' in folders
    assert re.search(r'className="stg-cardgroup" data-stg=\{TAB\}>\s*'
                     r'<span className="stg-cardgroup-label">Files &amp; folders</span>', folders)
    assert '<LibrarySection title="Organization"' in _read(tabs + "library-organization.tsx")
    slot = markup.index('<div data-stg-react="library" data-stg-part="folders"')
    org = markup.index('<div data-stg-react="library" data-stg-part="organization"')
    processing = _read(tabs + "library-processing.tsx")
    assert 'title="Post-Processing"' in processing and 'title="Import"' in processing
    assert processing.index('title="Post-Processing"') < processing.index('title="Import"')
    assert '<span className="stg-cardgroup-label">Processing</span>' in processing
    proc = markup.index('<div data-stg-react="library" data-stg-part="processing"')
    prefs = _read(tabs + "library-video-prefs.tsx")
    assert re.search(r'tab=\{TAB\}\s+title="Video Preferences"', prefs)
    assert '<span className="stg-cardgroup-label">Video</span>' in prefs
    vp = markup.index('<div data-stg-react="library" data-stg-part="video-prefs"')
    titles = [(slot, "Folders"), (org, "Organization"),
              (proc, "Post-Processing"), (proc + 1, "Import"), (vp, "Video Preferences")]
    groups = [(slot, "Files &amp; folders"), (proc, "Processing"), (vp, "Video")]
    for m in re.finditer(r'<div class="settings-section-header[^"]*"[^>]*data-stg="library"[^>]*>', markup):
        t = re.search(r"<h3[^>]*>(.*?)</h3>", markup[m.end():m.end() + 400], re.S)
        if t:
            titles.append((m.start(), re.sub(r"<[^>]+>", "", t.group(1)).strip()))
    for m in re.finditer(r'<div class="stg-cardgroup"[^>]*data-stg="library"[^>]*>', markup):
        groups.append((m.start(), re.search(r'<span class="stg-cardgroup-label">(.*?)</span>',
                                            markup[m.end():m.end() + 300]).group(1)))
    return [t for _, t in sorted(titles)], [g for _, g in sorted(groups)]


def test_the_library_tab_speaks_one_card_language():
    """It used to be three loose blocks of video settings followed by seven
    music cards, then five folded cards with two media tab strips. Now
    (settings-bic phase 4) it is pills by SUBJECT, each with a kind switch:
    music paths beside video folders under Folders, music post-processing
    beside the video sidecars under Tags and artwork. Grouping by which side
    a setting came from is an implementation detail leaking into the layout.
    """
    nav = _read("webui/src/features/settings/nav.ts")
    library = nav.split("key: 'library',", 1)[1].split("},\n  {", 1)[0]
    pills = re.findall(r"\{ key: '(\w+)', label: '([^']+)'", library)
    assert pills == [
        ("folders", "Folders"),
        ("naming", "Naming"),
        ("tags", "Tags and artwork"),
        ("import", "Import and files"),
        ("subtitles", "Subtitles"),
    ], pills
    # the subtitles are the video side's own pill
    assert "{ key: 'subtitles', label: 'Subtitles', side: 'video' }" in library
    # every pill but Subtitles has its music and video kinds side by side
    page = _read("webui/src/features/settings/tabs/library/library-page.tsx")
    pages = page.split("const PAGES", 1)[1].split("\n};", 1)[0]
    for pill in ("folders", "naming", "tags", "import"):
        block = pages.split(f"  {pill}: {{", 1)[1].split("\n  },", 1)[0]
        assert "music: {" in block and "video: {" in block, pill
    # no folded cards left on the tab
    for f in ("library-folders.tsx", "library-organization.tsx", "library-processing.tsx",
              "library-audiobooks.tsx", "library-video-folders.tsx",
              "library-video-organization.tsx"):
        src = _read(f"webui/src/features/settings/tabs/{f}")
        assert "settings-section-header" not in src, f
        assert "LibrarySection" not in src, f


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
    # every collapsible section is React now (SETTINGS_PORT.md): its header
    # is parts.tsx Section, with the three attributes (the Library cards'
    # LibrarySection went with settings-bic phase 4: Library is pills now)
    tabs = Path(__file__).resolve().parent.parent / "webui" / "src" / "features" / "settings" / "tabs"
    for f in ("parts.tsx",):
        src = (tabs / f).read_text(encoding="utf-8")
        head = src.split('className="settings-section-header collapsed"', 1)[1]
        head = head.split('<span className="settings-section-arrow"', 1)[0]
        assert 'role="button"' in head and "tabIndex={0}" in head and 'aria-expanded="false"' in head, f

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


def test_library_headers_report_what_is_set_not_what_is_inside(index, js):
    """Every library hint used to be a table-of-contents entry - "Metadata,
    tags, conversion, lyrics" tells you what the section CONTAINS and nothing
    about your install, so learning anything meant opening all ten cards. That
    is the problem the Sources tab had before its tiles started showing state,
    and showing state is what made that page work.

    Each wired header now reports its own current value.

    Two properties matter more than the wording:

      * a fallback. if a summary cannot be computed the header shows its static
        description again, so it degrades to the old behaviour rather than to a
        blank gap.
      * summaries are computed from the LIVE controls, never from a second copy
        of the state. a summary that can disagree with the field below it is a
        bug generator, and this page has already been bitten by exactly that
        kind of drift twice (the download-mode select, the slskd form).
    """
    slots = re.findall(r'<span[^>]*data-stg-summary="([^"]+)"[^>]*>', index)
    # the tabs ported to React render their headers themselves, from the same
    # state their controls render from, so the two cannot disagree either
    # (src/features/settings/tabs/parts.tsx Section, SETTINGS_PORT.md)
    tabs = Path(__file__).resolve().parent.parent / "webui" / "src" / "features" / "settings" / "tabs"
    react = "\n".join(p.read_text(encoding="utf-8") for p in tabs.glob("*-tab.tsx"))
    react_slots = re.findall(r'summaryKey="([^"]+)"', react)
    # (settings-bic phase 4) Library has no folded headers any more either:
    # each pill reads its own settings back in a sentence at the top, built
    # from the same store its fields show (tabs/library/words.ts)
    words = _read("webui/src/features/settings/tabs/library/words.ts")
    assert words.count("export function ") >= 15
    # (settings-bic) Downloads and Library have no folded headers any more:
    # their pills show everything, and their state is the sentence at the top
    assert len(slots) + len(react_slots) >= 3, f"only {len(slots) + len(react_slots)} headers report live values"
    assert not set(slots) & set(react_slots), "a summary is on the page twice"
    parts = (tabs / "parts.tsx").read_text(encoding="utf-8")
    assert "data-stg-fallback={hint}" in parts, "a React header without a fallback could go blank"
    for key in set(react_slots):
        block = react.split(f'summaryKey="{key}"', 1)[1][:200]
        assert "summary={" in block, f"React header {key} has no summary"

    for m in re.finditer(r'<span[^>]*data-stg-summary="[^"]+"[^>]*>', index):
        assert 'data-stg-fallback="' in m.group(0), (
            "no fallback - a header that cannot compute its summary would go blank"
        )

    # every wired key must have a matching computer, and vice versa
    computed = set(re.findall(r"^\s*'([a-z-]+)': \(\) =>", js, re.M))
    assert set(slots) <= computed, f"markup asks for summaries nothing computes: {set(slots) - computed}"
    assert computed <= set(slots), f"computed but never shown: {computed - set(slots)}"

    # and they read the real inputs rather than a cached copy of the settings
    # (every Library summary is React since SETTINGS_PORT.md video pass V2c,
    # checked above; the vanilla map is empty until settings.js goes)
    # (the vanilla map went with settings.js, step V4b)
    assert "LIBRARY_SUMMARIES" not in js and not computed


def test_a_failed_settings_load_does_not_summarise_an_empty_form(js):
    """If the fetch fails the form is blank, and a blank form summarised reads as
    "nothing enabled" - which is a confident lie about the user's config at the
    exact moment they should be told to reload.

    Every summary is React (parts.tsx Section, the Library cards): it shows
    only once the store has loaded settings, and the store is given them only
    on a good load, after the #879 guard (shell.ts, SETTINGS_PORT.md step V4b).
    """
    fn = js.split("export async function loadSettingsData()", 1)[1]
    good = fn.split("} catch (error) {", 1)[0]
    assert good.index("throw new Error(") < good.index("SoulSyncSettings?.hydrate(settings)")
    assert "hydrate" not in fn.split("} catch (error) {")[-1][:400]
    # a hero reads the settings back only after a load (Library, settings-bic
    # phase 4): before it, it says it is loading
    folders = _read("webui/src/features/settings/tabs/library-folders.tsx")
    assert "loads\n          ? musicFoldersSentence(" in folders
    assert "[{ text: 'Loading your folders.' }]" in folders


def test_the_two_sides_name_their_sections_the_same_way():
    """Folders and Naming are shared pills, each keeping both media kinds so
    neither music nor video settings disappear in consolidation; a pill opens
    on the side the page is on (settings-bic phase 4)."""
    page = _read("webui/src/features/settings/tabs/library/library-page.tsx")
    for component in ("MusicFolders", "VideoFoldersBody", "MusicNaming", "<VideoNaming />"):
        assert component in page, component
    assert "return kinds.includes(side) ? side : (kinds[0] ?? 'music');" in page


def test_a_settings_row_reads_left_to_right_the_same_way_everywhere(index):
    """One row grammar: what it is on the left, what it is set to on the right.

    Selects and text inputs already landed on the trailing edge. Checkbox rows
    did the opposite - the switch sat on the LEFT with its label trailing after
    it - so a card holding both kinds read as two layouts stacked together, with
    no single edge for the eye to run down.

    Fixed in CSS with row-reverse rather than by rewriting ~100 labels: the
    <input> is the first child, so reversing lays the text first and the control
    last. That also survives the two shapes the markup uses (bare text, and text
    wrapped in a span).
    """
    css = _read("webui/static/style.css")
    m = re.search(
        r'(?m)^#settings-page \.form-group > \.checkbox-label\s*\{([^}]*)\}', css)
    assert m, "no rule for the checkbox row"
    # strip comments FIRST: the note inside this very rule explains what
    # row-reverse does, so matching the raw body passes on the prose even when
    # the declaration has been deleted. this exact trap has now fooled three
    # separate guards in this file.
    rule = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    assert "flex-direction: row-reverse" in rule, "the switch is back on the leading edge"
    assert "justify-content: space-between" in rule


def test_no_checkbox_opts_out_of_the_switch(index):
    """178 checkboxes render as switches. Six carried inline width/height, which
    beats any stylesheet, so they rendered as small squares among switches - the
    kind of inconsistency that reads as a rendering bug rather than a choice.

    Inline sizing on a checkbox is always a mistake here: the size belongs to the
    control's styling, not to one instance of it.
    """
    offenders = re.findall(
        r'<input type="checkbox"[^>]*style="[^"]*(?:width|height)[^"]*"[^>]*>', index)
    assert not offenders, (
        f"{len(offenders)} checkbox(es) override the switch size inline: "
        f"{[o[:70] for o in offenders[:3]]}"
    )


def test_no_two_cards_share_a_name():
    """Two cards were both called "Quality" - one the music profile, one the
    video ladder. Nobody had noticed, because the tab hid the music half on the
    video side, so the pair could never appear on screen together. Hiding things
    does not remove a collision, it removes the evidence of one.

    They are Music Quality and Video Quality now, matching Music Folders /
    Video Folders. A duplicate title anywhere on the settings page means two
    sections are claiming the same subject.
    """
    index = index_with_settings_page()
    markup = re.sub(r"<!--.*?-->", "", index, flags=re.S)

    from collections import Counter
    titles = []
    for m in re.finditer(r'<div class="settings-section-header[^"]*"[^>]*>', markup):
        t = re.search(r"<h3[^>]*>(.*?)</h3>", markup[m.end():m.end() + 400], re.S)
        if t:
            titles.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t.group(1))).strip())

    # the tabs ported to React render their cards themselves (SETTINGS_PORT.md):
    # a Section's title, or a header written out by hand (Music Quality)
    tabs = Path(__file__).resolve().parent.parent / "webui/src/features/settings/tabs"
    # (and the video pass's sections, video-*.tsx)
    for tsx in sorted([*tabs.glob("*-tab.tsx"), *tabs.glob("video-*.tsx")]):
        if tsx.name.endswith(".test.tsx"):
            continue
        text = tsx.read_text(encoding="utf-8")
        titles += re.findall(r'<Section\b[^<]*?\btitle="([^"]+)"', text, re.S)
        for m in re.finditer(r'className="settings-section-header[^"]*"', text):
            t = re.search(r"<h3>([^<{]+)</h3>", text[m.end():m.end() + 900])
            if t:
                titles.append(re.sub(r"\s+", " ", t.group(1)).strip())

    dupes = [t for t, n in Counter(titles).items() if n > 1]
    assert not dupes, f"more than one card claims this title: {dupes}"

    # and the pair that caused it stays explicit about which side it serves:
    # (settings-bic phase 3) each is its own Quality pill, named for its kind
    nav = (Path(__file__).resolve().parent.parent / "webui/src/features/settings/nav.ts").read_text(
        encoding="utf-8")
    assert "{ key: 'music', label: 'Music' }" in nav
    assert "{ key: 'video', label: 'Movies and TV' }" in nav


# ── Connections: services as tiles ─────────────────────────────────────────
#
# the tiles, the Music / Video tabs and the modal are React since step 10a
# (SETTINGS_PORT.md): connections-services.tsx. the Metadata service cards are
# React too (connections-metadata.tsx), each a <ServiceCard service="...">


@pytest.fixture(scope="module")
def tiles():
    return _read(_TABS + "connections-services.tsx")


def _react_cards():
    """the service ids of the cards React renders (steps 10a and 10b)"""
    src = _read(_TABS + "connections-metadata.tsx") + _read(_TABS + "connections-extras.tsx")
    return re.findall(r'<ServiceCard\s+service="([a-z-]+)"', src)


def _fn(src, name):
    """a top-level function's text in a ts module"""
    start = src.index(f"function {name}(")
    return src[start:src.index("\n}\n", start)]


def _video_cards():
    """the React video cards' ids (SETTINGS_PORT.md video pass V3a), each a
    <Card service="..."> drawn by ServiceFrame kind="video", whose frame carries
    data-video-service (the services' attr for the video kind)"""
    cards = _read(_TABS + "connections-video-api.tsx")
    assert '<ServiceFrame\n      kind="video"' in cards
    services = _read(_TABS + "connections-services.tsx")
    assert "video: { label: 'Video', attr: 'data-video-service' }" in services
    assert "{...{ [attr]: service }}" in _fn(services, "ServiceFrame")
    return re.findall(r'<Card\s+service="([^"]+)"', cards)


def test_every_service_has_a_handle_a_tile_can_use(index):
    """22 services live in two "API Configuration" groups as nested accordions.
    The tiles are built by querying for those frames, so each one needs a stable
    attribute; a service without one is simply absent from the grid, with nothing
    to say why.

    The two sides use different attribute names - data-service for music,
    data-video-service for video - which is how the renderer knows which side a
    service belongs to without a second registry to keep in step.
    """
    music = re.findall(r'<div class="[^"]*api-service-frame[^"]*"[^>]*\bdata-service="([^"]+)"', index)
    music += _react_cards()
    video = re.findall(r'<div class="[^"]*api-service-frame[^"]*"[^>]*\bdata-video-service="([^"]+)"', index)
    # the video cards are React (connections-video-api.tsx, video pass V3a)
    video += _video_cards()
    assert len(music) >= 12, f"only {len(music)} music services carry a handle"
    assert len(video) >= 10, f"only {len(video)} video services carry a handle"
    assert not (set(music) & set(video)), "a service id is claimed by both sides"

    # every framed service on the connections tab should be reachable
    for m in re.finditer(r'<div class="[^"]*api-service-frame[^"]*stg-service[^"]*"([^>]*)>', index):
        attrs = m.group(1)
        assert 'data-service=' in attrs or 'data-video-service=' in attrs, (
            f"service frame with no handle - it will not appear as a tile: {attrs[:70]}"
        )


def test_opening_a_service_moves_the_panel_and_never_clones_it(tiles):
    """The forms are not rebuilt for the modal - the real panel is MOVED into it.

    That is not a style preference. Cloning would duplicate every field id in
    that panel, and on this page a duplicated id is how a save reads one element
    and writes another - which is exactly how live Prowlarr, torrent and usenet
    URLs got blanked. innerHTML anywhere near the panel would do the same.

    The panel also has to go home to the EXACT slot it came from, or the
    accordion groups silently reorder themselves as services get opened.
    """
    # (settings-bic) every card is React now: what moves is the card's
    # wrapper (React keeps hearing a portal wherever its wrapper sits), into
    # the sheet's body while open, and home in page order after
    assert 'cloneNode' not in tiles, "cloning would duplicate every id in the panel"
    assert 'innerHTML' not in tiles
    place = _fn(tiles, "useServicePlace")
    assert 'body.appendChild(wrap)' in place and 'placeHome(home, wrap)' in place
    home = _fn(tiles, "placeHome")
    assert "Number((a as HTMLElement).dataset.order) - Number((b as HTMLElement).dataset.order)" in home, (
        "the cards do not go back in page order"
    )
    assert "createPortal(" in _fn(tiles, "ServiceFrame")


def test_closing_a_service_lands_a_pending_key(tiles):
    """An API key typed seconds before closing sits on the 2s autosave timer. If
    the modal just closes, the field it belongs to has been moved out of view and
    nothing on screen says a save is still pending - and a reload loses it."""
    part = _fn(tiles, "ServicesPart")
    close_fn = part[part.index('function close()'):]
    # the page's own timer (window.landPendingSettingsSave) and the React store
    assert 'landPendingSettingsSave?.()' in close_fn
    assert 'settingsStore.flush()' in close_fn


def test_a_service_tile_reports_the_live_field_not_a_cached_setting(tiles):
    """Same rule as the library summaries: judge by the inputs on the page. A
    tile that reads a cached settings object can disagree with the field behind
    it, and "configured" is not a claim worth getting wrong about a credential.

    A service with nothing to judge by reports nothing rather than "not set up" -
    claiming a service is unconfigured because you cannot see its fields is worse
    than staying quiet.
    """
    fn = _fn(tiles, "serviceConfigured")
    assert 'querySelectorAll' in fn and 'settings' not in fn
    assert 'return null' in fn, "no 'cannot tell' state - it will guess instead"


def test_the_old_accordion_list_is_not_shown_alongside_the_tiles(index):
    """The tiles REPLACE the two "API Configuration" accordion groups; they do
    not sit above them. Leaving both on screen meant a video user saw a grid of
    music service tiles and, directly underneath, the old video accordion list -
    the same information twice in two different shapes.

    The groups stay in the DOM because they are where a service panel lives
    between visits to the modal: a tile lifts the panel out and puts it straight
    back. They are hidden on the GROUP, so a panel moved into the modal is no
    longer a descendant and renders normally.
    """
    # the music group is React (connections-extras.tsx, step 10b)
    # and so is the video group (connections-video-api.tsx, video pass V3a)
    extras = _read(_TABS + "connections-extras.tsx") + _read(_TABS + "connections-video-api.tsx")
    assert index.count('data-svc-legacy') + extras.count('data-svc-legacy=""') == 2, (
        "the legacy groups moved or multiplied"
    )

    css = _read("webui/static/style.css")
    rule = re.search(r'#settings-page \.settings-group\[data-svc-legacy\]\s*\{([^}]*)\}', css)
    assert rule, "the legacy accordion groups are not hidden"
    assert "display: none" in rule.group(1) and "!important" in rule.group(1)

    # the panels themselves must NOT be individually hidden, or lifting one into
    # the modal would show an empty dialog
    assert 'api-service-frame[data-svc-legacy]' not in css


def test_the_required_keys_note_survived_hiding_its_group(index, tiles):
    """TMDB and TVDB are required, and the note saying so - crucially including
    "both are free" - lived inside the group that is now hidden. The tiles say
    "Required: no key yet", but words on a tile cannot say why, and someone who
    assumes it is a paid feature just skips it and wonders why matching never
    works."""
    # the note is the Services part's (React), drawn on the Connections page
    assert 'className="svc-required-note"' in tiles and 'both keys are set. Both are free.' in tiles
    at = index.index('data-stg-react="connections" data-stg-part="page"')
    enclosing = index.rfind('data-svc-legacy', 0, at)
    # if the slot were inside the hidden group there would be no closing
    # </div> between that group's tag and the slot
    assert enclosing == -1 or index.count('</div>', enclosing, at) > 0, (
        "the required-keys note is inside the hidden group and will never be seen"
    )
    page = _read(_TABS + "connections/connections-page.tsx")
    assert "<ServicesPart " in page


def test_the_service_tabs_open_on_the_side_you_are_on(tiles):
    """Defaulting to music meant a video user landed on a grid of Spotify and
    Tidal tiles with their own TMDB key nowhere in sight. Same fix, and same
    reasoning, as the download chain's media tabs."""
    model = _fn(tiles, "createServicesModel")
    side = model[model.index('const sideKind'):model.index('return {')]
    assert 'data-side' in side and 'video' in side and 'state.chosen' in side
    assert "rebuild: () => set({ kind: sideKind()" in model, "the side default is never applied"
    assert "set({ kind, chosen: true" in model, "a manual pick would be overridden"


def test_every_mapped_service_logo_actually_exists(tiles):
    """A wrong path fails silently: the <img> 404s, onerror swaps in the
    monogram, and the card looks deliberate while quietly having lost its
    artwork. Nobody would ever notice from the page."""
    import os
    block = tiles.split('const SERVICE_LOGOS: Record<string, string> = {', 1)[1].split('};', 1)[0]
    pairs = re.findall(r"(\w+):\s*'([^']+)'", block)
    assert len(pairs) >= 13, f"only {len(pairs)} services mapped to artwork"
    missing = [(svc, path) for svc, path in pairs
               if not os.path.exists(str(_ROOT / 'webui') + path)]
    assert not missing, f"logo paths that do not exist: {missing}"


def test_a_service_without_artwork_still_looks_deliberate(tiles):
    """Nine services have no brand art in the repo. A grey blob for each would
    make the grid a list you have to read anyway, which defeats the point of a
    grid. (settings-bic) They get the parts' Mark: one line icon on their own
    brand colour, as DESIGN 4.2 says.

    The colour comes off the card (its dot) rather than out of a stylesheet map,
    so a service added later picks its hue up with no CSS change."""
    meta = _fn(tiles, "serviceMeta")
    # (settings-bic phase 3) the card's colour is its frame's data attribute
    assert "frame.dataset.svcColour" in meta
    assert "data-svc-colour={colour}" in _fn(tiles, "ServiceFrame")
    mark = _fn(tiles, "ServiceMark")
    assert "brand={meta.colour ? brandRgb(meta.colour) : undefined}" in mark, (
        "the brand colour never reaches the mark"
    )
    assert "src={meta.logo}" in mark
    # the parts' Mark draws the line icon on --brand when there is no image
    parts = _read("webui/src/features/settings/ui/parts.tsx")
    fn = parts.split("export function Mark(", 1)[1].split("\n}\n", 1)[0]
    assert "!image && s.markBrand" in fn and "'--brand': brand" in fn
    css = _read("webui/src/features/settings/ui/settings.module.css")
    brand = re.search(r'(?m)^\.markBrand\s*\{([^}]*)\}', css)
    assert brand and 'var(--brand' in brand.group(1), "the fallback is not brand-tinted"


def test_the_service_grid_only_shows_actual_services(tiles):
    """"Detail Pages" is a video-preferences panel on the LIBRARY tab that
    happens to use the same accordion component. An unscoped query pulled it into
    the Connections grid, where it appeared as a connection you could configure -
    which it is not.

    The grid is scoped to the connections tab, so a panel reusing this component
    elsewhere cannot wander in.
    """
    fn = _fn(tiles, "frames")
    assert 'closest(' in fn and 'data-stg="connections"' in fn, (
        "the service query is not scoped to the connections tab"
    )


def test_the_media_tab_owns_the_whole_connections_tab(index, tiles):
    """The Services tabs moved only the tile grid. The server sections below kept
    following body[data-side], so a music user clicking "Video" got video service
    tiles sitting directly above their own MUSIC server settings - the tab said
    one thing and the panel under it said another.

    (settings-bic) The media server is its own section above the services and
    follows the side the page is on; the Music/Video switch is the services'
    own, and the services heading says which side's they are. Both server
    sections stay marked with the side they belong to."""
    servers = _read(_TABS + "connections-servers.tsx")
    video = _read(_TABS + "connections-video-server.tsx")
    assert 'data-svc-side="music"' in servers and 'data-svc-side="video"' in video
    page = _read(_TABS + "connections/connections-page.tsx")
    assert '<div hidden={side === \'video\'} data-media-server="music">' in page
    assert '<div hidden={side !== \'video\'} data-media-server="video">' in page
    # (settings-bic phase 3) the video server is a card on the parts in its
    # own section: no old group to switch on with svc-side-active
    assert '<VideoServerConnection onView={() => setVideoSheet(true)} />' in page
    build = _fn(tiles, "ServicesPart")
    assert "title={state.kind === 'video' ? 'Video services' : 'Services'}" in build


def test_the_server_override_can_actually_win():
    """The rule it overrides lives in video-side.css, is loaded last, and carries
    !important. A plain rule in style.css could never beat that no matter how it
    was written - so the override lives in the same file, and uses an id so the
    specificity wins outright rather than by a class count that could shift.

    This is the exact trap that hid the download-chain widget from the video side
    for weeks: a rule in one file quietly losing to a rule in another.
    """
    video_css = _read("webui/static/video/video-side.css")
    rules = re.sub(r"/\*.*?\*/", "", video_css, flags=re.S)
    assert '.settings-group[data-svc-side].svc-side-active' in rules, (
        "the override is not in the file that loads last"
    )
    # Both halves need !important to beat the side-gating rule beside them.
    # Matched by their exact selectors rather than "first occurrence + N chars":
    # other rules use this selector now, and a positional slice silently starts
    # measuring one of those instead.
    hide = re.search(
        r'#settings-page \.settings-group\[data-svc-side\]\s*\{([^}]*)\}', rules)
    show = re.search(
        r'#settings-page \.settings-group\[data-svc-side\]\.svc-side-active\s*\{([^}]*)\}', rules)
    assert hide and 'display: none !important' in hide.group(1), (
        "the hide half does not win against the side-gating rule"
    )
    assert show and 'display: block !important' in show.group(1), (
        "the show half does not win, so the active tab's section stays hidden"
    )


def test_both_server_sections_use_the_same_copy_style(index):
    """The video server section's description used .callback-help - 10px,
    italic, off the type scale - while the music side's equivalent copy used
    setting-help-text. Italic body copy was removed page-wide earlier because it
    reads as a warning rather than as an explanation; this one had been missed
    because the two sections could never be seen at the same time.
    """
    # the video section is React (connections-video-server.tsx, video pass V3b)
    seg = _read(_TABS + "connections-video-server.tsx")
    assert 'data-svc-side="video"' in seg, "could not read the video server section"
    assert 'className="callback-help"' not in seg, (
        "the video server copy is back on the off-scale italic class"
    )
    # (settings-bic phase 3) both are the parts' Card with a one-line hint,
    # and the closing note is the card's quiet foot line
    servers = _read(_TABS + "connections-servers.tsx")
    assert 'title="Server Connections"' in seg and 'title="Server Connections"' in servers
    assert '<p className={s.cardFoot}>' in seg


def test_the_server_toggles_are_one_control_on_both_sides():
    """Same button, same metrics, same focus ring - they are the same control
    doing the same job, and the only reason they ever drifted is that the two
    sections were never on screen together to be compared."""
    css = _read("webui/static/style.css")
    rule = re.search(
        r'(?m)^#settings-page \.settings-group\[data-svc-side\] \.server-toggle-btn\s*\{([^}]*)\}', css)
    assert rule, "the shared toggle styling is gone"
    body = re.sub(r"/\*.*?\*/", "", rule.group(1), flags=re.S)
    assert "border-radius" in body and "transition" in body

    focus = re.search(
        r'(?m)^#settings-page \.settings-group\[data-svc-side\] \.server-toggle-btn:focus-visible\s*\{([^}]*)\}', css)
    assert focus and "outline" in focus.group(1), "no keyboard focus ring on the server toggles"


def test_the_music_tab_shows_every_music_server_from_either_side():
    """The video side downloads through Plex or Jellyfin only, so Navidrome and
    SoulSync's own server are hidden there. Correct for VIDEO settings - wrong
    for the Music tab, which is showing MUSIC settings and happens to be rendered
    while the user stands on the video side.

    The effect was quiet and bad: open your music settings from the video side
    and you were offered two of your four servers, with nothing saying the list
    had been trimmed. The tabs exist precisely so either side can manage the
    other's settings, so the hiding rule needs an exception for the active music
    section - the library pickers too, since those ARE the music ones.
    """
    css = _read("webui/static/video/video-side.css")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    for toggle in ('#navidrome-toggle', '#soulsync-toggle'):
        assert f'.settings-group[data-svc-side="music"].svc-side-active {toggle}' in rules, (
            f"{toggle} is still hidden on the Music tab when viewed from the video side"
        )
    for picker in ('#plex-library-selector-container',
                   '#jellyfin-library-selector-container',
                   '#navidrome-folder-selector-container'):
        assert f'.svc-side-active {picker}' in rules, (
            f"{picker} is a MUSIC library picker and is still hidden on the Music tab"
        )


def test_a_server_row_is_one_setting_per_row(index):
    """The video half packed two library selects and a caption into a single
    form-group. A form-group is a flex ROW, so all three rendered on one line and
    the caption came out looking like a heading.

    One setting per row, same as everywhere else on the page.
    """
    # React (connections-video-server.tsx, SETTINGS_PORT.md video pass V3b)
    src = _read(_TABS + "connections-video-server.tsx")
    # (settings-bic phase 3) each library is its own Row of the parts
    assert re.findall(r'<LibraryRow kind="(\w+)" />', src) == ["movies", "tv"]
    row = _fn(src, "LibraryRow")
    assert row.count("<Row ") == 2 and 'data-video-lib-select={kind}' in row

    # the caption is the card's foot line, not a row
    assert '<p className={s.cardFoot}>' in src


def test_services_without_art_reuse_the_app_s_own_glyphs(tiles):
    """Six video services have no brand art anywhere in the repo - OMDb,
    fanart.tv, OpenSubtitles, Trakt, MDBList, Community Data - and a grid of
    letter monograms among real logos reads as placeholders, not as icons.

    (settings-bic) DESIGN 4.2 Mark: where there is no logo file, one line icon
    on that service's own brand colour, never an emoji or a letter. The glyph
    map still says which services have an established mark elsewhere."""
    block = tiles.split('const SERVICE_GLYPHS: Record<string, string> = {', 1)[1].split('};', 1)[0]
    mapped = re.findall(r"^\s*'?([\w-]+)'?:", block, re.M)
    for svc in ('omdb', 'fanart', 'opensubtitles', 'trakt'):
        assert svc in mapped, f"{svc} lost its place in the glyph map"
    mark = _fn(tiles, "ServiceMark")
    # a line icon, never the emoji glyph itself, and never a monogram
    assert "icon={meta.glyph ? 'box' : 'link'}" in mark
    assert "meta.glyph}" not in mark and "monogram" not in mark


def test_a_caption_is_not_styled_as_a_heading():
    """<small> inside a settings row shared one rule with .settings-subheading -
    a heading class by its own comment - so every caption inherited heading
    styling and rendered as bold uppercase. They are all full sentences:

        "Select which music library to use (doesn't affect config file)"
        "Use 0.1 for up to 10 requests/second on your server"

    Uppercasing a sentence is shouting it. This is also what made the music and
    video server sections read differently for the same content: one had its
    caption in a <small> and the other did not.
    """
    css = _read("webui/static/style.css")

    cap = re.search(r'(?m)^\.form-group small,\s*\n\.settings-group small\s*\{([^}]*)\}', css)
    assert cap, "the caption rule is gone or merged back into a heading rule"
    body = cap.group(1)
    assert "text-transform: none" in body, "captions are being uppercased again"
    assert "font-weight: 400" in body, "captions are being bolded like a heading"

    # the heading class keeps its own treatment - it really is a heading
    head = re.search(r'(?m)^\.settings-subheading\s*\{([^}]*)\}', css)
    assert head and "text-transform: uppercase" in head.group(1)


def test_the_server_toggles_are_the_same_size_on_both_sides():
    """The video toggles were deliberately shrunk - a 32px logo cap - while the
    music ones let .server-logo fill at width:100%. Same control, same job, two
    sizes. Nobody compared them because the two sections could never be on
    screen at once; the media tabs mean they can be now."""
    video_css = _read("webui/static/video/video-side.css")
    rules = re.sub(r"/\*.*?\*/", "", video_css, flags=re.S)

    opt_out = re.search(
        r'#settings-page \.settings-group\[data-svc-side\] \.server-logo\s*\{([^}]*)\}', rules)
    assert opt_out, "the settings server sections do not opt out of the video shrink"
    body = opt_out.group(1)
    # both sides read ONE token rather than each hard-coding a size. the override
    # has to live in this file - it loads last and is the only thing that can beat
    # the [data-video-only] 32px cap - but the value belongs in style.css.
    assert 'var(--svc-server-logo-max' in body, (
        "the video override hard-codes a size instead of reading the shared token"
    )
    main_css = _read("webui/static/style.css")
    assert '--svc-server-logo-max:' in main_css, "the shared token is not declared"


def test_the_video_server_row_is_not_capped_narrower_than_music():
    """[data-video-only] caps the server toggle row at 420px and the config at
    560px. That was invisible while the two sections could never be compared -
    now the media tabs put them one click apart, and two buttons floating in a
    third of the width next to four that span it is the first thing you see.

    The caps stay for any other data-video-only use of these blocks; the settings
    server sections opt out.
    """
    css = _read("webui/static/video/video-side.css")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(
        r'#settings-page \.settings-group\[data-svc-side\] \.server-toggle-container,\s*'
        r'#settings-page \.settings-group\[data-svc-side\] \.server-config-container\s*\{([^}]*)\}',
        rules)
    assert m, "the settings server sections do not opt out of the video width caps"
    assert "max-width: none" in m.group(1)


def test_the_server_cards_have_a_ceiling():
    """.server-logo is width:100% and .server-toggle-btn is flex:1, so with
    nothing stopping them a four-server row grew ~450px-tall cards - a
    poster-sized logo to answer "which server". Both axes are capped now.

    The cap is a TOKEN read by two rules: the one in style.css for the music
    side, and the one in video-side.css that has to exist because that file loads
    last and is the only thing able to beat its own [data-video-only] caps. One
    value, two readers - not two opinions.
    """
    css = _read("webui/static/style.css")
    assert '--svc-server-logo-max:' in css and '--svc-server-btn-max:' in css

    btn = re.search(
        r'(?m)^#settings-page \.settings-group\[data-svc-side\] \.server-toggle-btn\s*\{([^}]*)\}', css)
    assert btn, "no shared rule for the server toggles"
    body = re.sub(r"/\*.*?\*/", "", btn.group(1), flags=re.S)
    assert 'max-width: var(--svc-server-btn-max' in body, "the button has no width ceiling"

    logo = re.search(
        r'(?m)^#settings-page \.settings-group\[data-svc-side\] \.server-logo\s*\{([^}]*)\}', css)
    assert logo and 'max-height: var(--svc-server-logo-max' in logo.group(1), (
        "the logo has no height ceiling - the card will grow to whatever the art is"
    )


def test_the_connections_tab_survives_a_phone():
    """The service tiles had narrow-screen rules; the server section had NONE.
    Four buttons capped at 176px in a flex row do not fit a 360px screen - the
    row either scrolled sideways or squeezed each logo into a sliver.

    (settings-bic) The pick-one server row and the tiles are the parts'
    (ui/settings.module.css): on a phone the servers go two to a row and the
    tiles one column, and a mark stays square on both axes."""
    css = _read("webui/src/features/settings/ui/settings.module.css")
    phone = css.split("@media (max-width: 768px)", 1)[1]
    servers = re.search(r'\.servers\s*\{([^}]*)\}', phone)
    assert servers and 'repeat(2, minmax(0, 1fr))' in servers.group(1), (
        "the server row does not go two to a line on a phone"
    )
    tiles = re.search(r'\.tiles\s*\{([^}]*)\}', phone)
    assert tiles, "the tiles have no phone rule"
    mark = re.search(r'(?m)^\.mark\s*\{([^}]*)\}', css)
    assert mark and 'width: 40px' in mark.group(1) and 'height: 40px' in mark.group(1), (
        "only one axis is set - the mark stops being square"
    )


def test_every_connections_service_sits_in_a_named_group(index, tiles):
    """The grid groups services by what they do (metadata, scrobbling, subtitles
    and so on). Anything unlisted falls into "Other" so it never vanishes, but a
    new service landing there means nobody decided where it belongs."""
    block = tiles.split('const SERVICE_GROUPS', 1)[1].split('\n};', 1)[0]
    lists = ' '.join(re.findall(r'ids:\s*\[([^\]]*)\]', block))
    grouped = set(re.findall(r"'([a-z]+)'", lists))
    # (settings-bic) every card is React: the page markup holds none of its own
    connections = index.split('data-stg="connections"', 1)[-1].split('data-stg="library"', 1)[0]
    ids = set(re.findall(r'data-(?:video-)?service="([a-z]+)"', connections)) | {
        i for i in _react_cards() + _video_cards() if re.fullmatch(r'[a-z]+', i)
    }
    assert len(ids) >= 20, f"only found {len(ids)} services, the page split is off"
    assert ids - grouped == set(), f"services with no group: {sorted(ids - grouped)}"

    assert "label: 'Other'" in _fn(tiles, "readGroups"), "an unlisted service would vanish from the grid"
