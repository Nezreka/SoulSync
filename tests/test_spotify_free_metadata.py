"""Tests for core/spotify_free_metadata.py — the no-creds Spotify fallback.

Pure-unit only: the live SpotipyFree calls hit the network and can't run in CI,
but the two things that actually need pinning are pure — the activation gate and
the artist normalizer (the one shape SpotipyFree returns raw). Fixtures below
are trimmed from real captured responses (2026-06).
"""

from __future__ import annotations

from core.spotify_free_metadata import (
    normalize_artist,
    should_block_rate_limited_resume,
    should_offer_spotify_metadata,
    should_use_free_fallback,
    spotify_free_installed,
)


# ---------------------------------------------------------------------------
# should_block_rate_limited_resume — the worker resume guard
# ---------------------------------------------------------------------------

def test_resume_blocked_when_rate_limited_and_nothing_serves():
    # Plain auth, no free: resuming during a ban would just sleep → block.
    assert should_block_rate_limited_resume(rate_limited=True, metadata_available=False) is True


def test_resume_allowed_when_free_can_bridge():
    # Rate-limited but free is available (metadata_available True during a ban
    # because is_spotify_authenticated() is False while banned) → allow resume,
    # the worker bridges via free.
    assert should_block_rate_limited_resume(rate_limited=True, metadata_available=True) is False


def test_resume_never_blocked_when_not_rate_limited():
    assert should_block_rate_limited_resume(rate_limited=False, metadata_available=False) is False
    assert should_block_rate_limited_resume(rate_limited=False, metadata_available=True) is False


# ---------------------------------------------------------------------------
# should_use_free_fallback — the activation gate
# ---------------------------------------------------------------------------

def test_gate_open_when_no_auth():
    assert should_use_free_fallback(authenticated=False, rate_limited=False) is True


def test_gate_open_when_rate_limited_even_if_authed():
    assert should_use_free_fallback(authenticated=True, rate_limited=True) is True


def test_gate_closed_when_authed_and_healthy():
    # The critical guarantee: with working Spotify auth and no rate limit, the
    # free source must NEVER activate.
    assert should_use_free_fallback(authenticated=True, rate_limited=False) is False


def test_gate_open_when_no_auth_and_rate_limited():
    assert should_use_free_fallback(authenticated=False, rate_limited=True) is True


def test_gate_open_when_budget_exhausted_even_if_authed_and_healthy():
    # #758-follow-up: the real-API daily budget is spent, but the user has
    # Spotify Free — switch to the uncapped free source instead of pausing.
    assert should_use_free_fallback(authenticated=True, rate_limited=False,
                                    budget_exhausted=True) is True


def test_gate_closed_when_authed_healthy_and_under_budget():
    assert should_use_free_fallback(authenticated=True, rate_limited=False,
                                    budget_exhausted=False) is False


# ---------------------------------------------------------------------------
# should_offer_spotify_metadata — the availability gate the callers use
# ---------------------------------------------------------------------------

def test_offer_when_authed_even_if_no_free():
    assert should_offer_spotify_metadata(authenticated=True, free_available=False) is True


def test_offer_when_free_available_even_if_no_auth():
    # The whole point: no auth but free available → Spotify source stays usable.
    assert should_offer_spotify_metadata(authenticated=False, free_available=True) is True


def test_not_offered_when_neither():
    assert should_offer_spotify_metadata(authenticated=False, free_available=False) is False


def test_full_composition_authed_healthy_never_uses_free():
    # The critical no-regression guarantee end to end: authed + healthy →
    # offered (official), and the per-request gate stays CLOSED.
    assert should_offer_spotify_metadata(authenticated=True, free_available=True) is True
    assert should_use_free_fallback(authenticated=True, rate_limited=False) is False


def test_spotify_free_installed_returns_bool():
    # Whatever the env, it's a cached bool and doesn't raise / hit network.
    assert isinstance(spotify_free_installed(), bool)


# ---------------------------------------------------------------------------
# normalize_artist — raw web-player GraphQL → Spotify-compatible artist dict
# ---------------------------------------------------------------------------

# Trimmed from a real `SpotipyFree.artist()` response.
_RAW_ARTIST = {
    '__typename': 'Artist',
    'id': '4Z8W4fKeB5YxbusRsdQVPb',
    'uri': 'spotify:artist:4Z8W4fKeB5YxbusRsdQVPb',
    'profile': {'name': 'Radiohead', 'biography': {'text': '...'}},
    'stats': {'followers': 16239336, 'monthlyListeners': 45139421},
    'visuals': {
        'avatarImage': {
            'sources': [
                {'height': 640, 'width': 640, 'url': 'https://i.scdn.co/image/big'},
                {'height': 160, 'width': 160, 'url': 'https://i.scdn.co/image/small'},
            ]
        }
    },
}

# Trimmed from a real `artist_search` item's `data` (no usable image — only
# color swatches under visualIdentity; SoulSync lazy-loads art separately).
_RAW_SEARCH_ARTIST = {
    '__typename': 'Artist',
    'uri': 'spotify:artist:1dfeR4HaWDbWqFHLkxsg1d',
    'profile': {'name': 'Queen'},
    'visualIdentity': {'squareCoverImage': {'extractedColorSet': {}}},
}


def test_normalize_artist_full_shape():
    out = normalize_artist(_RAW_ARTIST)
    assert out['id'] == '4Z8W4fKeB5YxbusRsdQVPb'
    assert out['name'] == 'Radiohead'
    assert out['followers'] == {'total': 16239336}
    assert out['images'][0]['url'] == 'https://i.scdn.co/image/big'
    assert len(out['images']) == 2
    assert out['external_urls'] == {
        'spotify': 'https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb'
    }
    assert out['genres'] == []  # web player doesn't provide genres


def test_normalize_artist_id_from_uri_when_no_id_field():
    out = normalize_artist(_RAW_SEARCH_ARTIST)
    assert out['id'] == '1dfeR4HaWDbWqFHLkxsg1d'
    assert out['name'] == 'Queen'
    assert out['images'] == []  # search items carry no usable image
    assert out['external_urls']['spotify'].endswith('1dfeR4HaWDbWqFHLkxsg1d')


def test_normalize_artist_handles_empty_and_none():
    for bad in (None, {}, {'profile': None, 'stats': None, 'visuals': None}):
        out = normalize_artist(bad)
        assert out['name'] == ''
        assert out['images'] == []
        assert out['followers'] == {'total': 0}
        assert out['external_urls'] == {}


def test_normalize_artist_skips_imageless_sources():
    raw = {'uri': 'spotify:artist:x', 'profile': {'name': 'A'},
           'visuals': {'avatarImage': {'sources': [{'height': 1}, {'url': 'u'}]}}}
    out = normalize_artist(raw)
    assert out['images'] == [{'url': 'u', 'height': None, 'width': None}]


# ---------------------------------------------------------------------------
# SpotifyClient gate model — auto-bridge vs explicit opt-in vs no-surprise
# (constructs the client via __new__ so no network/config init runs)
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402
from core.spotify_client import SpotifyClient  # noqa: E402
import core.spotify_free_metadata as _sfm  # noqa: E402


def _gate(authed, has_creds, selected, installed, rate_limited):
    c = SpotifyClient.__new__(SpotifyClient)
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=authed), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=rate_limited), \
         patch.object(_sfm, 'spotify_free_installed', return_value=installed):
        cm.get_spotify_config.return_value = (
            {'client_id': 'x', 'client_secret': 'y'} if has_creds else {})
        cm.get.side_effect = lambda k, d=None: selected if k == 'metadata.spotify_free' else d
        return c.is_spotify_metadata_available(), c._free_active()


def test_connected_healthy_uses_official():
    avail, free = _gate(authed=True, has_creds=True, selected=False, installed=True, rate_limited=False)
    assert avail is True and free is False  # official; free never opens


def test_plain_spotify_ratelimited_does_NOT_bridge():
    # OPT-IN: a user on plain 'Spotify' (didn't pick Spotify Free) waits out a
    # rate-limit ban — free does NOT auto-bridge for them. No surprise scraping.
    avail, free = _gate(authed=False, has_creds=True, selected=False, installed=True, rate_limited=True)
    assert avail is False and free is False


def test_spotify_free_user_ratelimited_bridges():
    # A user who DID pick Spotify Free and also connected an account: official
    # when healthy, free bridges during a ban.
    avail, free = _gate(authed=False, has_creds=True, selected=True, installed=True, rate_limited=True)
    assert avail is True and free is True


def test_spotify_free_user_healthy_uses_official():
    avail, free = _gate(authed=True, has_creds=True, selected=True, installed=True, rate_limited=False)
    assert avail is True and free is False   # picked Spotify Free but authed -> official


def test_no_auth_picked_spotify_free_serves():
    avail, free = _gate(authed=False, has_creds=False, selected=True, installed=True, rate_limited=False)
    assert avail is True and free is True


def test_no_auth_not_opted_in_does_nothing():
    # The key no-surprise guarantee: a user who never chose Spotify gets no free.
    avail, free = _gate(authed=False, has_creds=False, selected=False, installed=True, rate_limited=False)
    assert avail is False and free is False


def test_selected_but_package_missing_is_graceful():
    avail, free = _gate(authed=False, has_creds=False, selected=True, installed=False, rate_limited=False)
    assert avail is False and free is False


def test_connected_ratelimited_but_no_package_no_bridge():
    avail, free = _gate(authed=False, has_creds=True, selected=False, installed=False, rate_limited=True)
    assert avail is False and free is False


# ── #(free album search): rank + artist-discography workaround ──────────────

from core.spotify_free_metadata import (  # noqa: E402
    rank_albums_by_name, SpotifyFreeMetadataClient,
)


def test_rank_albums_by_name_orders_best_first_and_limits():
    albums = [{'name': 'Random Access Memories'}, {'name': 'GNX'},
              {'name': 'GNX (Deluxe)'}, {'name': 'untitled unmastered.'}]
    out = rank_albums_by_name(albums, 'GNX', limit=2)
    assert [a['name'] for a in out] == ['GNX', 'GNX (Deluxe)']


def test_rank_albums_by_name_handles_empty():
    assert rank_albums_by_name([], 'GNX') == []
    assert rank_albums_by_name(None, 'GNX') == []


def test_search_albums_via_artist_resolves_through_discography():
    c = SpotifyFreeMetadataClient()
    c.search_artists = lambda q, limit=5: [
        {'id': 'art_other', 'name': 'Some Other Band'},
        {'id': 'art_k', 'name': 'Kendrick Lamar'},
    ]
    c.get_artist_albums_list = lambda aid, limit=50: (
        [{'id': 'al1', 'name': 'DAMN.'}, {'id': 'al2', 'name': 'GNX'}]
        if aid == 'art_k' else [])
    out = c.search_albums_via_artist('Kendrick Lamar', 'GNX', limit=3)
    assert out and out[0]['name'] == 'GNX'        # picked the right artist + ranked


def test_search_albums_via_artist_empty_without_artist_or_album():
    c = SpotifyFreeMetadataClient()
    c.search_artists = lambda q, limit=5: [{'id': 'a', 'name': 'X'}]
    assert c.search_albums_via_artist('', 'GNX') == []
    assert c.search_albums_via_artist('X', '') == []


def test_search_albums_via_artist_empty_when_no_artist_match():
    c = SpotifyFreeMetadataClient()
    c.search_artists = lambda q, limit=5: []      # nothing found
    assert c.search_albums_via_artist('Nobody', 'GNX') == []


def test_client_search_albums_uses_free_via_artist_when_active():
    """SpotifyClient.search_albums bridges album matching through Spotify Free
    (artist discography) when free is active, instead of dropping to iTunes/Deezer."""
    c = SpotifyClient.__new__(SpotifyClient)
    fake_free = SpotifyFreeMetadataClient()
    fake_free.search_albums_via_artist = lambda artist, album, limit: [
        {'id': 'al2', 'name': 'GNX',
         'artists': [{'name': 'Kendrick Lamar', 'id': 'art_k'}]}
    ]
    c._free_meta_client = fake_free
    fake_cache = type('C', (), {'get_search_results': lambda *a, **k: None})()

    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=False), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=False), \
         patch('core.spotify_client.get_metadata_cache', return_value=fake_cache), \
         patch.object(_sfm, 'spotify_free_installed', return_value=True):
        cm.get_spotify_config.return_value = {}
        cm.get.side_effect = lambda k, d=None: True if k == 'metadata.spotify_free' else d
        results = c.search_albums('Kendrick Lamar GNX', limit=5,
                                  artist='Kendrick Lamar', album='GNX')

    assert len(results) == 1 and results[0].name == 'GNX'
    assert results[0].id == 'al2'


# ── prefer-free (enrichment opt-in) + the use_spotify root-cause fix ─────────

def _free_active_with(prefer_free, installed, authed=True, rate_limited=False,
                      selected=False, budget=False):
    c = SpotifyClient.__new__(SpotifyClient)
    if prefer_free:
        c._prefer_free = True
    if budget:
        c._budget_exhausted_use_free = True
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=authed), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=rate_limited), \
         patch.object(_sfm, 'spotify_free_installed', return_value=installed):
        cm.get.side_effect = lambda k, d=None: selected if k == 'metadata.spotify_free' else d
        return c._free_active()


def test_prefer_free_activates_even_when_authed_healthy_under_budget():
    # The enrichment opt-in: free serves even though official is perfectly usable.
    assert _free_active_with(prefer_free=True, installed=True) is True


def test_prefer_free_inert_without_package():
    # Graceful: opt-in set but SpotipyFree missing -> stays on official.
    assert _free_active_with(prefer_free=True, installed=False) is False


def test_no_prefer_free_authed_healthy_stays_official():
    assert _free_active_with(prefer_free=False, installed=True, selected=True) is False


def _client_that_forbids_official(prefer_free=False, budget=False):
    """A SpotifyClient whose official .sp blows up if touched + a Free client that
    serves albums — so a passing search proves official was skipped."""
    c = SpotifyClient.__new__(SpotifyClient)
    if prefer_free:
        c._prefer_free = True
    if budget:
        c._budget_exhausted_use_free = True

    class _Sp:
        def search(self, *a, **k):
            raise AssertionError("official Spotify API must not be called when deferring to Free")
    c.sp = _Sp()
    fake_free = SpotifyFreeMetadataClient()
    fake_free.search_albums_via_artist = lambda artist, album, limit: [
        {'id': 'al2', 'name': 'GNX', 'artists': [{'name': 'Kendrick Lamar', 'id': 'art_k'}]}]
    c._free_meta_client = fake_free
    return c


def test_search_albums_prefers_free_when_authed_and_prefer_free_set():
    """Root-cause regression: prefer_free makes an AUTHED, healthy client defer to
    Spotify Free instead of the official API. Previously the use_spotify gate
    (= is_spotify_authenticated()) ignored _free_active() and hit official."""
    c = _client_that_forbids_official(prefer_free=True)
    fake_cache = type('C', (), {'get_search_results': lambda *a, **k: None})()
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=True), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=False), \
         patch('core.spotify_client.get_metadata_cache', return_value=fake_cache), \
         patch.object(_sfm, 'spotify_free_installed', return_value=True):
        cm.get_spotify_config.return_value = {'client_id': 'x', 'client_secret': 'y'}
        cm.get.side_effect = lambda k, d=None: d
        results = c.search_albums('Kendrick Lamar GNX', limit=5,
                                  artist='Kendrick Lamar', album='GNX')
    assert len(results) == 1 and results[0].id == 'al2'   # Free served; official untouched


def test_search_albums_diverts_to_free_when_budget_exhausted_and_authed():
    """Budget→Free bridge regression: an AUTHED client that has spent the daily
    budget defers to Free instead of hammering the official API (which the budget
    exists to protect). This is the divert that previously never happened."""
    c = _client_that_forbids_official(budget=True)
    fake_cache = type('C', (), {'get_search_results': lambda *a, **k: None})()
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=True), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=False), \
         patch('core.spotify_client.get_metadata_cache', return_value=fake_cache), \
         patch.object(_sfm, 'spotify_free_installed', return_value=True):
        cm.get_spotify_config.return_value = {'client_id': 'x', 'client_secret': 'y'}
        # metadata.spotify_free=True so the budget path's _free_available() holds
        cm.get.side_effect = lambda k, d=None: True if k == 'metadata.spotify_free' else d
        results = c.search_albums('Kendrick Lamar GNX', limit=5,
                                  artist='Kendrick Lamar', album='GNX')
    assert len(results) == 1 and results[0].id == 'al2'


# ── default-ON enrichment: prefer_free makes metadata available to the worker ──

def _metadata_available(prefer_free, installed, authed=False, selected=False):
    c = SpotifyClient.__new__(SpotifyClient)
    if prefer_free:
        c._prefer_free = True
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=authed), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=False), \
         patch.object(_sfm, 'spotify_free_installed', return_value=installed):
        cm.get.side_effect = lambda k, d=None: selected if k == 'metadata.spotify_free' else d
        return c.is_spotify_metadata_available()


def test_prefer_free_makes_metadata_available_without_auth_or_source():
    # Default-ON enrichment: the worker runs via the no-auth path on the toggle
    # alone — no account connected, no 'no-auth Spotify' source selected.
    assert _metadata_available(prefer_free=True, installed=True) is True


def test_prefer_free_metadata_unavailable_without_package():
    assert _metadata_available(prefer_free=True, installed=False) is False


def test_interactive_metadata_availability_unaffected_by_prefer_free():
    # A client WITHOUT _prefer_free (interactive/global): no auth + no source -> unavailable.
    assert _metadata_available(prefer_free=False, installed=True) is False
    # ...and authed is available as before.
    assert _metadata_available(prefer_free=False, installed=True, authed=True) is True


# ── (spotify-free) fall back to Free when the AUTHED official search returns empty ──
from unittest.mock import MagicMock  # noqa: E402
import core.spotify_client as _sc  # noqa: E402


def _search_client(official_items, free_result, *, free_available, monkeypatch):
    c = SpotifyClient.__new__(SpotifyClient)
    c.sp = MagicMock()
    c.sp.search.return_value = {'tracks': {'items': official_items}}
    c._free_meta_client = MagicMock()
    c._free_meta_client.search_tracks.return_value = free_result
    fallback = MagicMock()
    fallback.search_tracks.return_value = ['ITUNES']
    monkeypatch.setattr(SpotifyClient, '_fallback', fallback)             # property → class-patch
    monkeypatch.setattr(SpotifyClient, '_fallback_source', 'itunes')     # property → class-patch
    monkeypatch.setattr(SpotifyClient, 'is_spotify_authenticated', lambda self: True)
    monkeypatch.setattr(SpotifyClient, '_free_active', lambda self: False)      # authed + healthy
    monkeypatch.setattr(SpotifyClient, '_free_available', lambda self: free_available)
    monkeypatch.setattr(_sc, '_is_globally_rate_limited', lambda: True)         # bypass decorator retry loop
    cache = MagicMock()
    cache.get_search_results.return_value = None
    monkeypatch.setattr(_sc, 'get_metadata_cache', lambda: cache)
    monkeypatch.setattr(_sc.Track, 'from_spotify_track', staticmethod(lambda t: ('T', t.get('id'))))
    return c


def test_authed_official_empty_falls_back_to_free_when_opted_in(monkeypatch):
    # The reported case: authed but official returns nothing; with Free opted in it
    # must serve Free results instead of a blank page.
    c = _search_client([], [{'id': 'free1'}], free_available=True, monkeypatch=monkeypatch)
    assert c.search_tracks('q', limit=5) == [('T', 'free1')]
    c._fallback.search_tracks.assert_not_called()          # Free won, not iTunes


def test_authed_official_empty_uses_itunes_when_free_not_opted_in(monkeypatch):
    # Without the opt-in, no unofficial scraping — falls to iTunes as before.
    c = _search_client([], [{'id': 'free1'}], free_available=False, monkeypatch=monkeypatch)
    assert c.search_tracks('q', limit=5) == ['ITUNES']
    c._free_meta_client.search_tracks.assert_not_called()


def test_authed_official_nonempty_never_touches_free(monkeypatch):
    # A working official account is unaffected — Free/iTunes never consulted.
    c = _search_client([{'id': 'off1'}], [{'id': 'free1'}], free_available=True, monkeypatch=monkeypatch)
    assert c.search_tracks('q', limit=5) == [('T', 'off1')]
    c._free_meta_client.search_tracks.assert_not_called()
    c._fallback.search_tracks.assert_not_called()


def test_authed_premium_required_artist_lookup_falls_back_to_free(monkeypatch):
    class PremiumRequired(Exception):
        http_status = 403

        def __str__(self):
            return "Active premium subscription required for the owner of the app"

    c = SpotifyClient.__new__(SpotifyClient)
    c.sp = MagicMock()
    c.sp.artist.side_effect = PremiumRequired()
    c._free_meta_client = MagicMock()
    c._free_meta_client.get_artist.return_value = {
        'id': 'sp-artist',
        'name': 'Free Artist',
        'images': [{'url': 'https://img.example/free.jpg'}],
        'followers': {'total': 99},
        'genres': [],
    }
    cache = MagicMock()
    cache.get_entity.return_value = None
    monkeypatch.setattr(SpotifyClient, '_fallback_source', 'itunes')
    monkeypatch.setattr(SpotifyClient, 'is_spotify_authenticated', lambda self: True)
    monkeypatch.setattr(SpotifyClient, '_free_active', lambda self: False)
    monkeypatch.setattr(SpotifyClient, '_free_available', lambda self: True)
    monkeypatch.setattr(_sc, '_is_globally_rate_limited', lambda: True)
    monkeypatch.setattr(_sc, 'get_metadata_cache', lambda: cache)

    artist = c.get_artist('sp-artist')

    assert artist['name'] == 'Free Artist'
    c._free_meta_client.get_artist.assert_called_once_with('sp-artist')


def test_prefer_free_serves_when_not_authed_and_not_opted_in(monkeypatch):
    # Boulder's case: no auth, and 'Spotify Free' isn't the chosen metadata source
    # (free_available=False), so the normal gates are all shut. An explicit Spotify
    # search pick passes prefer_free=True; with the package installed it must take
    # the no-creds path. Differential: SAME client, only the flag changes.
    c = _search_client([], [{'id': 'free1'}], free_available=False, monkeypatch=monkeypatch)
    monkeypatch.setattr(SpotifyClient, 'is_spotify_authenticated', lambda self: False)
    monkeypatch.setattr(SpotifyClient, '_free_installed', lambda self: True)

    # Without the flag → no unofficial scraping, falls to iTunes exactly as before.
    assert c.search_tracks('q', limit=5) == ['ITUNES']
    c._free_meta_client.search_tracks.assert_not_called()

    # With the flag (the explicit pick) → Free serves instead of a blank page.
    c._free_meta_client.search_tracks.reset_mock()
    assert c.search_tracks('q', limit=5, prefer_free=True) == [('T', 'free1')]
    c._free_meta_client.search_tracks.assert_called_once()


def test_prefer_free_inert_when_package_not_installed(monkeypatch):
    # prefer_free must not conjure free out of nothing: package missing → it stays
    # off and falls to iTunes (the orchestrator also guards on _free_installed, but
    # the client is defensive too).
    c = _search_client([], [{'id': 'free1'}], free_available=False, monkeypatch=monkeypatch)
    monkeypatch.setattr(SpotifyClient, 'is_spotify_authenticated', lambda self: False)
    monkeypatch.setattr(SpotifyClient, '_free_installed', lambda self: False)
    assert c.search_tracks('q', limit=5, prefer_free=True) == ['ITUNES']
    c._free_meta_client.search_tracks.assert_not_called()


# ── exact-source lookups must serve via free (the wrong-tracklist bug) ────────
#
# The exact-source metadata layer (core/metadata/album_tracks.py's source chain,
# the strict discography providers) calls the entity lookups with
# allow_fallback=False — "this Spotify id, this catalog, don't switch sources."
# The free branches used to be gated on allow_fallback, so a 'Spotify (no auth)'
# user got None from the spotify hop and the chain fell through to Deezer/iTunes
# WITH THE SAME SPOTIFY ID — an id-space collision that served a completely
# unrelated release's tracklist (Katy Perry EP → 10-track trance compilation).
# Free IS Spotify (same catalog, same base-62 ids): it must serve regardless of
# allow_fallback.

def _entity_client(fake_free):
    c = SpotifyClient.__new__(SpotifyClient)
    c._free_meta_client = fake_free
    fake_cache = type('C', (), {
        'get_entity': lambda *a, **k: None,
        'store_entity': lambda *a, **k: None,
    })()
    ctx = (
        patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=False),
        patch('core.spotify_client.config_manager'),
        patch('core.spotify_client._is_globally_rate_limited', return_value=False),
        patch('core.spotify_client.get_metadata_cache', return_value=fake_cache),
        patch.object(_sfm, 'spotify_free_installed', return_value=True),
    )
    return c, ctx


def _enter_all(ctx):
    entered = [c.__enter__() for c in ctx]
    cm = entered[1]
    cm.get_spotify_config.return_value = {}
    cm.get.side_effect = lambda k, d=None: True if k == 'metadata.spotify_free' else d
    return entered


def _exit_all(ctx):
    for c in reversed(ctx):
        c.__exit__(None, None, None)


def test_get_album_exact_source_serves_via_free():
    free = SpotifyFreeMetadataClient()
    free.get_album = lambda album_id: {'id': album_id, 'name': "WOMAN'S WORLD",
                                       'tracks': {'items': [{'name': "WOMAN'S WORLD"}]}}
    c, ctx = _entity_client(free)
    _enter_all(ctx)
    try:
        album = c.get_album('4iVb0hSCVvBLKeCTLlBRq6', allow_fallback=False)
    finally:
        _exit_all(ctx)
    assert album is not None and album['name'] == "WOMAN'S WORLD"


def test_get_album_tracks_exact_source_serves_via_free():
    free = SpotifyFreeMetadataClient()
    free.get_album_tracks = lambda album_id: {'items': [{'name': "WOMAN'S WORLD"}]}
    c, ctx = _entity_client(free)
    _enter_all(ctx)
    try:
        tracks = c.get_album_tracks('4iVb0hSCVvBLKeCTLlBRq6', allow_fallback=False)
    finally:
        _exit_all(ctx)
    assert tracks is not None and tracks['items'][0]['name'] == "WOMAN'S WORLD"


def test_get_album_no_free_no_auth_exact_source_still_none():
    """Plain-spotify user with no token: exact-source lookup stays None — the
    ungating must not conjure data when free isn't selected."""
    free = SpotifyFreeMetadataClient()
    free.get_album = lambda album_id: {'id': album_id, 'name': 'SHOULD NOT SERVE'}
    c, ctx = _entity_client(free)
    entered = _enter_all(ctx)
    entered[1].get.side_effect = lambda k, d=None: d  # spotify_free NOT selected
    try:
        album = c.get_album('4iVb0hSCVvBLKeCTLlBRq6', allow_fallback=False)
    finally:
        _exit_all(ctx)
    assert album is None


# ── the vanished singles (artist discography sections) ───────────────────────

def test_get_artist_albums_list_fetches_singles_and_retags_types():
    """One upstream call serves the whole listing: the raw discography payload
    already carries id/name/date/art/count for all three sections. The old
    path (SpotipyFree.artist_albums) re-scraped every release individually —
    minutes for a big artist — and only ever fetched the albums section with
    everything typed 'album'."""
    def _release(rid, name, year=2024, count=10):
        return {'id': rid, 'uri': f'spotify:album:{rid}', 'name': name,
                'date': {'year': year},
                'coverArt': {'sources': [{'url': f'https://img/{rid}.jpg'}]},
                'tracks': {'totalCount': count}}

    discog = {
        'albums': {'items': [{'releases': {'items': [_release('alb1', 'Album One')]}}]},
        'singles': {'items': [{'releases': {'items': [_release('sgl1', "WOMAN'S WORLD", count=1)]}}]},
        'compilations': {'items': []},
    }
    client = SpotifyFreeMetadataClient()
    client._fetch_discography_sections = lambda artist_id: discog
    out = client.get_artist_albums_list('artist123')

    by_id = {a['id']: a for a in out}
    assert by_id['sgl1']['album_type'] == 'single'   # the vanished single, correctly typed
    assert by_id['alb1']['album_type'] == 'album'
    assert by_id['sgl1']['total_tracks'] == 1
    assert by_id['alb1']['release_date'] == '2024'
    assert by_id['alb1']['images'][0]['url'] == 'https://img/alb1.jpg'


def test_get_artist_albums_list_survives_upstream_failure():
    client = SpotifyFreeMetadataClient()
    def _boom(artist_id):
        raise RuntimeError('scrape died')
    client._fetch_discography_sections = _boom
    assert client.get_artist_albums_list('artist123') == []


# ── explicit-Spotify requests engage free without the persistent opt-in ──────
#
# Boulder's rule: clicking Spotify IS asking for Spotify. A user with
# metadata source = Deezer and no working auth used to have their 'spotify'
# searches silently served by the iTunes fallback, and artist pages had no
# Spotify catalog at all. Interactive endpoints that explicitly target
# spotify now set g._spotify_free_ok on the Flask request; _free_wanted
# honors it FOR THAT REQUEST ONLY. No request context (enrichment, watchlist,
# background threads) → unchanged opt-in behavior.

def _flask_ctx(flag):
    from flask import Flask, g
    app = Flask(__name__)
    ctx = app.test_request_context('/')
    ctx.push()
    if flag:
        g._spotify_free_ok = True
    return ctx


def test_request_scoped_opt_in_engages_free_wanted(monkeypatch):
    from core.spotify_client import SpotifyClient
    c = SpotifyClient.__new__(SpotifyClient)
    with patch('core.spotify_client.config_manager') as cm:
        cm.get.side_effect = lambda k, d=None: d  # spotify_free NOT selected
        assert c._free_wanted() is False          # outside any request
        ctx = _flask_ctx(flag=False)
        try:
            assert c._free_wanted() is False      # request without the marker
        finally:
            ctx.pop()
        ctx = _flask_ctx(flag=True)
        try:
            assert c._free_wanted() is True       # explicitly-Spotify request
        finally:
            ctx.pop()


def test_request_scoped_opt_in_makes_metadata_available(monkeypatch):
    """The whole downstream stack keys off is_spotify_metadata_available —
    with the request marker set, the resolver hands out the client and the
    strict discography serves via free instead of 'provider is unavailable'."""
    from core.spotify_client import SpotifyClient
    c = SpotifyClient.__new__(SpotifyClient)
    ctx = _flask_ctx(flag=True)
    try:
        with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=False), \
             patch('core.spotify_client.config_manager') as cm, \
             patch.object(_sfm, 'spotify_free_installed', return_value=True):
            cm.get.side_effect = lambda k, d=None: d  # NOT selected persistently
            assert c.is_spotify_metadata_available() is True
    finally:
        ctx.pop()


def test_endpoint_marker_only_fires_for_spotify():
    import web_server
    from flask import g
    ctx = _flask_ctx(flag=False)
    try:
        web_server._mark_request_free_ok_for_spotify('deezer')
        assert not getattr(g, '_spotify_free_ok', False)
        web_server._mark_request_free_ok_for_spotify('spotify')
        assert g._spotify_free_ok is True
    finally:
        ctx.pop()
