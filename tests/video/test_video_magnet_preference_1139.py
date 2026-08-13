"""The video side prefers the .torrent URL over the magnet, like the music side.

#1139 was reported against the music album flow, but the same defect sat in the
video grab path: both `prowlarr_search._project`'s caller and `rss_sync` did
``magnet_uri or download_url``, so a magnet won whenever the indexer offered
both. A magnet hands the client an info-hash and nothing else — it has to find
the swarm itself, and one that can't parks on "downloading metadata" with zero
size and zero peers indefinitely. ``add_torrent_smart`` exists to fetch the real
.torrent server-side and push the file (the Sonarr/Radarr handoff) and could
never reach it.

Flipping the preference alone would have traded one failure for another: a
split-container install where SoulSync cannot reach the indexer but the CLIENT
can would lose a magnet that worked. So the magnet is CARRIED — hit dict → API
candidate → grab body → add_torrent_smart's ``fallback_magnet`` — and these
tests pin every link in that chain, because a carrier that silently stops
carrying looks exactly like the bug being fixed.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from core.video import client_grab

_ROOT = Path(__file__).resolve().parents[2]


def _result(**over):
    base = dict(title='Movie 2024 1080p', size=1024, seeders=9, leechers=1, grabs=None,
                protocol='torrent', indexer_name='idx', indexer_id=1, guid='g',
                magnet_uri='magnet:?xt=urn:btih:abc',
                download_url='https://indexer/dl.torrent')
    base.update(over)
    return SimpleNamespace(**base)


# ── the projection carries both links ────────────────────────────────────────

def test_the_hit_carries_the_magnet_beside_the_download_url():
    from core.video.prowlarr_search import _project
    hit = _project(_result(), 'https://indexer/dl.torrent', 'torrent')
    assert hit['download_url'] == 'https://indexer/dl.torrent'
    assert hit['magnet_uri'] == 'magnet:?xt=urn:btih:abc'


def test_a_magnet_only_release_still_projects():
    from core.video.prowlarr_search import _project
    hit = _project(_result(download_url=None), 'magnet:?xt=urn:btih:abc', 'torrent')
    assert hit['download_url'] == 'magnet:?xt=urn:btih:abc'


def test_both_search_paths_prefer_the_torrent_url():
    """Source guard on the two selection sites — the bug was a one-line
    ``or`` in each, and a revert would be just as small."""
    for rel in ('core/video/prowlarr_search.py', 'core/video/rss_sync.py'):
        src = (_ROOT / rel).read_text(encoding='utf-8')
        assert not re.search(r'getattr\(r,\s*"magnet_uri".*\bor\b.*"download_url"', src), (
            f'{rel} still prefers the magnet over the .torrent URL (#1139)'
        )
        assert re.search(r'getattr\(r,\s*"download_url".*\bor\b.*"magnet_uri"', src), rel


# ── the carrier survives every hop ───────────────────────────────────────────

def test_the_api_copies_the_magnet_onto_the_candidate():
    src = (_ROOT / 'api/video/downloads.py').read_text(encoding='utf-8')
    assert '"magnet_uri": hit.get("magnet_uri")' in src, (
        'the search response must carry the magnet or the grab has nothing to '
        'fall back to'
    )


def test_both_grab_call_sites_pass_the_fallback():
    api = (_ROOT / 'api/video/downloads.py').read_text(encoding='utf-8')
    handler = (_ROOT / 'core/automation/handlers/video_process_wishlist.py').read_text(encoding='utf-8')
    assert 'fallback_magnet=body.get("magnet_uri")' in api
    assert 'fallback_magnet=best.get("magnet_uri")' in handler


def test_both_frontend_payloads_send_the_magnet():
    for rel in ('webui/static/video/video-download-view.js', 'webui/static/video/video-grab.js'):
        src = (_ROOT / rel).read_text(encoding='utf-8')
        assert 'payload.magnet_uri' in src, f'{rel} drops the magnet before the grab'


# ── the dispatcher forwards it, and usenet is untouched ──────────────────────

def test_grab_forwards_the_fallback_to_the_torrent_client(monkeypatch):
    seen = {}

    def _fake(url, *, save_path=None, fallback_magnet=None):
        seen.update(url=url, fallback=fallback_magnet)
        return {'ok': True, 'ref': 'h'}

    monkeypatch.setattr(client_grab, 'grab_torrent', _fake)
    client_grab.grab('torrent', 'https://indexer/dl.torrent',
                     fallback_magnet='magnet:?xt=urn:btih:abc')
    assert seen == {'url': 'https://indexer/dl.torrent',
                    'fallback': 'magnet:?xt=urn:btih:abc'}


def test_usenet_ignores_the_torrent_only_fallback(monkeypatch):
    """It rides the shared signature so callers need not branch on source —
    but an NZB has no magnet, and grab_usenet must not be handed one."""
    seen = {}

    def _fake(url, *, save_path=None):
        seen.update(url=url)
        return {'ok': True, 'ref': 'n'}

    monkeypatch.setattr(client_grab, 'grab_usenet', _fake)
    out = client_grab.grab('usenet', 'https://indexer/x.nzb',
                           fallback_magnet='magnet:?xt=urn:btih:abc')
    assert out['ok'] is True and seen == {'url': 'https://indexer/x.nzb'}


def test_grab_torrent_hands_the_fallback_to_add_torrent_smart(monkeypatch):
    seen = {}

    class _Ad:
        def is_configured(self):
            return True

    async def _smart(adapter, url, category='soulsync', save_path=None, fallback_magnet=None):
        seen.update(url=url, fallback=fallback_magnet)
        return 'hash'

    import core.torrent_clients as tc
    import core.torrent_clients.base as base
    monkeypatch.setattr(tc, 'get_active_adapter', lambda: _Ad(), raising=False)
    monkeypatch.setattr(base, 'add_torrent_smart', _smart)

    out = client_grab.grab_torrent('https://indexer/dl.torrent',
                                   fallback_magnet='magnet:?xt=urn:btih:abc')
    assert out['ok'] is True
    assert seen['fallback'] == 'magnet:?xt=urn:btih:abc'
