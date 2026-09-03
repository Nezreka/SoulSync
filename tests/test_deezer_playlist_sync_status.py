"""Deezer playlists always read "NEVER SYNCED" (TheHomeGuy).

    "Sync does seem to be working in regards to grabbing the songs and creating
    the playlist. But it does not show it synced or the last time it was in the
    deezer playlist section."

He was right on both halves. The sync ran, the tracks downloaded, the playlist
appeared under Server Playlists — and the Deezer tab still said NEVER SYNCED,
because ``/api/deezer/arl-playlists`` returned a hardcoded literal:

    'sync_status': 'Never Synced',

The comment above it said "Add sync_status field to match Spotify format". It
matched the field's SHAPE and never its value, so no Deezer playlist could ever
report anything else.

The write side was never broken — ``core/discovery/sync.py`` stores status for
every source alike. Only the read was missing.

**The key took two goes to get right, so it is pinned here.** These cards are
shimmed into ``spotifyPlaylists`` with ``id = deezer_arl_<id>``
(``-sync.accounts.ts``), and ``startPlaylistSync`` posts that card id straight
to ``/api/sync/start`` — so the status lands under the PREFIXED id. The bare
``deezer_<id>`` is a DIFFERENT engine: the per-source discovery flow behind
``/api/deezer/sync/start``, which sets ``sync_id_prefix="deezer"``. Reading only
the bare form would have looked correct and fixed nothing.

Not a bug, ruled out while investigating: the ``404``s on
``/api/sync/status/deezer_arl_<id>`` in the log. That endpoint reads
``sync_states``, an in-memory dict of RUNNING syncs, so 404 means "nothing
syncing right now" — which the caller already treats as normal ("no active sync
is the normal case, not an error", account-tabs.tsx).
"""

import re
from datetime import datetime, timedelta

import pytest

# the deezer playlist routes moved out of web_server.py (aug 25 lift)
WEB_SERVER = 'api/source_playlists.py'


def read_source():
    with open(WEB_SERVER, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture(scope='module')
def src():
    return read_source()


@pytest.fixture(scope='module')
def format_status():
    """The real ``_format_playlist_sync_status``, lifted out — it is pure, and
    importing web_server drags the whole app in. it STAYED in web_server.py
    when the deezer routes moved out; the routes get it injected."""
    with open('web_server.py', encoding='utf-8') as handle:
        src = handle.read()
    i = src.index('def _format_playlist_sync_status(')
    ns = {'datetime': datetime}
    exec(src[i:src.index('\ndef ', i + 10)], ns)
    return ns['_format_playlist_sync_status']


@pytest.fixture(scope='module')
def endpoint(src):
    """The body of the ARL playlists route."""
    i = src.index("def get_deezer_arl_playlists():")
    return src[i:src.index('\n@bp.route', i)]


# ── the literal is gone ─────────────────────────────────────────────────────

def test_the_status_is_no_longer_hardcoded(endpoint):
    """The whole bug in one line."""
    assert "'sync_status': 'Never Synced'," not in endpoint


def test_the_status_comes_from_the_shared_status_store(endpoint):
    """Same store every other source reads, rather than a Deezer-only notion of
    what 'synced' means."""
    assert '_load_sync_status_file()' in endpoint
    assert '_format_playlist_sync_status(' in endpoint


# ── the key, which is the part that was easy to get wrong ───────────────────

def test_it_reads_the_prefixed_card_id(endpoint):
    """``deezer_arl_<id>`` is what the ARL cards sync under. Reading only the
    bare ``deezer_<id>`` compiles, runs, and fixes nothing."""
    assert 'deezer_arl_{p[' in endpoint or 'f"deezer_arl_{' in endpoint


def test_it_also_honours_the_other_engines_key(endpoint):
    """Two engines can sync the same playlist: the ARL card path and the
    per-source discovery flow (``sync_id_prefix="deezer"``). A playlist synced
    either way has been synced."""
    assert 'deezer_{p[' in endpoint or 'f"deezer_{' in endpoint


@pytest.mark.parametrize("stored_key,expect_synced", [
    ('deezer_arl_17', True),    # the ARL card path — what the report used
    ('deezer_17', True),        # the discovery flow
    ('deezer_arl_999', False),  # some other playlist
    ('spotify_17', False),      # never let another source's key leak in
])
def test_the_lookup_resolves_the_right_playlist(format_status, stored_key, expect_synced):
    now = datetime.now().isoformat()
    statuses = {stored_key: {'last_synced': now}}
    pid = 17
    info = (statuses.get(f"deezer_arl_{pid}") or statuses.get(f"deezer_{pid}") or {})
    label = format_status(info, None)
    assert label.startswith('Synced') is expect_synced, label


def test_an_unsynced_playlist_still_says_never(format_status):
    assert format_status({}, None) == 'Never Synced'


def test_a_synced_playlist_carries_the_time(format_status):
    """"or the last time it was" — the other half of his report."""
    when = datetime.now() - timedelta(hours=3)
    label = format_status({'last_synced': when.isoformat()}, None)
    assert label.startswith('Synced: ')
    assert when.strftime('%b %d, %H:%M') in label


def test_no_snapshot_is_passed(endpoint):
    """Deezer has no snapshot/etag, so there is nothing to compare against — and
    the Deezer card only renders two states anyway (deezerArlStatusClass has no
    'Needs Sync' arm). Passing a bogus snapshot would invent a third."""
    call = re.search(r'_format_playlist_sync_status\(([^)]*)\)', endpoint).group(1)
    assert call.split(',')[-1].strip() == 'None'
