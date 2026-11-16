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

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# the panel is React (SETTINGS_PORT.md video pass V2b): its markup is
# library-video-organization.tsx, its settings video-organization.ts
_TABS = "webui/src/features/settings/tabs/"


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def index():
    return _read("webui/index.html")


@pytest.fixture(scope="module")
def video_js():
    # the API keys are React too (SETTINGS_PORT.md video pass V3a)
    return (_read(_TABS + "video-keys.ts")
            + _read(_TABS + "connections-video-api.tsx")
            + _read(_TABS + "video-organization.ts")
            + _read(_TABS + "library-video-organization.tsx"))


@pytest.fixture(scope="module")
def org_src():
    return _read(_TABS + "video-organization.ts")


@pytest.fixture(scope="module")
def panel():
    """The video organization panel's markup, where the section must live."""
    return _read(_TABS + "library-video-organization.tsx")


def _bound(panel, element_id, key, kind="Check"):
    """a control of the panel, bound to a key of the settings (and so saved
    when it changes: Check and Select save, TextField when left)"""
    return re.search(rf'<{kind}\s+id="{element_id}"\s+field="{key}"', panel) is not None


def test_section_lives_in_the_video_panel(panel):
    assert "Subtitles" in panel
    for element_id in ("vo-subs-dl", "vo-sub-langs", "subchain-widget",
                       "subchain-pool", "subchain-list",
                       "vo-sub-hi-forced", "vo-sub-autosync",
                       "vo-sub-open-job", "vo-sub-job-status"):
        assert f'id="{element_id}"' in panel, element_id


def test_ids_are_unique(panel):
    for element_id in ("vo-subs-dl", "vo-sub-langs", "vo-sub-langs-error",
                       "subchain-widget", "subchain-pool", "subchain-list",
                       "vo-sub-hi-forced", "vo-sub-autosync",
                       "vo-sub-min-score", "vo-sub-daily-quota",
                       "vo-sub-upgrade-delta", "vo-sub-worker-interval",
                       "vo-sub-worker-batch"):
        assert panel.count(f'id="{element_id}"') == 1, element_id


def test_master_toggle_wired_to_download_subtitles(panel, org_src):
    assert "download_subtitles: !!o.download_subtitles" in org_src
    assert "download_subtitles: b('download_subtitles')" in org_src
    assert _bound(panel, "vo-subs-dl", "download_subtitles")


def test_toggle_help_text_describes_semantics(panel):
    """On by default for new imports; best-effort; ~20/day quota; misses retried."""
    assert "On by default for new" in panel
    assert "~20" in panel
    assert "retried later" in panel


def test_langs_input_wired_to_subtitle_langs(panel, org_src):
    assert "subtitle_langs: text(o.subtitle_langs" in org_src
    assert "subtitle_langs: s('subtitle_langs')" in org_src
    assert _bound(panel, "vo-sub-langs", "subtitle_langs", "TextField")


def test_langs_input_validates_lightly(index, video_js, panel):
    # 2-3 letter codes, same separators the backend parse_langs accepts
    assert r"/^[A-Za-z]{2,3}$/" in video_js
    assert 'id="vo-sub-langs-error"' in panel
    assert "2&ndash;3 letters" in panel
    assert "commas, spaces, or semicolons" in panel
    # invalid input reverts to the last saved value instead of saving
    assert "form: { ...state.form, subtitle_langs: langsGood }" in video_js


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
    assert "subtitle_provider_order: order.slice()" in video_js


def test_chain_knows_all_providers(video_js):
    for pid in ("opensubtitles", "subdl", "subsource", "embedded", "whisper"):
        assert pid in video_js
    # Whisper is pinned last as the last resort, never reorderable
    assert "SUBCHAIN_BACKGROUND_IDS = ['whisper']" in video_js


def test_hi_forced_preference_wired(panel, org_src):
    assert _bound(panel, "vo-sub-hi-forced", "subtitle_hi_forced", "Select")
    assert "subtitle_hi_forced: text(o.subtitle_hi_forced, 'off')" in org_src
    assert "subtitle_hi_forced: s('subtitle_hi_forced') || 'off'" in org_src
    for value in ("off", "prefer_hi"):
        assert f'value="{value}"' in panel
    # forced is its own toggle now, never a preference
    assert 'value="prefer_forced"' not in panel


def test_forced_and_have_it_toggles_wired(panel, org_src):
    for element_id, key in (("vo-sub-forced", "subtitle_forced"),
                            ("vo-sub-use-embedded", "subtitle_use_embedded"),
                            ("vo-sub-ignore-pgs", "subtitle_ignore_pgs"),
                            ("vo-sub-ignore-vobsub", "subtitle_ignore_vobsub"),
                            ("vo-sub-ignore-ass", "subtitle_ignore_ass")):
        assert _bound(panel, element_id, key), element_id
        assert f"{key}: b('{key}')" in org_src, key


def test_every_subtitle_control_autosaves(panel):
    # the hi preference and auto-sync used to have no change listener, so
    # flipping them never saved on its own: a Check or Select saves on change
    assert "onChange={(event) => save(field, event.target.checked)}" in panel
    assert "onChange={(event) => save(field, event.target.value)}" in panel
    for element_id, kind in (("vo-sub-hi-forced", "Select"), ("vo-sub-autosync", "Check"),
                             ("vo-sub-forced", "Check"), ("vo-sub-use-embedded", "Check"),
                             ("vo-sub-ignore-pgs", "Check"), ("vo-sub-ignore-vobsub", "Check"),
                             ("vo-sub-ignore-ass", "Check")):
        assert re.search(rf'<{kind}\s+id="{element_id}"', panel), element_id
    # and the knobs save when left
    for element_id in ("vo-sub-min-score", "vo-sub-daily-quota", "vo-sub-upgrade-delta",
                       "vo-sub-worker-interval", "vo-sub-worker-batch"):
        assert f"'{element_id}': true," in panel, element_id


def _video_cards():
    # the video API cards are React (SETTINGS_PORT.md video pass V3a)
    return _read(_TABS + "connections-video-api.tsx") + _read(_TABS + "video-keys.ts")


def test_subdl_and_subsource_have_test_buttons():
    cards = _video_cards()
    for svc in ("subdl", "subsource", "opensubtitles", "fanart"):
        assert f'<Test spec="{svc}">' in cards, svc


def test_autosync_toggle_wired(panel, org_src):
    assert _bound(panel, "vo-sub-autosync", "subtitle_autosync")
    assert "subtitle_autosync: !!o.subtitle_autosync" in org_src
    assert "subtitle_autosync: b('subtitle_autosync')" in org_src


def test_library_sweep_points_at_the_tools_job(panel, video_js):
    # the one-off backfill button became the Missing Subtitles maintenance job
    assert 'id="vo-sub-backfill"' not in panel
    assert "/api/video/subtitles/backfill" not in video_js
    assert 'id="vo-sub-open-job"' in panel
    assert "MISSING_SUBS_JOB = 'missing_subtitles'" in video_js
    # the React tools page opens the job from ?job= (gotoUnifiedTools)
    assert "page: 'video-tools', job: MISSING_SUBS_JOB" in video_js


def test_tools_page_knows_the_job():
    from pathlib import Path
    from core.video.repair.catalog import VIDEO_FINDING_TYPE_META
    assert VIDEO_FINDING_TYPE_META["missing_subtitles"]["label"] == "Missing Subtitles"
    assert VIDEO_FINDING_TYPE_META["missing_subtitles"]["verb"] == "Queue Subtitles"
    tsx = (Path(__file__).resolve().parents[1] /
           "webui/src/routes/tools/-ui/video-finding-detail.tsx").read_text(encoding="utf-8")
    assert "type === 'missing_subtitles') body = <SubtitlesDetail d={d} />" in tsx


def test_advanced_knobs_wired(panel, video_js):
    for element_id, setting in (
            ("vo-sub-min-score", "subtitle_min_score"),
            ("vo-sub-daily-quota", "subtitle_daily_quota"),
            ("vo-sub-upgrade-delta", "subtitle_upgrade_min_delta"),
            ("vo-sub-worker-interval", "subtitle_worker_interval_min"),
            ("vo-sub-worker-batch", "subtitle_worker_batch")):
        assert f'id="{element_id}"' in panel, element_id
        assert f"{setting}:" in video_js, setting


def test_provider_api_keys_have_fields_and_endpoints(panel):
    cards = _video_cards()
    # each key field is id="<field>-api-key"
    assert 'id={`${field}-api-key`}' in cards
    for field in ("opensubtitles", "subdl", "subsource"):
        assert f'<Key field="{field}"' in cards, field
        assert f"'{field}'," in cards.split("export const KEY_FIELDS", 1)[1].split("]", 1)[0], field
    # keys save through the enrichment config endpoint, as <field>_api_key
    assert "export const CONFIG_URL = '/api/video/enrichment/config';" in cards
    assert "`${k}_api_key`, s.keys[k]" in cards
    # the section points at the Services keys
    assert "Services" in panel


def test_sane_defaults(org_src):
    assert "subtitle_langs: text(o.subtitle_langs, 'en')" in org_src
    # the chain: OpenSubtitles when the stored order is garbage or missing
    assert "return (list.length ? list : ['opensubtitles'])" in org_src
