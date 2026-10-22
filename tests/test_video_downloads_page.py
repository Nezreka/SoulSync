"""Downloads page UX — string-contract level (like test_video_side_shell.py), so a
refactor that silently drops the per-type theming, the sidebar count, or the new
statuses fails here.

The legacy vanilla-JS page (webui/static/video/video-downloads-page.js) was
rewritten in React for the unified downloads page (PR #1651). These contracts
now pin the same behaviors in the React implementation under
webui/src/routes/active-downloads/. the cards were rebuilt on the shared dlc-*
row grammar (dl-parts.tsx, styled in static/downloads-premier.css), so these pin
the dlc names now.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VDL = _ROOT / "webui" / "src" / "routes" / "active-downloads"
_LIST = (_VDL / "-ui" / "vdl-list.tsx").read_text(encoding="utf-8")
_USE = (_VDL / "-vdl.use-downloads.ts").read_text(encoding="utf-8")
_HELPERS = (_VDL / "-vdl.helpers.ts").read_text(encoding="utf-8")
_TYPES = (_VDL / "-vdl.types.ts").read_text(encoding="utf-8")
_HEADER = (_VDL / "-ui" / "vdl-header.tsx").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
_CARDS_CSS = (_ROOT / "webui" / "static" / "downloads-premier.css").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")


def test_cards_carry_a_type_for_cinema_theming():
    assert "export function vdlType(" in _HELPERS
    assert "data-vtype" in _LIST                       # set on each card
    # the three Cinema palette colours are defined per type
    for vt in ('[data-vtype="movie"]', '[data-vtype="tv"]', '[data-vtype="youtube"]'):
        assert vt in _CARDS_CSS, vt
    assert "--vt: 79, 143, 247" in _CARDS_CSS and "--vt: 240, 69, 75" in _CARDS_CSS   # azure + red
    # and the rows actually use it
    assert "rgb(var(--vt))" in _CARDS_CSS


def test_importing_is_a_first_class_active_status():
    assert "importing: { label: 'Importing'" in _HELPERS and "import_failed:" in _HELPERS
    assert "'importing'" in _TYPES                # VDL_ACTIVE_STATUSES counts it as active
    assert "dlc-rowbar-indet" in _LIST and ".dlc-rowbar-indet" in _CARDS_CSS   # sweep for queued/searching/importing


def test_sidebar_has_a_live_downloads_count():
    assert "data-video-downloads-badge" in _INDEX     # the nav badge element
    assert "updateVideoDownloadsBadge" in _USE
    assert "VideoDownloadsBadgePoller" in _USE        # stays live off-page too
    assert "[data-video-downloads-badge]" in _USE     # the poller queries the badge


def test_cards_expand_into_a_detail_drawer():
    assert "function VdlSheet(" in _LIST
    assert "expanded" in _USE                          # open state survives re-patches
    assert "dlc-cast" in _LIST and "dlc-hero-text" in _LIST   # cast + synopsis sections
    assert "Copy path" in _LIST                        # copy-path action
    # the lazy TMDB detail endpoint the drawer fetches synopsis/cast from
    assert "/api/video/downloads/meta/" in _HELPERS


def test_drawer_renders_the_rich_tmdb_fields():
    # cast PHOTOS (the bug was the wrong field name) + logo header + trailer + providers
    assert "c.photo" in _LIST                          # correct TMDB cast-photo field
    assert "dlc-hero-logo" in _LIST                    # title logo header
    assert "dlc-trailer" in _LIST and "dlc-prov" in _LIST   # trailer + where-to-watch
    assert "trailer_url" in _LIST and "providers" in _LIST


def test_youtube_open_button_opens_the_channel_page_not_a_show():
    # regression: the open-show button ran parseInt(youtube_id) → opened a random library
    # show (id 3 → '3rd Rock'). youtube cards must open the in-app CHANNEL page instead.
    assert "vdlOpenDetail('channel'" in _LIST           # opens the in-app channel page
    assert "youtube.com/watch?v=" in _LIST             # fallback when the channel id is unknown
    # clicking it dispatches an open-detail for the channel (reuses the show-detail page)
    assert "export function vdlOpenDetail(" in _LIST


def test_drawer_has_episode_youtube_and_availability_blocks():
    assert "ym.thumbnail_url" in _LIST                 # youtube big thumbnail header
    assert "dlc-hero-ep" in _LIST and "ep?.still_url" in _LIST   # episode still + block
    assert "ctx.peer" in _LIST                         # grab-time availability snapshot
    assert "yt-meta/" in _HELPERS                      # youtube metadata fetch
    assert "season=${" in _HELPERS and "episode=${" in _HELPERS   # episode params on the meta fetch


def test_download_meta_routes_are_registered():
    import api.video as videoapi
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/video/downloads/meta/<kind>/<int:tmdb_id>" in rules
    assert "/api/video/downloads/yt-meta/<video_id>" in rules



# --- overnight polish pass (upgrade watch · retry-all · manual import · skeleton) ---

def test_completed_rows_can_show_an_upgrade_watch_chip():
    # backend annotates completed rows still on the wishlist (below-cutoff grabs)…
    assert "upgrade_watch" in _LIST
    assert "dlc-tag-watch" in _LIST and ".dlc-tag-watch" in _CARDS_CSS
    # …and the drawer explains what the chip means
    assert "watching for a better copy" in _LIST


def test_youtube_rows_name_their_channel_on_the_collapsed_row():
    assert "const channel = vdlRowType(d) === 'youtube' ? ctx.channel || ctx.channel_title" in _LIST


def test_import_failed_rows_offer_manual_import():
    assert "const needsImport = d.status === 'import_failed' && canImport;" in _LIST
    assert "vdlGotoVideoImport" in _LIST               # navigates to the Import page
    assert "'video-import'" in _LIST                  # …via the video-side navigate event
    assert "canImport" in _LIST                       # admin-gated, same rule as video-side.js
    assert "Manual import" in _LIST                   # sheet button label


def test_failed_rows_get_a_retry_all_button():
    assert "Retry failed (" in _HEADER                 # in the header's actions menu
    assert "retryAllFailed" in _USE
    assert "vdlIsRetryable" in _HELPERS
    # only real grab failures with a known release count as retryable
    assert "d.status === 'failed' && Boolean(d.username) && Boolean(d.filename)" in _HELPERS


def test_first_visit_paints_a_loading_skeleton_and_rich_empty_state():
    assert "vdpg-skel-row" in _LIST and ".vdpg-skel-row" in _CSS
    assert "vdpg-empty-t" in _LIST and ".vdpg-empty-t" in _CSS
    assert "No downloads yet" in _LIST
