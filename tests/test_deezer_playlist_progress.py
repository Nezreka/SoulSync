"""Live progress while a Deezer playlist resolves (TheHomeGuy).

    "System seems to hang when trying to load large playlist. Will sit here for
    several minutes doing nothing."

Nothing was hanging. Loading a playlist resolves every unique album twice — once
for release dates, once for real track numbers — and both are rate-limited
requests. A 1,219-track playlist is roughly 900 albums, so ~1,750 calls at 8/s:
minutes of genuine work behind a spinner that said only "Loading playlist: X...".

That is indistinguishable from broken, and it was reported as broken. The work
itself cannot go away — release dates and track numbers are what the download
picker uses to choose a candidate and verify it is the right one — so the fix is
to narrate it.

The seam is the one the shell already uses: the server emits on the socket,
core.js re-broadcasts as an ``ss:`` CustomEvent (``socket`` is a module-scoped
``let`` there, unreachable from a React route), and the card listens while its
fetch is in flight. Same shape as ``ss:discovery-progress`` and
``ss:repair-progress``.

Everything here is best-effort by construction: a progress callback that throws,
a socket that is down, an older server that never emits — none of it may affect
whether the playlist loads.

Later: the same silence was reported again, on the OTHER deezer path. The ARL
account tab passed a progress_cb and narrated; the paste-a-link tab passed None
and sat on "Loading..." for minutes on a 1500-track playlist. Same feature, two
paths, one of them wired. Both are covered here now, and the event constant
moved to -sync.events.ts so the two consumers cannot drift apart.
"""

import inspect
import pathlib

import pytest

from core.deezer_client import resolve_album_track_positions
from core.deezer_download_client import DeezerDownloadClient

EVENT = 'ss:deezer-playlist-progress'
SOCKET_EVENT = 'deezer:playlist_progress'


def read(path):
    return pathlib.Path(path).read_text(encoding='utf-8')


# ── the callback reaches both passes ────────────────────────────────────────

def test_the_track_fetch_accepts_a_progress_callback():
    sig = inspect.signature(DeezerDownloadClient.get_playlist_tracks)
    assert 'progress_cb' in sig.parameters
    assert sig.parameters['progress_cb'].default is None, "must stay optional"


def test_the_track_position_pass_accepts_one_too():
    """It is the second of the two album passes and just as slow."""
    sig = inspect.signature(resolve_album_track_positions)
    assert 'progress_cb' in sig.parameters
    assert sig.parameters['progress_cb'].default is None


class _Resp:
    ok = True

    @staticmethod
    def json():
        return {'data': [{'id': 1, 'track_position': 3}]}


class _Session:
    def get(self, *_a, **_kw):
        return _Resp()


def test_the_track_position_pass_reports_as_it_goes():
    seen = []
    resolve_album_track_positions(_Session(), 'https://api.deezer.com',
                                  [str(i) for i in range(20)],
                                  progress_cb=lambda done, total: seen.append((done, total)))
    assert seen, "a 20-album pass must report something"
    assert seen[-1] == (20, 20), "the last frame has to reach the total"
    assert all(t == 20 for _, t in seen)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen), "must count up"


def test_progress_is_not_emitted_once_per_album():
    """One socket frame per album is 900 frames for one playlist. Every fifth is
    enough for a counter that does not look stuck."""
    seen = []
    resolve_album_track_positions(_Session(), 'https://api.deezer.com',
                                  [str(i) for i in range(50)],
                                  progress_cb=lambda d, t: seen.append(d))
    assert len(seen) < 50


def test_a_callback_that_throws_cannot_break_the_load():
    """Narration is decoration. If it fails, the playlist still resolves."""
    def _boom(*_a):
        raise RuntimeError('socket died mid-emit')

    positions = resolve_album_track_positions(
        _Session(), 'https://api.deezer.com', ['1', '2', '3'], progress_cb=_boom)
    assert positions == {'1': 3}


def test_no_callback_is_the_normal_case():
    """Every other caller of this passes nothing."""
    assert resolve_album_track_positions(
        _Session(), 'https://api.deezer.com', ['1']) == {'1': 3}


# ── the route emits ─────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def route():
    src = read('web_server.py')
    i = src.index('def get_deezer_arl_playlist_tracks(')
    return src[i:src.index('\n@app.route', i)]


def test_the_route_hands_the_fetch_a_progress_emitter(route):
    assert 'progress_cb=_emit_progress' in route


def test_the_frame_carries_what_the_card_needs(route):
    """Which playlist (several cards can be open), how far, and which pass."""
    assert f"socketio.emit('{SOCKET_EVENT}'" in route
    for field in ("'playlist_id'", "'done'", "'total'", "'phase'"):
        assert field in route, field


def test_a_failed_emit_cannot_break_the_route(route):
    """The socket being down must not turn a slow load into a failed one."""
    emitter = route[route.index('def _emit_progress'):route.index('playlist = deezer_dl')]
    assert 'try:' in emitter and 'except' in emitter


# ── the shell re-broadcasts, the card listens ───────────────────────────────

def test_core_js_rebroadcasts_it_for_react():
    """`socket` is a module-scoped `let` in core.js, so a route cannot subscribe
    to it directly — same reason ss:discovery-progress exists."""
    core = read('webui/static/core.js')
    assert f"socket.on('{SOCKET_EVENT}'" in core
    assert f"CustomEvent('{EVENT}'" in core


def test_the_card_listens_only_while_its_fetch_is_running():
    """A listener left attached would repaint the overlay for a load that has
    already finished."""
    tsx = read('webui/src/routes/sync/-ui/account-tabs.tsx')
    assert 'addEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT' in tsx
    assert 'removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT' in tsx
    finally_block = tsx[tsx.index('removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT') - 200:]
    assert 'finally' in tsx[:tsx.index('removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT')][-400:], \
        "removal has to be in the finally, or an error leaves it attached"
    assert 'hideLoadingOverlay' in finally_block[:400]


def test_the_card_ignores_frames_for_other_playlists():
    """The emit is unroomed, so every card hears every frame."""
    tsx = read('webui/src/routes/sync/-ui/account-tabs.tsx')
    assert 'frame.playlist_id) !== String(row.id)' in tsx


def test_the_event_name_matches_on_both_sides():
    """The constant lives in -sync.events.ts, not in whichever component
    happened to consume it first: BOTH deezer tabs listen for these frames
    now, and a shared event defined inside one component's file is how two
    consumers quietly drift apart."""
    core = read('webui/static/core.js')
    events = read('webui/src/routes/sync/-sync.events.ts')
    assert f"'{EVENT}'" in core and f"= '{EVENT}'" in events


def test_the_paste_a_link_tab_listens_too():
    """The second consumer, and the one that was silent for a year: the ARL
    account path passed a progress_cb and narrated, while the paste-a-link
    path passed None and sat on "Loading..." for minutes (Boulder, on a
    1500-track playlist)."""
    tsx = read('webui/src/routes/sync/-ui/url-import-tab.tsx')
    assert 'addEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT' in tsx
    assert 'removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT' in tsx
    before_removal = tsx[:tsx.index('removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT')]
    assert 'finally' in before_removal[-400:], \
        "removal has to be in the finally, or an error leaves it attached"


def test_the_public_playlist_route_emits_as_well():
    """Same event, same shape, so the existing core.js bridge and consumers
    work unchanged."""
    src = read('web_server.py')
    i = src.index('def get_deezer_playlist(')
    route = src[i:src.index('\n@app.route', i)]
    assert 'progress_cb=_emit_progress' in route
    assert f"socketio.emit('{SOCKET_EVENT}'" in route


def test_the_public_playlist_fetch_accepts_a_callback():
    from core.deezer_client import DeezerClient

    sig = inspect.signature(DeezerClient.get_playlist)
    assert 'progress_cb' in sig.parameters
    assert sig.parameters['progress_cb'].default is None, "must stay optional"


def test_the_built_bundle_carries_the_listener():
    """webui/src compiles into static/dist, so editing the source without
    rebuilding ships a bundle that ignores these frames entirely — the React
    listener simply is not in the file the browser loads.

    Skipped when there is no build: ``webui/static/dist`` is gitignored
    (webui/.gitignore:3), so a fresh clone and CI have no bundle at all, and a
    hard failure there would be about the checkout rather than about this code.
    """
    assets = pathlib.Path('webui/static/dist/assets')
    if not assets.is_dir():
        pytest.skip('no built bundle in this checkout (static/dist is gitignored)')
    dist = list(assets.glob('main-*.js'))
    if not dist:
        pytest.skip('no built bundle in this checkout (static/dist is gitignored)')
    assert any(EVENT in p.read_text(encoding='utf-8', errors='ignore') for p in dist), \
        "run `vite build` — the bundle predates the progress listener"
