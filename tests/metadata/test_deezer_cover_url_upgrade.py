"""Pin the Deezer CDN cover-URL upgrade helper.

Discord report (Tim, 2026-05-XX): downloaded cover art via Deezer
metadata source comes out blurry — visibly low-res in Navidrome.
Cause: Deezer's API returns ``cover_xl`` URLs at 1000×1000 but the
underlying CDN serves up to 1900×1900 by rewriting the size segment
in the URL path. SoulSync wasn't doing the rewrite.

Helper: ``_upgrade_deezer_cover_url(url, target_size=1900)`` — pure
function, lifts to one boundary so cover-download sites don't each
re-implement the regex. Tests pin every input shape:

- Standard Deezer URL → upgraded to target
- Non-Deezer URL → returned unchanged
- Already at/above target → returned unchanged (no needless rewrite)
- Empty / None → returned as-is
- Custom target → applied correctly
- Picture URLs (artist) — same path pattern, also upgraded
"""

from __future__ import annotations

import pytest

from core.deezer_client import _upgrade_deezer_cover_url


# ---------------------------------------------------------------------------
# Standard upgrade — the headline case
# ---------------------------------------------------------------------------


class TestUpgradeStandardDeezerUrl:
    def test_default_target_1900(self):
        url = 'https://cdn-images.dzcdn.net/images/cover/abc123/1000x1000-000000-80-0-0.jpg'
        upgraded = _upgrade_deezer_cover_url(url)
        assert upgraded == 'https://cdn-images.dzcdn.net/images/cover/abc123/1900x1900-000000-80-0-0.jpg'

    def test_alternate_dzcdn_host(self):
        """Both `cdn-images.dzcdn.net` and `e-cdns-images.dzcdn.net`
        are valid Deezer CDN hosts. Helper must catch both."""
        url = 'https://e-cdns-images.dzcdn.net/images/cover/xyz/1000x1000-000000-80-0-0.jpg'
        upgraded = _upgrade_deezer_cover_url(url)
        assert '1900x1900' in upgraded
        assert upgraded.startswith('https://e-cdns-images.dzcdn.net/')

    def test_artist_picture_url_also_upgrades(self):
        """Artist `picture_xl` URLs follow the same `/SIZExSIZE-` path
        pattern and the same CDN. Same upgrade applies."""
        url = 'https://cdn-images.dzcdn.net/images/artist/hash/1000x1000-000000-80-0-0.jpg'
        upgraded = _upgrade_deezer_cover_url(url)
        assert '1900x1900' in upgraded

    def test_500x500_upgrades(self):
        """Some albums on Deezer only have cover_big (500×500). Helper
        upgrades anything below target, not just 1000×1000."""
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/500x500-000000-80-0-0.jpg'
        upgraded = _upgrade_deezer_cover_url(url)
        assert '1900x1900' in upgraded


# ---------------------------------------------------------------------------
# Custom target size
# ---------------------------------------------------------------------------


class TestCustomTargetSize:
    def test_smaller_target(self):
        """Caller can request a smaller size for bandwidth-sensitive
        cases (mobile, thumbnails, etc.)."""
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg'
        upgraded = _upgrade_deezer_cover_url(url, target_size=600)
        # 1000 already > 600, so this is a no-op — never DOWNGRADE.
        assert upgraded == url

    def test_larger_target_works(self):
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/250x250-000000-80-0-0.jpg'
        upgraded = _upgrade_deezer_cover_url(url, target_size=1400)
        assert '1400x1400' in upgraded


# ---------------------------------------------------------------------------
# Already-upgraded URLs — no needless rewrite
# ---------------------------------------------------------------------------


class TestAlreadyUpgraded:
    def test_already_at_target_returned_unchanged(self):
        """Re-running the upgrade on an already-upgraded URL should
        be a no-op. Idempotent — important for cached URLs that may
        have been rewritten by a previous SoulSync version."""
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/1900x1900-000000-80-0-0.jpg'
        assert _upgrade_deezer_cover_url(url) == url

    def test_above_target_returned_unchanged(self):
        """Defensive: if the URL is somehow LARGER than target, don't
        downgrade. Cached URL from a future bigger-target setting,
        manual edits, etc."""
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/3000x3000-000000-80-0-0.jpg'
        assert _upgrade_deezer_cover_url(url) == url


# ---------------------------------------------------------------------------
# Defensive — non-Deezer URLs left untouched
# ---------------------------------------------------------------------------


class TestNonDeezerUrls:
    @pytest.mark.parametrize('url', [
        'https://i.scdn.co/image/spotify-id-thing',           # Spotify
        'https://is4-ssl.mzstatic.com/image/100x100bb.jpg',   # iTunes
        'https://coverartarchive.org/release/abc/front',      # MB CAA
        'https://lastfm.freetls.fastly.net/i/u/770x0/abc.jpg', # Last.fm
        'https://example.com/random.jpg',                     # Random
    ])
    def test_non_dzcdn_returned_unchanged(self, url):
        """Helper must NOT touch non-Deezer URLs. Mirrors the
        defensive check pattern the iTunes and Spotify upgrade
        helpers use."""
        assert _upgrade_deezer_cover_url(url) == url

    def test_dzcdn_url_without_size_segment_returned_unchanged(self):
        """Defensive: if Deezer ever changes URL format, don't crash
        — return as-is and let the download attempt happen with the
        original URL."""
        url = 'https://cdn-images.dzcdn.net/images/cover/abc/some-other-format.jpg'
        assert _upgrade_deezer_cover_url(url) == url


# ---------------------------------------------------------------------------
# Empty / None inputs
# ---------------------------------------------------------------------------


class TestEmptyInputs:
    def test_empty_string(self):
        assert _upgrade_deezer_cover_url('') == ''

    def test_none(self):
        assert _upgrade_deezer_cover_url(None) is None


# ---------------------------------------------------------------------------
# Download fallback — if upgraded URL 403s, retry with original
# ---------------------------------------------------------------------------


class TestDownloadFallbackOnCdnRefusal:
    """If Deezer CDN refuses the upgraded 1900×1900 URL for some
    specific album (rare but possible — empirically tested 4 albums
    and none hit this, but defending the edge keeps the fix
    strictly non-regressive vs. pre-upgrade behaviour)."""

    def test_tag_writer_retries_with_original_on_failure(self, monkeypatch):
        """tag_writer.download_cover_art must fall back to the
        original URL when the upgraded URL fails."""
        from core import tag_writer

        original_url = 'https://cdn-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg'
        upgraded_url = 'https://cdn-images.dzcdn.net/images/cover/abc/1900x1900-000000-80-0-0.jpg'

        call_log = []

        class _FakeResponse:
            def read(self): return b'cover-bytes'
            def info(self):
                class _Info:
                    def get_content_type(_self): return 'image/jpeg'
                return _Info()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(url, timeout=None):
            call_log.append(url)
            if url == upgraded_url:
                raise Exception("403 Forbidden")
            return _FakeResponse()

        monkeypatch.setattr('core.tag_writer.urllib.request.urlopen', fake_urlopen)

        result = tag_writer.download_cover_art(original_url)

        assert result == (b'cover-bytes', 'image/jpeg')
        # Tried upgraded first, then fell back to original
        assert call_log == [upgraded_url, original_url]

    def test_tag_writer_no_fallback_for_non_dzcdn_url(self, monkeypatch):
        """Non-Deezer URLs go through unchanged — no upgrade, no
        fallback. Fast path preserved."""
        from core import tag_writer

        spotify_url = 'https://i.scdn.co/image/abc'
        call_log = []

        def fake_urlopen(url, timeout=None):
            call_log.append(url)
            raise Exception("network error")

        monkeypatch.setattr('core.tag_writer.urllib.request.urlopen', fake_urlopen)

        result = tag_writer.download_cover_art(spotify_url)

        assert result is None
        # Single attempt — no Deezer fallback path triggered
        assert call_log == [spotify_url]
