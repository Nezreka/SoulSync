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
                       "vo-sub-open-job", "vo-sub-job-status"):
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
    for value in ("off", "prefer_hi"):
        assert f'value="{value}"' in panel
    # forced is its own toggle now, never a preference
    assert 'value="prefer_forced"' not in panel


def test_forced_and_have_it_toggles_wired(panel, video_js):
    for element_id, key in (("vo-sub-forced", "subtitle_forced"),
                            ("vo-sub-use-embedded", "subtitle_use_embedded"),
                            ("vo-sub-ignore-pgs", "subtitle_ignore_pgs"),
                            ("vo-sub-ignore-vobsub", "subtitle_ignore_vobsub"),
                            ("vo-sub-ignore-ass", "subtitle_ignore_ass")):
        assert f'id="{element_id}"' in panel, element_id
        assert f"{key}: on('{element_id}')" in video_js, key


def test_every_subtitle_control_autosaves(video_js):
    # the hi preference and auto-sync used to have no change listener, so
    # flipping them never saved on its own
    block = video_js[video_js.index("'vo-sub-worker-interval', 'vo-sub-worker-batch'"):]
    block = block[:block.index("forEach")]
    for element_id in ("vo-sub-hi-forced", "vo-sub-autosync", "vo-sub-forced",
                       "vo-sub-use-embedded", "vo-sub-ignore-pgs",
                       "vo-sub-ignore-vobsub", "vo-sub-ignore-ass"):
        assert f"'{element_id}'" in block, element_id


def test_subdl_and_subsource_have_test_buttons(index):
    for svc in ("subdl", "subsource", "opensubtitles", "fanart"):
        assert f'data-video-test-service="{svc}"' in index, svc


def test_autosync_toggle_wired(panel, video_js):
    assert 'id="vo-sub-autosync"' in panel
    assert "chk('vo-sub-autosync', _videoOrg.subtitle_autosync)" in video_js
    assert "subtitle_autosync: on('vo-sub-autosync')" in video_js


def test_library_sweep_points_at_the_tools_job(panel, video_js):
    # the one-off backfill button became the Missing Subtitles maintenance job
    assert 'id="vo-sub-backfill"' not in panel
    assert "/api/video/subtitles/backfill" not in video_js
    assert 'id="vo-sub-open-job"' in panel
    assert "MISSING_SUBS_JOB = 'missing_subtitles'" in video_js
    assert "page: 'video-tools'" in video_js
    assert ".repair-job-card[data-job-id=" in video_js


def test_tools_page_knows_the_job():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "webui/static/video/video-repair.js").read_text(
        encoding="utf-8")
    assert "missing_subtitles: 'Missing Subtitles'" in js
    assert "missing_subtitles: 'Queue Subtitles'" in js
    assert "if (f.finding_type === 'missing_subtitles') return subtitlesDetailHTML(d);" in js


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
