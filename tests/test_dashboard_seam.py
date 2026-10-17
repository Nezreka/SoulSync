"""Guards for the dashboard's socket → React seam.

Same contract the tools seam pinned: every re-broadcast is dispatched INSIDE
the handler function (never the socket binding), so all of a handler's callers
— socket frames, the 10s HTTP fallback pollers, any replay — reach the React
dashboard; and no dispatch is duplicated at the binding, which would deliver
every frame twice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parent.parent / "webui"
STATIC = WEBUI / "static"
EVENTS_TS = WEBUI / "src" / "routes" / "dashboard" / "-dash.events.ts"


def _handler_body(filename: str, handler: str, *, strip_comments: bool = True) -> str:
    source = (STATIC / filename).read_text(encoding="utf-8")
    tail = source.split(f"function {handler}")[1]
    body = re.split(r"\n(?:async )?function ", tail)[0]
    if not strip_comments:
        return body
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))


# The 16 provider renderers all dispatch the ONE canonical channel with their
# own literal id — the fifth provider registry (per-provider socket names) is
# deliberately not inherited by React.
ENRICH_RENDERERS = {
    "updateMusicBrainzStatusFromData": "musicbrainz",
    "updateAudioDBStatusFromData": "audiodb",
    "updateDiscogsStatusFromData": "discogs",
    "updateDeezerStatusFromData": "deezer",
    "updateJioSaavnStatusFromData": "jiosaavn",
    "updateSpotifyEnrichmentStatusFromData": "spotify",
    "updateiTunesEnrichmentStatusFromData": "itunes",
    "updateLastFMEnrichmentStatusFromData": "lastfm",
    "updateGeniusEnrichmentStatusFromData": "genius",
    "updateBandcampEnrichmentStatusFromData": "bandcamp",
    "updateTidalEnrichmentStatusFromData": "tidal",
    "updateQobuzEnrichmentStatusFromData": "qobuz",
    "updateAmazonEnrichmentStatusFromData": "amazon",
    "updateSimilarArtistsEnrichmentStatusFromData": "similar_artists",
    "updateHydrabaseStatusFromData": "hydrabase",
    "updateSoulIDStatusFromData": "soulid",
}

CORE_HANDLERS = {
    "handleWatchlistCountUpdate": "ss:watchlist-count",
    "handleDashboardStats": "ss:dashboard-stats",
    "handleDashboardActivity": "ss:dashboard-activity",
    "handleDashboardToast": "ss:dashboard-toast",
    "handleDashboardDbStats": "ss:dashboard-db-stats",
    "handleDashboardWishlistCount": "ss:dashboard-wishlist-count",
    "handleServiceStatusUpdate": "ss:service-status",
}


@pytest.mark.parametrize("handler,provider_id", sorted(ENRICH_RENDERERS.items()))
def test_every_renderer_dispatches_its_canonical_id(handler: str, provider_id: str) -> None:
    body = _handler_body("enrichment.js", handler)
    needle = f"CustomEvent('ss:enrich-status', {{ detail: {{ id: '{provider_id}', data }} }})"
    assert needle in body, (
        f"{handler} no longer re-broadcasts ss:enrich-status with id '{provider_id}' — "
        "its pill on the React dashboard goes dead"
    )


@pytest.mark.parametrize("handler", sorted(ENRICH_RENDERERS))
def test_renderers_are_dispatch_only(handler: str) -> None:
    """DISPATCH-ONLY since the dashboard flip: the orbs, tooltips and status
    classes are React-rendered from these frames. Any DOM access reappearing
    in a renderer would fight React for its own nodes."""
    body = _handler_body("enrichment.js", handler)
    assert "getElementById" not in body and "querySelector" not in body, (
        f"{handler} touches the DOM again — its orb is React-rendered now"
    )


@pytest.mark.parametrize("handler,event", sorted(CORE_HANDLERS.items()))
def test_core_handlers_dispatch(handler: str, event: str) -> None:
    body = _handler_body("core.js", handler)
    assert f"CustomEvent('{event}'" in body, f"{handler} lost its {event} re-broadcast"


def test_rate_monitor_dispatches() -> None:
    body = _handler_body("api-monitor.js", "_handleRateMonitorUpdate")
    assert "CustomEvent('ss:rate-monitor'" in body


def test_no_dispatch_at_the_socket_bindings() -> None:
    """The bindings must stay one-liners delegating to handlers — a dispatch
    there too would deliver every frame twice."""
    core = (STATIC / "core.js").read_text(encoding="utf-8")
    wiring = core.split("function initializeWebSocket")[1].split("\nfunction ")[0]
    offenders = [
        line.strip()
        for line in wiring.splitlines()
        if "socket.on(" in line and "ss:enrich-status" in line
    ]
    assert not offenders, f"enrich dispatch duplicated at the binding: {offenders}"


def test_react_subscribes_to_every_event() -> None:
    events = EVENTS_TS.read_text(encoding="utf-8")
    for name in [
        "ss:enrich-status",
        *CORE_HANDLERS.values(),
        "ss:rate-monitor",
        "ss:jiosaavn-experimental",
        "ss:dev-mode",
    ]:
        assert f"'{name}'" in events, f"{name} is broadcast but -dash.events.ts never names it"


# ── The header visibility + quick-nav seams (P4) ─────────────────────────────
#
# The JioSaavn and Hydrabase orbs are shown/hidden by SETTINGS-PAGE writers
# (syncJiosaavnEnrichmentBubble; the three dev-mode sites). Those writers drive
# vanilla DOM the React dashboard does not read, so each one also dispatches an
# ss: event the React header subscribes to. The wishlist hero button's
# fast/slow path was an anonymous closure in init.js — it is a NAMED function
# now so the React header can invoke it (it reads script-scoped state no
# module can reach).


def test_service_status_poller_twin_dispatches() -> None:
    """The five poll↔socket twin pairs have separate code paths. For
    service-status, the HTTP poller (fetchAndUpdateServiceStatus, the app-wide
    5s sidebar interval) must dispatch the SAME ss:service-status event the
    socket handler does — it early-returns while the socket is connected, so
    exactly one source fires at a time and React needs no poll of its own."""
    body = _handler_body("shared-helpers.js", "fetchAndUpdateServiceStatus")
    assert "CustomEvent('ss:service-status'" in body, (
        "fetchAndUpdateServiceStatus lost its re-broadcast — with the socket "
        "down the React service cards freeze"
    )
    # The socket twin keeps its own dispatch (pinned by CORE_HANDLERS above);
    # nothing else may dispatch this event.
    for name in ["core.js", "shared-helpers.js", "enrichment.js", "api-monitor.js"]:
        count = (STATIC / name).read_text(encoding="utf-8").count(
            "CustomEvent('ss:service-status'"
        )
        expected = 1 if name in ("core.js", "shared-helpers.js") else 0
        assert count == expected, f"{name}: {count} ss:service-status dispatches, expected {expected}"


def test_socket_connected_mirror_stays_in_lockstep() -> None:
    """window._socketConnected mirrors core.js's script-scoped socketConnected
    so React's fallback pollers can apply the vanilla twins' socket gate. All
    three write sites (init false, connect true, disconnect false) must keep
    the mirror in step."""
    core = (STATIC / "core.js").read_text(encoding="utf-8")
    assert "let socketConnected = false;" in core
    assert core.count("window._socketConnected = false;") == 2, (
        "expected the mirror set false at init AND on disconnect"
    )
    assert core.count("window._socketConnected = true;") == 1, (
        "expected the mirror set true on connect"
    )
    # Every bare-flag write has a mirror write adjacent — no site may drift.
    bare = core.count("socketConnected = true;") + core.count("socketConnected = false;")
    mirrored = core.count("window._socketConnected = true;") + core.count(
        "window._socketConnected = false;"
    )
    assert bare == mirrored * 2, (
        f"socketConnected writes ({bare - mirrored}) != mirror writes ({mirrored}) — "
        "a new write site forgot the window mirror"
    )


def test_jiosaavn_bubble_writer_dispatches() -> None:
    body = _handler_body("settings.js", "syncJiosaavnEnrichmentBubble")
    assert "CustomEvent('ss:jiosaavn-experimental'" in body, (
        "syncJiosaavnEnrichmentBubble no longer re-broadcasts — the React "
        "JioSaavn orb misses live experimental toggles"
    )


def test_dev_mode_writers_dispatch() -> None:
    settings = (STATIC / "settings.js").read_text(encoding="utf-8")
    enabled = settings.count("CustomEvent('ss:dev-mode', { detail: { enabled: true } })")
    disabled = settings.count("CustomEvent('ss:dev-mode', { detail: { enabled: false } })")
    assert enabled == 2, (
        f"expected the dev-mode ENABLE dispatch at both write sites (initial "
        f"check + activateDevMode), found {enabled}"
    )
    assert disabled == 1, (
        f"expected the dev-mode DISABLE dispatch at the hydra disconnect site, found {disabled}"
    )


def test_wishlist_hero_behaviour_is_a_named_function() -> None:
    init = (STATIC / "init.js").read_text(encoding="utf-8")
    assert "async function openWishlistFromHero()" in init, (
        "openWishlistFromHero is gone — the React wishlist hero button loses "
        "the in-flight-download fast/slow path"
    )
    assert "wishlistButton.addEventListener('click', openWishlistFromHero)" in init, (
        "the vanilla wishlist hero button no longer binds openWishlistFromHero"
    )


# ── The AudioDB logo rehome (P8) ─────────────────────────────────────────────
#
# The logo lived ONLY as a 40KB base64 line inside the dashboard markup, and
# TWO shipped React pages resolved it off that DOM (artist-detail via
# getAudioDBLogoURL, library by querying img.audiodb-logo directly). Deleting
# the markup would have silently degraded both to their text fallbacks — no
# error, no failing test. The asset is a real file now.

BRANDS = WEBUI / "static" / "img" / "brands"


def test_audiodb_logo_is_a_real_file() -> None:
    png = (BRANDS / "audiodb.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "audiodb.png is not a PNG"
    assert len(png) > 10_000, "suspiciously small — extraction went wrong"


def test_index_html_uses_the_file_not_the_inline_base64() -> None:
    """The dashboard markup (and with it the old <img>) is deleted since the
    flip — what must never return is the 40KB inline base64, and the React
    chrome must load the extracted FILE."""
    html = (WEBUI / "index.html").read_text(encoding="utf-8", errors="replace")
    assert "data:image/png;base64,iVBOR" not in html, (
        "the 40KB inline AudioDB logo is back in index.html"
    )
    chrome = (
        WEBUI / "src" / "routes" / "dashboard" / "-ui" / "dashboard-header.tsx"
    ).read_text(encoding="utf-8")
    assert "'/static/img/brands/audiodb.png'" in chrome, (
        "the React AudioDB orb no longer loads the extracted file"
    )


def test_get_audiodb_logo_url_survives_the_markup_deletion() -> None:
    core = (STATIC / "core.js").read_text(encoding="utf-8")
    assert "const AUDIODB_LOGO_URL = '/static/img/brands/audiodb.png';" in core
    body = _handler_body("core.js", "getAudioDBLogoURL")
    assert ": AUDIODB_LOGO_URL" in body, (
        "getAudioDBLogoURL fell back to null again — with the dashboard markup "
        "gone, artist-detail and library lose the logo silently"
    )


# ── The post-flip hardening sweep ────────────────────────────────────────────
#
# Every id inside the recorded dashboard fixture is React-rendered now. The
# only vanilla files allowed to reference them are read-only or explicitly
# ADOPTED writers; anything new is a foreign writer waiting to fight React
# (the tools-flip lesson class that artefact-green tests cannot catch).

FIXTURE = (
    WEBUI / "src" / "routes" / "dashboard" / "-ui" / "dash-vanilla-fixture.html"
)

ALLOWED_DASHBOARD_ID_REFS = {
    # helper.js: the help-search/tour CONTENT — read-only selectors resolved
    # against React's ids.
    "helper.js": None,  # None = any id allowed (read-only by construction)
    # worker-orbs.js: anchors #dashboard-page .dashboard-header, reads only.
    "worker-orbs.js": {"dashboard-page"},
    # wishlist-tools.js: the Active Downloads ADOPTED REGION — React renders
    # the shell, updateDashboardDownloads paints the container on purpose.
    "wishlist-tools.js": {
        "dashboard-active-downloads-section",
        "dashboard-downloads-container",
    },
    # init.js: initializeWatchlist's two hero bindings are null-guarded no-ops
    # (the buttons render after load); React binds its own handlers.
    "init.js": {"watchlist-button", "wishlist-button"},
}


def test_no_vanilla_writers_on_react_dashboard_ids() -> None:
    ids = sorted(set(re.findall(r'id="([^"]+)"', FIXTURE.read_text(encoding="utf-8"))))
    assert len(ids) > 100, "fixture looks truncated"
    offenders: list[str] = []
    for js in sorted(STATIC.glob("*.js")):
        if js.name.startswith("video"):
            continue
        allowed = ALLOWED_DASHBOARD_ID_REFS.get(js.name, set())
        if allowed is None:
            continue
        code_lines = [
            line
            for line in js.read_text(encoding="utf-8", errors="replace").splitlines()
            if not line.strip().startswith(("//", "*", "/*"))
        ]
        code = "\n".join(code_lines)
        for dom_id in ids:
            if dom_id in allowed:
                continue
            for pattern in (
                f"getElementById('{dom_id}')",
                f'getElementById("{dom_id}")',
                f"'#{dom_id}'",
                f'"#{dom_id}"',
            ):
                if pattern in code:
                    offenders.append(f"{js.name}: {pattern}")
                    break
    assert not offenders, (
        "surviving vanilla references React-rendered dashboard ids — read-only "
        "or adopted refs belong in ALLOWED_DASHBOARD_ID_REFS, writers must be "
        f"severed: {offenders}"
    )


def test_fixture_is_the_recorded_vanilla_page() -> None:
    """The artefact differentials pin the port against this recording — it must
    stay the byte capture of the deleted #dashboard-page block."""
    fixture = FIXTURE.read_text(encoding="utf-8")
    assert fixture.lstrip().startswith('<div class="page" id="dashboard-page">')
    assert 'class="dash-grid"' in fixture
    html = (WEBUI / "index.html").read_text(encoding="utf-8", errors="replace")
    assert 'id="dashboard-page"' not in html, (
        "the vanilla dashboard markup is back in index.html"
    )
