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
    assert "openSourceConfig(" in fn


def test_opening_a_card_by_hand_sets_both_halves(js):
    """The shared accordion onclick toggles the header AND the body. Clearing
    only one leaves the next click reading 'already open' and closing nothing."""
    fn = js.split("function openSourceConfig(", 1)[1].split("\n}", 1)[0]
    assert "body.classList.remove('collapsed')" in fn
    assert "header.classList.remove('collapsed')" in fn


def test_the_cards_are_built_from_the_shared_registry(js):
    """One list of names and icons for the chain rows and the cards, so the two
    views cannot end up disagreeing about what a source is called."""
    fn = js.split("function buildSourceConfigHeaders(", 1)[1].split("\nwindow.", 1)[0]
    assert "HYBRID_SOURCES" in fn
    assert "src.icon" in fn and "src.emoji" in fn


def test_the_cards_refresh_with_the_chain(js):
    # Same state drives both; a stale card is a card that lies about status.
    fn = js.split("function buildHybridSourceList(", 1)[1].split("\n}", 1)[0]
    assert "buildSourceConfigHeaders()" in fn


def test_every_card_has_a_source_id(index):
    """The id is what pairs a card with its registry entry; without it the card
    renders with no icon and no status."""
    headers = re.findall(r'<div class="src-card-header[^>]*>', index)
    assert len(headers) == len(_SOURCE_CONTAINERS)
    for h in headers:
        assert "data-source-id=" in h
        assert re.search(r'data-source-id="[a-z_]+"', h), h


def test_the_cards_are_keyboard_reachable(index):
    # They are divs, not buttons, so they have to say so themselves.
    headers = re.findall(r'<div class="src-card-header[^>]*>', index)
    for h in headers:
        assert 'role="button"' in h and 'tabindex="0"' in h


def test_the_styling_reuses_the_pages_own_vocabulary():
    """Unified, not a second design language bolted on. The cards borrow the
    accent token and the same status-dot colours the chain rows use."""
    css = _read("webui/static/style.css")
    block = css.split("/* ── Sources tab", 1)[1]
    assert "--accent-rgb" in block
    for state in ("hss-ok", "hss-fail", "hss-testing"):
        assert f".src-card-dot.{state}" in block


def test_the_dot_colours_match_the_chain_rows_exactly():
    # If these drift, the same source shows two different colours on two tabs.
    css = _read("webui/static/style.css")
    for state, colour in (("hss-ok", "#34d27b"), ("hss-fail", "#ff5f57")):
        chain = re.search(rf"\.hybrid-source-status\.{state}\s*{{([^}}]*)}}", css)
        card = re.search(rf"\.src-card-dot\.{state}\s*{{([^}}]*)}}", css)
        assert chain and card
        assert colour in chain.group(1) and colour in card.group(1)


def test_the_auth_probes_moved_to_card_open(js):
    """They used to run from updateDownloadSourceUI, gated on the same
    active-source test. Deleting that gate without moving them left showCfg
    referenced but undefined — a ReferenceError that would have taken the whole
    of updateDownloadSourceUI down on every settings redraw."""
    assert "showCfg" not in js, "showCfg is referenced but no longer defined"
    fn = js.split("function toggleSourceCard(", 1)[1].split("\n}", 1)[0]
    assert "probeSourceOnOpen" in fn


def test_a_failing_probe_does_not_block_the_card(js):
    fn = js.split("function probeSourceOnOpen(", 1)[1].split("\n}", 1)[0]
    assert "try {" in fn and "catch" in fn


def test_the_deep_link_probes_too(js):
    # Arriving via the cog should behave the same as clicking the card.
    fn = js.split("function openSourceConfig(", 1)[1].split("\nwindow.", 1)[0]
    assert "probeSourceOnOpen" in fn
