"""Pin `_normalize_album_type` + the legacy fallback's multi-key
lookup for `album_type`.

Discord report (CAL, 2026-05-12): downloading an artist's discography
with `$albumtype` in the path template put every release under
`Album/` regardless of actual type — EPs, singles, all dumped into
`Album/`. Trace: `_build_album_info_legacy` only checked the
`album_type` key. Different sources expose the type under different
names (Deezer `record_type`, Tidal/MB `type` / `primary-type`, often
uppercase). Spotify-shaped lowercase `album_type` was the only path
that worked; everything else defaulted to `album`.

Fix widens the legacy lookup to check `album_type`, `record_type`,
`type`, `primary-type` and routes the value through
`_normalize_album_type` which lowercases, validates against the
canonical token set, and falls back to `album` for unknowns.

These tests pin both the normalizer (pure helper) and the wired
behavior in `_build_album_info_legacy` (smoke).
"""

from __future__ import annotations

import pytest

from core.metadata.album_tracks import (
    _build_album_info_legacy,
    _normalize_album_type,
)


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


class TestNormalizeAlbumType:
    @pytest.mark.parametrize('raw', ['album', 'ALBUM', 'Album', '  Album  '])
    def test_album_variants_normalize_to_album(self, raw):
        assert _normalize_album_type(raw) == 'album'

    @pytest.mark.parametrize('raw', ['single', 'SINGLE', 'Single'])
    def test_single_variants_normalize_to_single(self, raw):
        assert _normalize_album_type(raw) == 'single'

    @pytest.mark.parametrize('raw', ['ep', 'EP', 'Ep'])
    def test_ep_variants_normalize_to_ep(self, raw):
        assert _normalize_album_type(raw) == 'ep'

    def test_compilation_preserved(self):
        """Spotify exposes 'compilation' as a distinct type. Preserve
        it so users with a `$albumtype` template get a separate folder
        instead of compilations getting demoted into `album/`."""
        assert _normalize_album_type('compilation') == 'compilation'

    @pytest.mark.parametrize('raw', [None, '', '   '])
    def test_empty_inputs_return_default(self, raw):
        assert _normalize_album_type(raw) == 'album'

    def test_unknown_value_returns_default(self):
        """Stray strings (e.g. 'mixtape', 'box-set') don't pass through
        — they'd produce nonsense folder names. Default to album."""
        assert _normalize_album_type('mixtape') == 'album'
        assert _normalize_album_type('box-set') == 'album'

    def test_custom_default_honored(self):
        assert _normalize_album_type('weird', default='single') == 'single'
        assert _normalize_album_type(None, default='ep') == 'ep'

    def test_non_string_value_handled(self):
        """Defensive: source might hand us an int / bool / dict.
        Should not crash."""
        assert _normalize_album_type(0) == 'album'
        assert _normalize_album_type({'name': 'foo'}) == 'album'


# ---------------------------------------------------------------------------
# Legacy builder — alt-key support
# ---------------------------------------------------------------------------


class TestLegacyBuilderAlbumTypeAltKeys:
    """The bug was sources whose `album_type` lives under an alt key.
    Pin each known shape produces the correct canonical token."""

    def _build(self, album_data):
        return _build_album_info_legacy(
            album_data, album_id='id1', album_name='Test', artist_name='Artist',
        )

    def test_spotify_shape_album_type_key(self):
        info = self._build({'album_type': 'single'})
        assert info['album_type'] == 'single'

    def test_deezer_shape_record_type_key(self):
        """Deezer's API returns `record_type` not `album_type`. CAL's
        EPs returned `record_type='ep'` but the legacy reader missed it
        and defaulted to album."""
        info = self._build({'record_type': 'ep'})
        assert info['album_type'] == 'ep'

    def test_tidal_shape_type_key_uppercase(self):
        """Tidal returns `type='ALBUM'/'EP'/'SINGLE'`. Uppercase + alt
        key = double-miss before the fix."""
        info = self._build({'type': 'EP'})
        assert info['album_type'] == 'ep'

    def test_musicbrainz_shape_primary_type_key(self):
        """Some flattened MB shapes carry `primary-type` at the top
        level (typed path handles release-group nesting; legacy hits
        the flattened cases)."""
        info = self._build({'primary-type': 'Single'})
        assert info['album_type'] == 'single'

    def test_album_type_wins_when_multiple_keys_present(self):
        """When both `album_type` AND `record_type` exist, prefer the
        Spotify-canonical key. `_extract_lookup_value` checks left to
        right — pin that ordering."""
        info = self._build({'album_type': 'album', 'record_type': 'ep'})
        assert info['album_type'] == 'album'

    def test_no_type_key_defaults_to_album(self):
        """Source response with no type field at all → defaults to
        `album` (legacy behavior preserved for genuinely-missing data)."""
        info = self._build({'name': 'Some Album'})
        assert info['album_type'] == 'album'

    def test_unknown_type_value_defaults_to_album(self):
        """`type='Mixtape'` → not in canonical set → default. Prevents
        a stray value from poisoning the path template."""
        info = self._build({'type': 'Mixtape'})
        assert info['album_type'] == 'album'
