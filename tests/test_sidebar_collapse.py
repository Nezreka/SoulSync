"""Collapsible sidebar (#1155, wishx).

    "The ability to collapse the sidebar down to just the icons (perhaps with a
    mouse-over with the name of the tab when collapsed) would save a lot of
    screen real estate."

One toggle, two states: full width, or just wide enough for the icons.

The two things that make this fiddly, both pinned below:

**The width has four consumers and two live outside the sidebar.** The audio
visualiser is ``position: fixed`` at the sidebar's right edge — deliberately
outside it, to escape the overflow clip — and the page-particles canvas offsets
from it with ``calc(100vw - …)``. Any of them left on a hardcoded 240px detaches
and floats in the middle of the page. They all read ``--sidebar-w`` now.

**The mode has to be on <html> before the stylesheet resolves.** Applied late,
the sidebar paints at 240px and snaps to its real width, jumping the whole page
on every navigation. Same reason the nav-section restore is inlined rather than
left to init.js, which says so in its own comment.

Section headers are the design question this feature poses: collapsed, they have
no room for text. A chevron you cannot read is a dead control — you would click
a nameless arrow, items would vanish, and nothing would tell you what you hid.
So collapsed turns each label into a divider and forces its items visible; the
saved per-section state stays in localStorage and returns on expand.
"""

import re

import pytest

CSS = 'webui/static/style.css'
HTML = 'webui/index.html'
JS = 'webui/static/init.js'
VIDEO_CSS = 'webui/static/video/video-side.css'

STORAGE_KEY = 'sidebarCollapsed'


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture(scope='module')
def css():
    return read(CSS)


@pytest.fixture(scope='module')
def html():
    return read(HTML)


@pytest.fixture(scope='module')
def js():
    return read(JS)


@pytest.fixture(scope='module')
def css_video():
    """video-side.css owns the Music/Video pill and loads AFTER style.css."""
    return read(VIDEO_CSS)


# ── the width is defined once ───────────────────────────────────────────────

def test_the_sidebar_width_is_a_single_variable(css):
    assert '--sidebar-w: 240px;' in css, "the default width has to live in one place"


def test_collapsed_is_narrow_enough_to_be_worth_it(css):
    width = int(re.search(r'html\[data-sidebar="collapsed"\]\s*\{[^}]*--sidebar-w:\s*(\d+)px', css).group(1))
    assert width <= 80, "icons-only should be icon width, not a slightly thinner sidebar"


def test_nothing_still_offsets_from_the_old_width(css):
    """The trap this feature is built on: elements that line up against the
    sidebar from OUTSIDE it, which fail silently — the visualiser floats
    mid-page, the particles canvas offsets from nothing, the bulk-actions bar
    leaves a gap. This test found ``.enhanced-bulk-bar`` while it was being
    written; the manual grep that preceded it had missed it.

    Only ``left:`` and ``calc(100vw - …)`` count. A bare ``width: 240px`` is
    usually something else — the discover hero is a 240px SQUARE image."""
    offenders = []
    for line_no, line in enumerate(css.splitlines(), 1):
        if re.search(r'(?:^|\s)left:\s*240px', line) or 'calc(100vw - 240px)' in line:
            offenders.append(f"{line_no}: {line.strip()}")
    assert offenders == [], f"still offsetting from a hardcoded sidebar width: {offenders}"


@pytest.mark.parametrize("selector", [
    '.sidebar {', '.sidebar-visualizer {', '#page-particles-canvas {',
])
def test_every_width_consumer_reads_the_variable(css, selector):
    block = css[css.index(selector):]
    block = block[:block.index('}')]
    assert 'var(--sidebar-w)' in block, f"{selector} must follow the sidebar width"


def test_no_sidebar_rule_pins_a_fixed_width(css):
    """The one that actually shipped broken. A desktop-only rule carried

        .sidebar { min-width: 240px; max-width: 240px; }

    and min-width pins a flex item regardless of what ``width`` says, so the
    toggle did visibly nothing — the state flipped, the class landed, and the
    sidebar sat there. Checking ``left:`` offsets missed it because this one is
    on the sidebar itself.

    Mobile is exempt: mobile.css owns the drawer below the breakpoint and sizes
    it independently."""
    offenders = []
    for match in re.finditer(r'(^|\n)\s*\.sidebar\s*\{([^}]*)\}', css):
        body = match.group(2)
        for prop in re.finditer(r'(min-width|max-width|width|flex-basis)\s*:\s*(\d+)px', body):
            offenders.append(f"{prop.group(1)}: {prop.group(2)}px")
    assert offenders == [], f".sidebar must size from --sidebar-w, found: {offenders}"


def test_nav_items_size_off_the_sidebar_rather_than_repeating_its_number(css):
    """.nav-button and .nav-section-label both sat on a hardcoded 216px — the
    kind of copy nobody thinks to update.

    Anchored to the rule itself: a bare substring search passed even with the
    rule reverted, because the mobile override further down restates the same
    calc(). Second time that exact weakness showed up today."""
    rule = css[css.index('html[data-sidebar] .nav-button,'):]
    rule = rule[:rule.index('}')]
    assert 'width: calc(var(--sidebar-w) - 24px);' in rule


# ── applied before paint ────────────────────────────────────────────────────

def test_the_state_is_applied_in_head_before_the_stylesheet(html):
    """Late application means a visible jump of the whole page on every load."""
    head = html[:html.index('</head>')]
    assert STORAGE_KEY in head, "the state must be read in <head>"
    assert "setAttribute('data-sidebar'" in head
    first_css = html.index('style.css')
    assert html.index(STORAGE_KEY) < first_css, "must run before the stylesheet resolves --sidebar-w"


def test_the_early_script_cannot_break_the_page(html):
    """It runs before everything, so a throw here takes the whole app down.
    localStorage is blocked outright in some embedded browsers."""
    head = html[:html.index('</head>')]
    block = head[head.index(STORAGE_KEY) - 400:head.index(STORAGE_KEY) + 400]
    assert 'try' in block and 'catch' in block


def test_both_scripts_agree_on_the_storage_key(html, js):
    """The <head> script writes nothing and the module writes everything — if
    they ever disagree on the key, the state silently stops persisting."""
    assert STORAGE_KEY in html and STORAGE_KEY in js


# ── the toggle ──────────────────────────────────────────────────────────────

def test_the_toggle_is_in_the_sidebar_not_buried_in_settings(html):
    header = html[html.index('class="sidebar-header"'):html.index('class="sidebar-scroll"')]
    assert 'sidebar-collapse-toggle' in header
    assert 'toggleSidebarCollapsed()' in header


def test_the_toggle_is_a_plain_two_state_flip(js):
    """Full width, or icon width. wishx also floated a middle 'compact' tier;
    deliberately not built — one button with one obvious effect."""
    assert 'function toggleSidebarCollapsed' in js
    body = js[js.index('function toggleSidebarCollapsed'):]
    body = body[:body.index('\n}')]
    assert '!isSidebarCollapsed()' in body, 'it should just flip, not walk a list of modes'


def test_expanded_carries_no_attribute(js):
    """One less state for the CSS to special-case: no attribute means the plain
    :root value applies."""
    assert "removeAttribute('data-sidebar')" in js


def test_the_state_survives_a_reload(js):
    assert f"localStorage.setItem(SIDEBAR_COLLAPSE_KEY, '1')" in js
    assert 'localStorage.removeItem(SIDEBAR_COLLAPSE_KEY)' in js


def test_a_blocked_localstorage_does_not_break_the_toggle(js):
    body = js[js.index('function setSidebarCollapsed'):js.index('function toggleSidebarCollapsed')]
    assert 'try {' in body and 'catch' in body


# ── collapsed: the design question ──────────────────────────────────────────

def test_collapsed_hides_the_text_and_keeps_the_icons(css):
    block = css[css.index('/* ── Collapsed: icons only ── */'):]
    block = block[:block.index('html[data-sidebar="collapsed"] .nav-button')]
    for hidden in ('.nav-text', '.sidebar-brand-text', '.nav-section-title',
                   '.nav-section-chevron', '.side-toggle-btn span', '.profile-indicator-name'):
        assert hidden in block, f"{hidden} has no room when collapsed"
    assert '.nav-icon' not in block, "the icons are the whole point"


def test_the_music_video_pill_slides_down_when_stacked(css_video):
    """The pill turns vertical at icon width, so its sliding thumb has to turn
    with it. The base rule sizes the thumb as a horizontal half — width:
    calc(50% - 4px) and translateX — which once stacked reads as a stray bar
    over the top button. Hiding it loses the active indicator instead, so it
    slides down."""
    block = css_video[css_video.index('html[data-sidebar="collapsed"] .side-toggle-thumb {'):]
    block = block[:block.index('}')]
    assert 'height: calc(50% - 3px);' in block, "the thumb must be a vertical half"
    assert 'width: auto;' in block, "the horizontal half-width has to be undone"
    assert 'translateY(100%)' in css_video, "video side must slide down, not across"
    assert 'html[data-sidebar="collapsed"] .side-toggle-thumb { display: none; }' not in css_video, \
        "hiding the thumb loses the active indicator"


def test_the_pill_geometry_stays_in_one_file(css, css_video):
    """The thumb only makes sense read with the track it slides in. Splitting
    them across stylesheets is how it ends up a stray bar."""
    assert '.side-toggle-thumb' not in css, "the pill's geometry belongs in video-side.css"


def test_nothing_in_the_header_is_wider_than_the_collapsed_sidebar(css, css_video):
    """Why the pill looked off-centre: the header's 24px side padding eats 48px
    of a 68px sidebar, leaving a 20px content box. Everything overflowed right
    and the header's own overflow:hidden clipped it — so it read as bad
    alignment rather than as not fitting."""
    width = int(re.search(r'html\[data-sidebar="collapsed"\]\s*\{[^}]*--sidebar-w:\s*(\d+)px', css).group(1))
    header = css[css.index('html[data-sidebar="collapsed"] .sidebar-header {'):]
    header = header[:header.index('}')]
    pad = int(re.search(r'padding:\s*\d+px\s+(\d+)px', header).group(1))
    box = width - pad * 2
    pill = int(re.search(r'html\[data-sidebar="collapsed"\] \.side-toggle \{[^}]*width:\s*(\d+)px',
                         css_video).group(1))
    assert pill <= box, f"the pill is {pill}px in a {box}px content box"
    assert 'align-items: center;' in header


def test_the_logo_and_toggle_stack_when_collapsed(css):
    """They do not fit on one line: a 38px logo, a 12px gap and a 26px button is
    76px, wider than the collapsed sidebar, so side by side they squash."""
    block = css[css.index('html[data-sidebar="collapsed"] .sidebar-brand {'):]
    block = block[:block.index('}')]
    assert 'flex-direction: column;' in block


@pytest.mark.parametrize("section", ['.support-section', '.version-section', '.status-section'])
def test_the_prose_footer_blocks_are_hidden_when_collapsed(css, section):
    """"Support SoulSync", the version string and the three service-status rows
    are all text with no icon that could stand in for them. At 68px they are
    unreadable clutter, so they go until you expand again."""
    block = css[css.index('html[data-sidebar="collapsed"] .support-section,'):]
    block = block[:block.index('}')]
    assert section in block


def test_the_footer_blocks_come_back_on_mobile(css):
    """mobile.css keeps the full-width drawer, so nothing there should vanish."""
    block = css[css.index('@media (max-width: 768px) {', css.index('Collapsible sidebar (#1155')):]
    block = block[:block.index('/* ══ Live Server Activity')]
    for section in ('.support-section', '.version-section', '.status-section'):
        assert section in block


def test_a_section_header_becomes_a_divider_not_a_nameless_chevron(css):
    block = css[css.index('html[data-sidebar="collapsed"] .nav-section-label {'):]
    block = block[:block.index('}')]
    assert 'height: 1px;' in block, "with no text to read it can only be a rule"
    assert 'pointer-events: none;' in block, "an unreadable control must not be clickable"


def test_collapsed_forces_every_item_visible(css):
    """A section the user collapsed earlier must not stay hidden behind a
    divider they cannot expand."""
    assert 'html[data-sidebar="collapsed"] .nav-item-hidden { display: flex !important; }' in css


def test_the_saved_section_state_is_not_destroyed(js):
    """Collapsed overrides the rendering only. Expanding must bring back exactly
    the sections the user had closed."""
    block = js[js.index('SIDEBAR_COLLAPSE_KEY ='):js.index('function initSidebarCollapse')]
    assert 'navSections' not in block, "collapsing must never touch the per-section state"


def test_icons_get_a_hover_label_when_collapsed(js):
    """wishx: "perhaps with a mouse-over with the name of the tab when
    collapsed". Without it a collapsed sidebar is a column of guesses."""
    body = js[js.index('function syncSidebarNavTitles'):js.index('function initSidebarCollapse')]
    assert "querySelector('.nav-text')" in body
    assert 'btn.title = label' in body


def test_the_hover_label_is_removed_when_expanded(js):
    """A title tooltip on a button whose text is already visible is noise."""
    body = js[js.index('function syncSidebarNavTitles'):js.index('function initSidebarCollapse')]
    assert "removeAttribute('title')" in body


# ── mobile keeps its own behaviour ──────────────────────────────────────────

def test_collapsing_stands_down_on_mobile(css):
    """mobile.css already owns the sidebar below 768px — a second set of width
    rules would fight it."""
    block = css[css.index('@media (max-width: 768px) {', css.index('Collapsible sidebar (#1155')):]
    block = block[:block.index('/* ══ Live Server Activity')]
    assert '--sidebar-w: 240px;' in block, "mobile keeps the full width"
    assert '.sidebar-collapse-toggle { display: none; }' in block, "no toggle on mobile"
    assert 'display: revert;' in block, "labels come back on mobile"
