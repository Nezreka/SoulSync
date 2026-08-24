"""Search page: recent-search chips + keyboard fast paths (contract tests).

Recents are remembered on COMMIT (opening a result), never on raw keystrokes,
so the list holds real queries instead of every typo prefix. Chips render on
the idle page above Trending; Enter opens the top result; Escape clears back
to idle.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-search.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_recents_remembered_on_commit_not_keystroke():
    open_fn = _JS.split("function openCard")[1].split("function wire")[0]
    assert "rememberSearch(lastQuery)" in open_fn
    # the debounced search path must NOT remember (typo prefixes)
    run_fn = _JS.split("function runSearch")[1].split("function runChannel")[0]
    assert "rememberSearch" not in run_fn


def test_recents_deduped_capped_and_renderable():
    fn = _JS.split("function rememberSearch")[1].split("function recentsHTML")[0]
    assert "toLowerCase()" in fn          # case-insensitive dedupe
    assert "slice(0, 8)" in fn            # capped
    assert "data-vsr-recent=" in _JS and "data-vsr-recent-clear" in _JS
    # idle page shows them even before trending has loaded
    idle = _JS.split("function showIdle")[1].split("function _json")[0]
    assert "recentsHTML()" in idle


def test_chip_click_and_clear_are_wired():
    wire_fn = _JS.split("function wire")[1]
    assert "closest('[data-vsr-recent]')" in wire_fn
    assert "closest('[data-vsr-recent-clear]')" in wire_fn
    assert "localStorage.removeItem('vsRecent')" in wire_fn


def test_keyboard_enter_opens_top_result_escape_clears():
    wire_fn = _JS.split("function wire")[1]
    assert "e.key !== 'Enter'" in wire_fn and "openCard(first)" in wire_fn
    assert "e.key === 'Escape'" in wire_fn


def test_css_exists_for_chips():
    assert ".vsr-recent-chip" in _CSS and ".vsr-recent-clear" in _CSS

def test_basic_search_runs_in_app_provider_searches():
    assert "function setMode" in _JS and "function renderBasicPreview" in _JS
    assert "[data-vsr-basic-form]" in _JS
    assert "data-vsr-basic-focus" in _JS
    assert "/api/video/downloads/search/start" in _JS and "/api/video/downloads/search/poll" in _JS
    assert "/api/video/downloads/config" in _JS and "basicIdsFromDownloadConfig" in _JS
    assert "source: 'soulseek'" in _JS and "indexer: 'thepiratebay'" in _JS and "source: 'usenet'" in _JS
    assert "kind: 'FlareSolverr torrent scraper', source: 'extto'" in _JS
    assert "thepiratebay.org" not in _JS and "1337x.to" not in _JS and 'target="_blank"' not in _JS
    basic_fn = _JS.split("function renderBasicPreview")[1].split("function freshPeriodLabel")[0]
    assert "fetch(" not in basic_fn
    assert ".vsr-tabs" in _CSS and ".vsr-basic" in _CSS and ".vsr-basic-hit" in _CSS
    assert "data-vsr-basic-source-tab" in _JS and ".vsr-basic-source-tabs" in _CSS
    assert "vsr-basic-hit-analysis" in _JS and "vsr-basic-hit-reason" not in _JS
    assert "basicSizeLabel" in _JS and "basicHealthLabel" in _JS
    assert "vsr-basic-hit-side" in _JS and "vsr-basic-hit-linkstate" in _JS
    assert "vsr-basic-loader" in _JS and "vsr-basic-tab-loader" in _JS
    assert ".vsr-basic-hit-side" in _CSS and "vsrBasicScan" in _CSS and "vsrBasicDots" in _CSS


def test_fresh_releases_tab_fetches_extto_homepage_board_in_app():
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert 'data-vsr-tab="fresh"' in index and 'data-vsr-panel="fresh"' in index
    assert "Sourced from EXT.to" in index
    assert "FRESH_URL = '/api/video/downloads/fresh-releases'" in _JS
    assert "function renderFreshReleases" in _JS and "function loadFreshReleases" in _JS
    assert "data-vsr-fresh-period" in _JS and "freshSectionHTML('movies', 'Movies')" in _JS
    assert "freshSectionHTML('tv', 'TV Series')" in _JS
    fresh_fn = _JS.split("function freshRowHTML")[1].split("function freshSectionHTML")[0]
    assert "<a " not in fresh_fn and "href=" not in fresh_fn
    assert ".vsr-fresh-results" in _CSS and ".vsr-fresh-row" in _CSS and ".vsr-fresh-loader" in _CSS
    assert "repeat(3, minmax" in _CSS
    assert "data-vsr-fresh-pick" in _JS and "freshOpenIdentify" in _JS
    assert "data-vsr-fi-search" in _JS and "data-vsr-fi-result" in _JS
    assert "source: 'extto'" in _JS and "'/api/video/downloads/grab'" in _JS
    assert "search_ctx: isMovie ? { scope: 'movie'" in _JS
    assert "scope: freshIdentify.mode === 'episode' ? 'episode' : 'season'" in _JS
    assert ".vsr-fi-modal" in _CSS and ".vsr-fresh-pick" in _CSS


def test_basic_search_hits_can_be_identified_and_grabbed():
    """Every Basic Search hit gets the Fresh Releases treatment: identify it as a
    movie / episode / season pack, then grab it from whichever source it came from.
    The behaviour of the per-source descriptor is executed in
    webui/src/test/video-basic-search-grab.test.ts; this pins the WIRING, which
    that test cannot see because it lifts the functions out of the file."""
    assert "data-vsr-basic-grab" in _JS and "function basicOpenIdentify" in _JS
    assert "basicRowsBySource[sourceId] = shown" in _JS, "the Identify buttons index into nothing"
    assert "basicOpenIdentify(bits[0], parseInt(bits[1], 10))" in _JS, "the click handler is not wired"
    # all three scopes offered, because a release-title search is not told what it found
    assert "modes: ['movie', 'episode', 'season']" in _JS
    assert "var modes = freshIdentify.modes ||" in _JS, "the modal still hard-codes the fresh-tab modes"
    # the payload takes its carriers from the source, not from a hard-coded EXT.to shape
    assert "function identifyGrabDescriptor" in _JS
    assert "source: 'extto'," not in _JS.split("function freshGrabPayload")[1].split("function freshGrabIdentifiedRelease")[0]
    # a Soulseek season pack fans out at grab time; a torrent cannot (its files
    # do not exist yet) and goes in as one season-scoped row
    assert "'/api/video/downloads/grab-pack'" in _JS
    assert "(grab.files || []).length > 1" in _JS
    assert ".vsr-basic-hit-grab" in _CSS


def test_search_results_carry_the_identity_the_release_name_already_states():
    """The identify modal prefills from the hit. _evaluate_hits parses every release
    anyway, then used to throw the identity away — so Basic Search could show you
    'Silo S03E08' and still make you retype the season and episode."""
    from api.video.downloads import _evaluate_hits

    hits = [{"title": "Silo S03E08 1080p WEB h264-SKYFiRE", "size_bytes": 517_000_000,
             "protocol": "torrent", "seeders": 40, "download_url": "magnet:?x"},
            {"title": "Interstellar 2014 1080p BluRay x264-YIFY", "size_bytes": 2_100_000_000,
             "protocol": "torrent", "seeders": 900, "download_url": "magnet:?z"}]
    out = {r["title"].split()[0]: r for r in
           _evaluate_hits(hits, None, "movie", None, None, blocked=frozenset(), blocked_users=frozenset())}

    ep = out["Silo"]
    assert ep["search_title"] == "Silo", "the modal's title box would prefill with the raw release name"
    assert ep["season"] == 3 and ep["episode"] == 8
    assert ep["is_season_pack"] is False

    movie = out["Interstellar"]
    assert movie["year"] == 2014 and movie["season"] is None and movie["episode"] is None
    # `source` stays the RELEASE source (BluRay/WEB) — the download source comes
    # from the tab, and renaming this field would silently change every chip row
    assert movie["source"] == "bluray"


# The four payload shapes webui/src/test/video-basic-search-grab.test.ts builds by
# EXECUTING freshGrabPayload. Replayed here against the real endpoint so both sides
# of the JS/Python boundary are checked against the same bodies.
_SHOW_CTX = {"title": "Silo", "year": 2023}
_BASIC_PAYLOADS = {
    "episode/torrent": {
        "kind": "show", "source": "torrent", "title": "Silo",
        "release_title": "Silo S03E08 1080p WEB", "filename": "Silo S03E08 1080p WEB",
        "username": "The Pirate Bay", "indexer_id": 42, "protocol": "torrent",
        "download_url": "https://prowlarr/dl.torrent", "magnet_uri": "magnet:?xt=1",
        "candidates": [], "size_bytes": 517_000_000, "quality_label": "1080p web",
        "media_id": 125988, "media_source": "tmdb", "year": 2023, "poster_url": "/p.jpg",
        "search_ctx": {"scope": "episode", "season": 3, "episode": 8, **_SHOW_CTX}},
    "season/torrent": {
        "kind": "show", "source": "torrent", "title": "Silo",
        "release_title": "Silo S03 COMPLETE 1080p WEB", "filename": "Silo S03 COMPLETE 1080p WEB",
        "username": "1337x", "indexer_id": "1337x", "protocol": "torrent",
        "download_url": "https://prowlarr/pack.torrent", "magnet_uri": "magnet:?xt=2",
        "candidates": [], "size_bytes": 5_000_000_000, "quality_label": "1080p web",
        "media_id": 125988, "media_source": "tmdb", "year": 2023, "poster_url": "/p.jpg",
        "search_ctx": {"scope": "season", "season": 3, "episode": None, **_SHOW_CTX}},
    "movie/extto": {
        "kind": "movie", "source": "extto", "title": "Interstellar",
        "release_title": "Interstellar 2014 1080p BluRay", "filename": "Interstellar 2014 1080p BluRay",
        "username": "EXT.to", "indexer_id": "extto", "protocol": "torrent",
        "download_url": "magnet:?xt=3", "magnet_uri": "magnet:?xt=3",
        "candidates": [], "size_bytes": 2_100_000_000, "quality_label": "1080p bluray",
        "media_id": 157336, "media_source": "tmdb", "year": 2014, "poster_url": "/i.jpg",
        "search_ctx": {"scope": "movie", "title": "Interstellar", "year": 2014}},
    "episode/soulseek": {
        "kind": "show", "source": "soulseek", "title": "Silo",
        "release_title": "Silo S03E09 1080p WEB", "filename": "@@silo/Silo.S03E09.mkv",
        "username": "peer1", "candidates": [{"username": "peer2", "filename": "other.mkv",
                                             "size_bytes": 9, "quality_label": None, "title": None}],
        "size_bytes": 517_000_000, "quality_label": "1080p web",
        "media_id": 125988, "media_source": "tmdb", "year": 2023, "poster_url": "/p.jpg",
        "search_ctx": {"scope": "episode", "season": 3, "episode": 9, **_SHOW_CTX}},
}


def test_every_basic_search_source_grabs_into_a_row_the_pipeline_understands(tmp_path, monkeypatch):
    """Basic Search hands four differently-shaped payloads to one endpoint. Each has
    to land as a row the monitor can poll and the importer can file — which is a
    different set of columns per source, and (for TV) the search_ctx scope that
    decides single-file import vs pack fan-out."""
    import api.video as videoapi
    from flask import Flask
    from database.video_database import VideoDatabase
    from core.video.season_pack import is_pack_download, want_season_of
    import core.video.client_grab as cg
    import core.video.slskd_download as sd

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    db.set_setting("tv_path", str(tmp_path / "TV"))
    videoapi._video_db = db
    try:
        monkeypatch.setattr(cg, "grab", lambda *a, **k: {"ok": True, "ref": "hash123"})
        monkeypatch.setattr(sd, "start_download", lambda *a, **k: {"ok": True})
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        client = app.test_client()

        rows = {}
        for label, payload in _BASIC_PAYLOADS.items():
            r = client.post("/api/video/downloads/grab", json=payload)
            assert r.status_code == 200 and r.get_json().get("ok"), (label, r.get_json())
            rows[label] = db.get_video_download(r.get_json()["id"])

        # every torrent-side source is polled by the client watcher, keyed on client_ref
        for label in ("episode/torrent", "season/torrent", "movie/extto"):
            assert rows[label]["source"] == "torrent", label
            assert rows[label]["client_ref"] == "hash123", label
        # ...and Soulseek keeps its peer + its retry pool
        ss = rows["episode/soulseek"]
        assert ss["source"] == "soulseek" and ss["username"] == "peer1"
        assert "peer2" in (ss["candidates"] or ""), "the retry pool was dropped"

        # TV scope decides single-file import vs pack fan-out
        assert is_pack_download(rows["episode/torrent"]) is False
        assert is_pack_download(rows["episode/soulseek"]) is False
        assert is_pack_download(rows["season/torrent"]) is True
        assert want_season_of(rows["season/torrent"]) == 3
        # a movie is never a pack, and carries no season to filter by
        assert is_pack_download(rows["movie/extto"]) is False
        assert want_season_of(rows["movie/extto"]) is None

        # each row lands in the right library root
        assert rows["movie/extto"]["target_dir"] == str(tmp_path / "Movies")
        assert rows["season/torrent"]["target_dir"] == str(tmp_path / "TV")

        # Basic Search shows the same episode from five sources at once, so the
        # in-flight dedup is what stops "try another source" becoming two copies of
        # S03E08 downloading side by side. It answers ok+already, NOT a second row.
        before = len(db.list_video_downloads())
        dupe = client.post("/api/video/downloads/grab",
                           json={**_BASIC_PAYLOADS["episode/torrent"], "source": "extto",
                                 "username": "EXT.to", "download_url": "magnet:?other"})
        assert dupe.get_json() == {"ok": True, "already": True}, dupe.get_json()
        assert len(db.list_video_downloads()) == before, "the same episode queued twice"
    finally:
        videoapi._video_db = None


def test_an_extto_basic_search_grab_resolves_its_magnet_at_grab_time(tmp_path, monkeypatch):
    """EXT.to lists releases WITHOUT magnets: the search page carries a per-row torrent
    id but not the tokens the magnet endpoint is signed with, so each magnet costs its
    own Cloudflare-challenged detail fetch. Resolving 25 to draw a result list would
    take minutes — so the list ships link-less and the ONE release you pick is resolved
    here. Without this every EXT.to hit in Basic Search read 'No link'."""
    import api.video as videoapi
    from flask import Flask
    from database.video_database import VideoDatabase
    import core.video.client_grab as cg
    import core.video.extto_search as ex

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    videoapi._video_db = db
    try:
        seen = {}
        monkeypatch.setattr(cg, "grab", lambda src, url, **k: seen.update(source=src, url=url)
                            or {"ok": True, "ref": "hash123"})
        monkeypatch.setattr(ex, "resolve_magnet",
                            lambda info_url, **kw: seen.update(resolved=info_url, id=kw.get("magnet_id"))
                            or {"ok": True, "magnet": "magnet:?xt=urn:btih:abc&dn=Interstellar&tr=udp://t:1337"})
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        payload = {"kind": "movie", "title": "Interstellar", "source": "extto",
                   "release_title": "Interstellar 2014 1080p BluRay", "username": "EXT.to",
                   "indexer_id": "extto", "protocol": "torrent",
                   "download_url": "", "magnet_uri": "",          # the list has no magnet
                   "info_url": "https://ext.to/interstellar-2014-1014669/", "magnet_id": "1014669",
                   "size_bytes": 2_100_000_000, "media_id": 157336, "media_source": "tmdb",
                   "year": 2014, "search_ctx": {"scope": "movie", "title": "Interstellar", "year": 2014}}
        r = app.test_client().post("/api/video/downloads/grab", json=payload)
        assert r.status_code == 200 and r.get_json().get("ok"), r.get_json()
        assert seen["resolved"] == "https://ext.to/interstellar-2014-1014669/"
        assert seen["id"] == "1014669", "the row's torrent id was not carried to the resolver"
        # the resolved magnet is what reaches the client, and the row is a torrent row
        assert seen["source"] == "torrent"
        assert seen["url"].startswith("magnet:?xt=urn:btih:abc")
        assert db.list_video_downloads()[0]["source"] == "torrent"
    finally:
        videoapi._video_db = None


def test_a_release_that_will_not_resolve_fails_the_grab_instead_of_queueing_a_dead_row(tmp_path, monkeypatch):
    import api.video as videoapi
    from flask import Flask
    from database.video_database import VideoDatabase
    import core.video.client_grab as cg
    import core.video.extto_search as ex

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    videoapi._video_db = db
    try:
        monkeypatch.setattr(cg, "grab", lambda *a, **k: {"ok": True, "ref": "nope"})
        monkeypatch.setattr(ex, "resolve_magnet",
                            lambda *a, **k: {"ok": False, "error": "EXT.to: Cloudflare challenge"})
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        r = app.test_client().post("/api/video/downloads/grab", json={
            "kind": "movie", "title": "Interstellar", "source": "extto",
            "release_title": "x", "download_url": "", "magnet_uri": "",
            "info_url": "https://ext.to/x-1/", "media_id": 1, "media_source": "tmdb"})
        assert r.status_code == 502
        assert "Cloudflare" in r.get_json()["error"], "the real reason was swallowed"
        assert db.list_video_downloads() == [], "a row was queued for a magnet we never got"
    finally:
        videoapi._video_db = None


def test_the_search_surfaces_use_the_accent_token_not_a_lookalike_palette():
    """Basic Search and Fresh Releases were built with a hardcoded mint/blue palette
    (#9fe7c6 -> #87b7ff) that only RESEMBLES the accent. On a green accent install the
    two sat side by side and read as a different product; on any other accent the page
    was simply the wrong colour. Every one of them is now the token, so a user's accent
    drives these surfaces like it drives the rest of the app."""
    for literal in ("#9fe7c6", "#87b7ff", "159,231,198", "135,183,255", "#06130f", "#071018"):
        assert literal not in _CSS, "a hardcoded stand-in for the accent came back: " + literal


def test_the_identify_modal_defines_the_accent_token_it_uses():
    """The modal is appended to document.body, so it renders OUTSIDE .vsr-page — the
    element that defines --vd-accent-rgb. Without its own declaration every accent rule
    in it is invalid-at-computed-value-time and SILENTLY resets: the focused search box
    drew a white border (border-color falling back to currentColor) and the primary
    button lost its fill. Nothing errors, it just quietly looks broken."""
    assert ".vsr-fi-backdrop { --vd-accent-rgb:" in _CSS, \
        "the detached modal lost its accent token — its accent rules are now inert"
    # ...and the rules that depend on it, so a failure here names something visible
    # rsplit, not split: a selector list ('.vsr-fi-secondary, .vsr-fi-primary {') contains
    # the same needle, and the LAST rule is the one that wins the cascade anyway.
    primary = _CSS.rsplit(".vsr-fi-primary {", 1)[1][:300]
    assert "--vd-accent-rgb" in primary, "the modal's primary button stopped using the accent"
    focus = _CSS.rsplit(".vsr-fi-search input:focus {", 1)[1][:300]
    assert "--vd-accent-rgb" in focus, "the modal's focus ring stopped using the accent"


def test_the_release_rows_are_cards_not_a_tracker_table():
    """Fresh Releases rendered as a spreadsheet — a column header row and fixed grid
    columns — which is the one layout the rest of SoulSync never uses. Both release
    lists are now cards carrying the accent edge every other card in the app has."""
    assert ".vsr-fresh-table-head { display: none; }" in _CSS, "the column header row is back"
    for card in (".vsr-basic-hit::before", ".vsr-fresh-row::before"):
        # the rule's OWN braces — a fixed character window spills into the rules
        # after it, which happen to use the token, so the guard could not fail
        at = _CSS.index(card + " {")
        block = _CSS[at:_CSS.index("}", at)]
        assert "--vd-accent-rgb" in block, card + " lost its accent edge"


def test_fresh_releases_is_a_stored_board_with_a_refresh_control():
    """The tab reads a SNAPSHOT now — the pull and the per-release matching happen
    in the 'Refresh Fresh Releases' automation or behind the Refresh button, both
    calling core.video.extto_board.refresh_board. Rendering used to wait on a
    Cloudflare challenge just to show a list."""
    assert "data-vsr-fresh-refresh" in _JS and "function refreshFreshReleases" in _JS
    assert "'/refresh'" in _JS, "the button does not call the refresh endpoint"
    assert "refreshFreshReleases()" in _JS, "the Refresh click is not wired"
    # the board's age is stated: there is otherwise no way to tell a quiet hour
    # from a refresh that never ran
    assert "function freshStampHTML" in _JS and "fetched_at" in _JS
    # ...and Refresh is reachable when there is NO board, which is when it matters
    head = _JS.split("function renderFreshReleases")[1].split("function loadFreshReleases")[0]
    assert head.count("freshRefreshHTML()") >= 3, \
        "Refresh is missing from the loading/error/board headers"
    assert ".vsr-fresh-refresh" in _CSS


def test_a_matched_release_shows_what_extto_already_stated():
    """Each row carries the facts from its own EXT.to detail page. This is
    presentational only — the user still identifies the title in the modal, so
    nothing here may feed the grab payload."""
    assert "function freshMetaHTML" in _JS and "function freshArtHTML" in _JS
    meta = _JS.split("function freshMetaHTML")[1].split("function freshArtHTML")[0]
    for field in ("imdb_rating", "year", "runtime_minutes", "quality", "genres"):
        assert field in meta, "the card stopped showing " + field
    # a release with no artwork draws an initial, never ext.to's grey placeholder
    art = _JS.split("function freshArtHTML")[1].split("function freshRowHTML")[0]
    assert "vsr-fresh-art--none" in art
    for cls in (".vsr-fresh-art", ".vsr-fresh-meta", ".vsr-fresh-rating"):
        assert cls in _CSS, cls + " lost its styling"
    # the matched facts must NOT leak into the grab payload — identifying stays manual
    payload = _JS.split("function freshGrabPayload")[1].split("function freshGrabIdentifiedRelease")[0]
    assert "detail" not in payload, "detail facts leaked into the grab payload"


def test_a_matched_release_card_expands_to_everything_extto_stated():
    """The detail page states far more than a card can show, and it is already
    stored — so the card opens in place rather than throwing the rest away."""
    assert "function freshFactsHTML" in _JS and "function freshToggleRow" in _JS
    assert "data-vsr-fresh-toggle" in _JS and "freshToggleRow(tb[0]" in _JS
    # the FULL list in page order, not a hand-picked set of fields: movie and TV
    # pages carry almost disjoint labels, so naming fields would blank one of them
    facts = _JS.split("function freshFactsHTML")[1].split("function freshRowHTML")[0]
    assert "d.facts" in facts and "f.label" in facts and "f.value" in facts

    row = _JS.split("function freshRowHTML")[1].split("function freshSectionHTML")[0]
    # only a matched release opens — an unmatched one would offer a control that
    # reveals nothing
    assert "var can = !!(d && (d.facts || []).length)" in row
    assert "aria-expanded" in row and 'role="button"' in row and "tabindex" in row
    # open state survives a re-render (period switch, refresh)
    assert "freshExpanded[r.url]" in row
    for cls in (".vsr-fresh-facts", ".vsr-fresh-fact", ".vsr-fresh-chev",
                ".vsr-fresh-row--can-open", ".vsr-fresh-row--open"):
        assert cls in _CSS, cls + " lost its styling"


def test_identifying_a_release_does_not_toggle_its_card():
    """The Identify button sits inside the clickable card. Its handler must run
    first and return, or grabbing would also expand the row underneath the modal."""
    handler = _JS.split("var freshPick = e.target.closest('[data-vsr-fresh-pick]')")[1]
    pick_at = handler.find("freshOpenIdentify(")
    toggle_at = handler.find("freshToggleRow(")
    assert pick_at != -1 and toggle_at != -1
    assert pick_at < toggle_at, "the toggle is checked before Identify — a grab would expand the card"
