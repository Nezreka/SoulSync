"""Pin the shared artwork resolution-upgrade + fetch helpers.

Bug (Discord, user report): embedded album art came out ~600×600 while the
cover.jpg in the folder was high-res. Cause: only the cover.jpg path
upgraded the source CDN URL to its highest resolution (Spotify master /
iTunes 3000 / Deezer 1900); the tag-embed path and the "Write Tags to File"
retag path fetched the raw URL — Spotify 640, iTunes 600, Deezer 1000.

Fix: one shared ``_upgrade_art_url`` + ``_fetch_art_bytes`` in
``core.metadata.artwork`` that every art path now calls, so embedded art is
always the same highest resolution as the folder cover.
"""

from __future__ import annotations

import pytest

from core.metadata.artwork import _upgrade_art_url, _fetch_art_bytes


# ---------------------------------------------------------------------------
# _upgrade_art_url — per-source resolution bump
# ---------------------------------------------------------------------------

class TestUpgradeArtUrl:
    def test_spotify_album_art_upgraded_to_master(self):
        # Spotify encodes size as the hex segment after 'ab67616d0000'.
        # 1e02 = 300px, b273 = 640px; 82c1 = the original uploaded master.
        url = 'https://i.scdn.co/image/ab67616d00001e02deadbeef'
        assert _upgrade_art_url(url) == 'https://i.scdn.co/image/ab67616d000082c1deadbeef'

    def test_spotify_640_also_upgraded(self):
        url = 'https://i.scdn.co/image/ab67616d0000b273cafef00d'
        assert _upgrade_art_url(url) == 'https://i.scdn.co/image/ab67616d000082c1cafef00d'

    def test_itunes_600_upgraded_to_3000(self):
        url = 'https://is1-ssl.mzstatic.com/image/thumb/abc/600x600bb.jpg'
        assert _upgrade_art_url(url) == 'https://is1-ssl.mzstatic.com/image/thumb/abc/3000x3000bb.jpg'

    def test_itunes_100_upgraded_to_3000(self):
        url = 'https://is1-ssl.mzstatic.com/image/thumb/abc/100x100bb.jpg'
        assert '3000x3000bb' in _upgrade_art_url(url)

    def test_deezer_upgraded_to_1900(self):
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg'
        assert '1900x1900' in _upgrade_art_url(url)

    def test_caa_thumbnail_upgraded_to_original(self):
        # MusicBrainz art arrives as /front-250 (and -500/-1200) thumbnails;
        # /front is the original full-resolution upload.
        url = 'https://coverartarchive.org/release/abc-123/front-250'
        assert _upgrade_art_url(url) == 'https://coverartarchive.org/release/abc-123/front'

    def test_caa_500_and_release_group_scope_upgraded(self):
        assert _upgrade_art_url('https://coverartarchive.org/release/x/front-500') \
            == 'https://coverartarchive.org/release/x/front'
        assert _upgrade_art_url('https://coverartarchive.org/release-group/y/front-1200') \
            == 'https://coverartarchive.org/release-group/y/front'

    def test_caa_already_original_unchanged(self):
        url = 'https://coverartarchive.org/release/abc/front'
        assert _upgrade_art_url(url) == url

    @pytest.mark.parametrize('url', [
        'https://lastfm.freetls.fastly.net/i/u/770x0/x.jpg',
        'https://example.com/random.jpg',
    ])
    def test_unrecognized_url_unchanged(self, url):
        assert _upgrade_art_url(url) == url

    def test_empty_and_none_unchanged(self):
        assert _upgrade_art_url('') == ''
        assert _upgrade_art_url(None) is None


# ---------------------------------------------------------------------------
# _fetch_art_bytes — upgrade, fetch, fall back to original on CDN refusal
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, data=b'art-bytes', ctype='image/jpeg'):
        self._data = data
        self._ctype = ctype

    def read(self):
        return self._data

    def info(self):
        ctype = self._ctype

        class _Info:
            def get_content_type(_self):
                return ctype

        return _Info()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class TestFetchArtBytes:
    def test_fetches_upgraded_url(self, monkeypatch):
        """The upgraded (high-res) URL is the one actually fetched."""
        calls = []

        def fake_urlopen(url, timeout=None):
            calls.append(url)
            return _FakeResponse(b'big-cover', 'image/png')

        monkeypatch.setattr('core.metadata.artwork.urllib.request.urlopen', fake_urlopen)

        data, mime = _fetch_art_bytes('https://i.scdn.co/image/ab67616d0000b273x')

        assert data == b'big-cover'
        assert mime == 'image/png'
        # Fetched the master-res URL, not the 640 original.
        assert calls == ['https://i.scdn.co/image/ab67616d000082c1x']

    def test_falls_back_to_original_when_upgrade_refused(self, monkeypatch):
        upgraded = 'https://is1-ssl.mzstatic.com/image/thumb/a/3000x3000bb.jpg'
        original = 'https://is1-ssl.mzstatic.com/image/thumb/a/600x600bb.jpg'
        calls = []

        def fake_urlopen(url, timeout=None):
            calls.append(url)
            if url == upgraded:
                raise Exception('403 Forbidden')
            return _FakeResponse(b'orig-cover')

        monkeypatch.setattr('core.metadata.artwork.urllib.request.urlopen', fake_urlopen)

        data, mime = _fetch_art_bytes(original)

        assert data == b'orig-cover'
        assert calls == [upgraded, original]  # tried big first, then fell back

    def test_no_fallback_when_url_not_upgraded(self, monkeypatch):
        """If the upgrade is a no-op (unrecognized URL), a single failed
        fetch returns (None, None) — no pointless retry of the same URL."""
        calls = []

        def fake_urlopen(url, timeout=None):
            calls.append(url)
            raise Exception('network down')

        monkeypatch.setattr('core.metadata.artwork.urllib.request.urlopen', fake_urlopen)

        data, mime = _fetch_art_bytes('https://example.com/cover.jpg')

        assert (data, mime) == (None, None)
        assert calls == ['https://example.com/cover.jpg']

    def test_empty_url_returns_none(self):
        assert _fetch_art_bytes('') == (None, None)
        assert _fetch_art_bytes(None) == (None, None)
