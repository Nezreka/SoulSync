"""The video settings' Subtitles section (Phase 1 of the subtitle overhaul).

Covers the three Phase 1 controls plus the link to the existing
OpenSubtitles API key:

  * ``vo-subs-dl``      master toggle      -> ``download_subtitles``
  * ``vo-sub-langs``    languages input    -> ``subtitle_langs``
  * ``vo-provider-order`` read-only chain  -> ``subtitle_provider_order``

The toggle and langs input used to live loose inside the video
organization card; the section groups them with the provider chain.
The provider chain is deliberately read-only in Phase 1 (reordering
is a later phase), rendering the backend order with an
OpenSubtitles-only fallback.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

_PANEL_ID = 'id="organization-video-panel"'


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def index():
    return _read("webui/index.html")


@pytest.fixture(scope="module")
def video_js():
    return _read("webui/static/video/video-settings.js")


@pytest.fixture(scope="module")
def panel(index):
    """The video organization panel's HTML, where the section must live."""
    start = index.index(_PANEL_ID)
    # the panel is the last tabpanel before the Processing cardgroup
    end = index.index('data-stg="library"', index.index("Processing", start))
    return index[start:end]


def test_section_lives_in_the_video_panel(panel):
    assert "Subtitles" in panel
    for element_id in ("vo-subs-dl", "vo-sub-langs", "vo-provider-order"):
        assert f'id="{element_id}"' in panel, element_id


def test_ids_are_unique(index):
    for element_id in ("vo-subs-dl", "vo-sub-langs", "vo-provider-order",
                       "vo-sub-langs-error"):
        assert index.count(f'id="{element_id}"') == 1, element_id


def test_master_toggle_wired_to_download_subtitles(video_js):
    assert "chk('vo-subs-dl', _videoOrg.download_subtitles)" in video_js
    assert "download_subtitles: on('vo-subs-dl')" in video_js


def test_toggle_help_text_describes_phase1_semantics(panel):
    """On by default for new imports; best-effort; ~20/day quota; misses retried."""
    assert "On by default for new" in panel
    assert "~20" in panel
    assert "retried later" in panel


def test_langs_input_wired_to_subtitle_langs(video_js):
    assert "set('vo-sub-langs', _videoOrg.subtitle_langs" in video_js
    assert "subtitle_langs: val('vo-sub-langs')" in video_js


def test_langs_input_validates_lightly(index, video_js, panel):
    # 2-3 letter codes, same separators the backend parse_langs accepts
    assert r"/^[A-Za-z]{2,3}$/" in video_js
    assert 'id="vo-sub-langs-error"' in panel
    assert "2&ndash;3 letters" in panel
    assert "commas, spaces, or semicolons" in panel
    # invalid input reverts to the last saved value instead of saving
    assert "_voLangsLastGood" in video_js


def test_provider_chain_is_read_only_ordered_list(index, video_js, panel):
    assert '<ol class="vo-provider-order" id="vo-provider-order">' in panel
    # rendered from the backend order, OpenSubtitles-only fallback
    assert "_videoOrg.subtitle_provider_order" in video_js
    assert "DEFAULT_SUBTITLE_PROVIDER_ORDER = ['opensubtitles']" in video_js
    # posted back to the backend (GET serves it, POST persists it — the backend
    # normalizes it into the organization blob)
    assert "subtitle_provider_order: subtitleProviderOrder()" in video_js
    # no drag-reorder affordance in Phase 1
    assert "draggable" not in video_js.split("renderProviderOrder")[1].split(
        "function fillOrg")[0]
    assert "More providers are" in panel


def test_section_links_to_existing_opensubtitles_key(index, panel):
    # the API key field stays where it is; the section points at it
    assert 'id="opensubtitles-api-key"' in index
    assert 'href="#opensubtitles-api-key"' in panel


def test_sane_defaults(video_js):
    assert "'vo-sub-langs', _videoOrg.subtitle_langs || 'en'" in video_js
    assert "subtitleProviderOrder()" in video_js
