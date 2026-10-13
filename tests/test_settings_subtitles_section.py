"""The video settings' Subtitles section (subtitle-finish redesign).

Covers the redesigned section: master toggle, languages input, the EDITABLE
provider chain (mirroring the download-chain widget: pool tiles + ordered
steps, drag + arrow buttons, Whisper pinned last), the HI/forced preference
and auto-sync controls, the advanced tuning knobs, and the API key fields
for the keyed providers.

  * ``vo-subs-dl``        master toggle      -> ``download_subtitles``
  * ``vo-sub-langs``      languages input    -> ``subtitle_langs``
  * ``subchain-widget``   editable chain     -> ``subtitle_provider_order``
  * ``vo-sub-hi-forced``  HI/forced pref     -> ``subtitle_hi_forced``
  * ``vo-sub-autosync``   auto-sync toggle   -> ``subtitle_autosync``
  * ``vo-sub-min-score`` etc. advanced knobs -> ``subtitle_*`` settings
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
    for element_id in ("vo-subs-dl", "vo-sub-langs", "subchain-widget",
                       "subchain-pool", "subchain-list",
                       "vo-sub-hi-forced", "vo-sub-autosync",
                       "vo-sub-backfill", "vo-sub-backfill-status"):
        assert f'id="{element_id}"' in panel, element_id


def test_ids_are_unique(index):
    for element_id in ("vo-subs-dl", "vo-sub-langs", "vo-sub-langs-error",
                       "subchain-widget", "subchain-pool", "subchain-list",
                       "vo-sub-hi-forced", "vo-sub-autosync",
                       "vo-sub-min-score", "vo-sub-daily-quota",
                       "vo-sub-upgrade-delta", "vo-sub-worker-interval",
                       "vo-sub-worker-batch"):
        assert index.count(f'id="{element_id}"') == 1, element_id


def test_master_toggle_wired_to_download_subtitles(video_js):
    assert "chk('vo-subs-dl', _videoOrg.download_subtitles)" in video_js
    assert "download_subtitles: on('vo-subs-dl')" in video_js


def test_toggle_help_text_describes_semantics(panel):
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


def test_provider_chain_is_editable(panel, video_js):
    # the download-chain widget structure, not a read-only list
    assert 'id="subchain-widget"' in panel
    assert 'id="subchain-pool"' in panel
    assert 'id="subchain-list"' in panel
    # drag AND arrow buttons (drag is impossible on phones)
    assert "draggable" in video_js
    assert "dlchain-move" in video_js
    # rendered from the backend order and posted back; the backend
    # normalizes via parse_provider_order
    assert "_subchainOrder" in video_js
    assert "subtitle_provider_order: _subchainOrder.slice()" in video_js


def test_chain_knows_all_providers(video_js):
    for pid in ("opensubtitles", "subdl", "subsource", "embedded", "whisper"):
        assert pid in video_js
    # Whisper is pinned last as the last resort, never reorderable
    assert "SUBCHAIN_BACKGROUND_IDS = ['whisper']" in video_js


def test_hi_forced_preference_wired(panel, video_js):
    assert 'id="vo-sub-hi-forced"' in panel
    assert "set('vo-sub-hi-forced', _videoOrg.subtitle_hi_forced || 'off')" in video_js
    assert "subtitle_hi_forced: val('vo-sub-hi-forced') || 'off'" in video_js
    for value in ("off", "prefer_hi", "prefer_forced"):
        assert f'value="{value}"' in panel


def test_autosync_toggle_wired(panel, video_js):
    assert 'id="vo-sub-autosync"' in panel
    assert "chk('vo-sub-autosync', _videoOrg.subtitle_autosync)" in video_js
    assert "subtitle_autosync: on('vo-sub-autosync')" in video_js


def test_backfill_button_wired(panel, video_js):
    assert 'id="vo-sub-backfill"' in panel
    assert "startBackfill" in video_js
    assert "/api/video/subtitles/backfill" in video_js


def test_advanced_knobs_wired(panel, video_js):
    for element_id, setting in (
            ("vo-sub-min-score", "subtitle_min_score"),
            ("vo-sub-daily-quota", "subtitle_daily_quota"),
            ("vo-sub-upgrade-delta", "subtitle_upgrade_min_delta"),
            ("vo-sub-worker-interval", "subtitle_worker_interval_min"),
            ("vo-sub-worker-batch", "subtitle_worker_batch")):
        assert f'id="{element_id}"' in panel, element_id
        assert f"{setting}:" in video_js, setting


def test_provider_api_keys_have_fields_and_endpoints(index, video_js):
    for element_id in ("opensubtitles-api-key", "subdl-api-key", "subsource-api-key"):
        assert f'id="{element_id}"' in index, element_id
    # keys save through the enrichment config endpoint
    assert "subdl_api_key:" in video_js
    assert "subsource_api_key:" in video_js
    # the section points at the Services keys
    assert "Services" in index


def test_sane_defaults(video_js):
    assert "'vo-sub-langs', _videoOrg.subtitle_langs || 'en'" in video_js
    assert "_subchainOrder" in video_js
