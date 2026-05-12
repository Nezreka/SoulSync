"""Pin track-level filters used by the Download Discography endpoint.

GitHub issue #559 (trackhacs): "Download Discography" on an artist
pulled in tracks where the artist's name appeared in the title of
someone else's song. Two failure modes:

1. Cross-artist tracks — compilations / appears_on albums brought in
   tracks by unrelated artists. Fixed by `track_artist_matches`.
2. Remix / live / acoustic / instrumental versions never honored the
   watchlist content-type filters for one-off discography downloads.
   Fixed by `content_type_skip_reason`.

These helpers live in ``core.metadata.discography_filters``. Tests pin
behavior at the function boundary so the wiring inside
``web_server.download_discography`` doesn't need an endpoint test to
catch a filter regression.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.metadata.discography_filters import (
    content_type_skip_reason,
    load_global_content_filter_settings,
    track_artist_matches,
)


# ---------------------------------------------------------------------------
# track_artist_matches
# ---------------------------------------------------------------------------


class TestTrackArtistMatches:
    def test_primary_artist_matches(self):
        """When the requested artist is the track's primary artist,
        match. This is the most common case — non-feature tracks on an
        artist's own album."""
        assert track_artist_matches(['Drake', 'Future'], 'Drake') is True

    def test_featured_artist_matches(self):
        """When the requested artist appears as a feature (anywhere in
        the list, not just position 0), still match. Keeping feature
        appearances is intentional — they're legit discography entries."""
        assert track_artist_matches(['Lil Wayne', 'Drake', 'Kanye West'], 'Drake') is True

    def test_unrelated_artist_drops(self):
        """The bug case: a compilation track by an unrelated artist
        that just mentions the requested artist in the title. The
        artists list contains only the actual performer(s); filter
        drops it."""
        assert track_artist_matches(['Random Artist'], 'Drake') is False

    def test_match_is_case_insensitive(self):
        """Source data can be cased inconsistently across providers."""
        assert track_artist_matches(['drake'], 'Drake') is True
        assert track_artist_matches(['DRAKE'], 'Drake') is True
        assert track_artist_matches(['Drake'], 'drake') is True

    def test_match_handles_whitespace_padding(self):
        """Trailing whitespace in either side mustn't break the match."""
        assert track_artist_matches(['  Drake  '], 'Drake') is True
        assert track_artist_matches(['Drake'], '  Drake  ') is True

    def test_empty_artists_list_drops(self):
        """No artists on the track → can't be by anyone → drop."""
        assert track_artist_matches([], 'Drake') is False
        assert track_artist_matches(None, 'Drake') is False

    def test_empty_requested_artist_keeps(self):
        """Defensive: if the caller forgot to pass the requested artist,
        don't drop every track — let the caller's other filters decide.
        Better to keep too much than to silently drop everything."""
        assert track_artist_matches(['Drake'], '') is True
        assert track_artist_matches(['Drake'], '   ') is True
        assert track_artist_matches(['Drake'], None) is True

    def test_accepts_list_of_dicts_shape(self):
        """Some upstreams pass `[{'name': 'Drake', 'id': '...'}]`
        directly instead of the normalized list-of-strings. Helper
        must handle both — easier than forcing a normalization step
        at the call site."""
        assert track_artist_matches([{'name': 'Drake'}], 'Drake') is True
        assert track_artist_matches([{'name': 'Random'}], 'Drake') is False

    def test_substring_does_not_match(self):
        """A song by "Drake & Future" should not match "Drake" via
        substring — that's exactly the false-positive case the bug
        report describes. Exact full-name match only."""
        assert track_artist_matches(['Drake & Future'], 'Drake') is False
        assert track_artist_matches(['Drakeo the Ruler'], 'Drake') is False


# ---------------------------------------------------------------------------
# content_type_skip_reason
# ---------------------------------------------------------------------------


_ALL_OFF = {
    'include_live': False,
    'include_remixes': False,
    'include_acoustic': False,
    'include_instrumentals': False,
}

_ALL_ON = {
    'include_live': True,
    'include_remixes': True,
    'include_acoustic': True,
    'include_instrumentals': True,
}


class TestContentTypeSkipReason:
    def test_returns_none_for_plain_track(self):
        """Default settings exclude all four content types, but a plain
        original studio track shouldn't trigger any of them."""
        assert content_type_skip_reason('Hotline Bling', 'Views', _ALL_OFF) is None

    def test_remix_skipped_when_excluded(self):
        """Default: remixes off → "(Remix)" track gets skipped with
        reason 'remix'."""
        assert content_type_skip_reason('Hotline Bling (Remix)', 'Views', _ALL_OFF) == 'remix'

    def test_remix_kept_when_included(self):
        """When the user opts in via include_remixes, the same track
        passes through."""
        assert content_type_skip_reason('Hotline Bling (Remix)', 'Views', _ALL_ON) is None

    def test_live_skipped_when_excluded(self):
        assert content_type_skip_reason('Hotline Bling (Live)', 'Views', _ALL_OFF) == 'live'

    def test_acoustic_skipped_when_excluded(self):
        assert content_type_skip_reason('Hotline Bling (Acoustic)', 'Views', _ALL_OFF) == 'acoustic'

    def test_instrumental_skipped_when_excluded(self):
        assert content_type_skip_reason('Hotline Bling (Instrumental)', 'Views', _ALL_OFF) == 'instrumental'

    def test_first_match_wins(self):
        """If a track somehow matches multiple categories (e.g. a live
        remix), it's reported under the first one checked. Order is
        live → remix → acoustic → instrumental. Stable for telemetry
        and for the user-facing skip-counter aggregation."""
        # "Live Remix" — both live and remix patterns fire. Live first.
        reason = content_type_skip_reason('Hotline Bling (Live Remix)', 'Views', _ALL_OFF)
        assert reason == 'live'

    def test_settings_missing_keys_default_to_exclude(self):
        """Defensive: caller passes an empty dict / partial dict.
        Missing keys treated as False (exclude) — same as the watchlist
        scanner contract. A remix passed with `{}` still gets skipped."""
        assert content_type_skip_reason('Track (Remix)', 'Album', {}) == 'remix'


# ---------------------------------------------------------------------------
# load_global_content_filter_settings
# ---------------------------------------------------------------------------


class TestLoadGlobalSettings:
    def test_reads_all_four_settings(self):
        cfg = SimpleNamespace()
        cfg.get = lambda key, default=None: {
            'watchlist.global_include_live': True,
            'watchlist.global_include_remixes': False,
            'watchlist.global_include_acoustic': True,
            'watchlist.global_include_instrumentals': False,
        }.get(key, default)
        result = load_global_content_filter_settings(cfg)
        assert result == {
            'include_live': True,
            'include_remixes': False,
            'include_acoustic': True,
            'include_instrumentals': False,
        }

    def test_defaults_all_false_when_config_manager_missing(self):
        """No config_manager → all four default to False (exclude).
        Same defaults the watchlist scanner uses for unconfigured artists."""
        result = load_global_content_filter_settings(None)
        assert result == {
            'include_live': False,
            'include_remixes': False,
            'include_acoustic': False,
            'include_instrumentals': False,
        }

    def test_config_get_raising_falls_back_to_defaults(self):
        """Defensive: if `config_manager.get` raises (corrupted config,
        backend offline, etc.), helper returns all-False defaults
        rather than crashing the discography fetch."""
        cfg = SimpleNamespace()
        def _boom(*_a, **_k):
            raise RuntimeError('config backend exploded')
        cfg.get = _boom
        result = load_global_content_filter_settings(cfg)
        assert result['include_live'] is False
        assert result['include_remixes'] is False

    def test_setting_values_coerced_to_bool(self):
        """Config can store as int / string — coerce defensively so
        downstream callers can rely on the bool contract."""
        cfg = SimpleNamespace()
        cfg.get = lambda key, default=None: {
            'watchlist.global_include_live': 1,        # int truthy
            'watchlist.global_include_remixes': '',    # empty string falsy
            'watchlist.global_include_acoustic': 'on', # string truthy
            'watchlist.global_include_instrumentals': 0,
        }.get(key, default)
        result = load_global_content_filter_settings(cfg)
        assert result['include_live'] is True
        assert result['include_remixes'] is False
        assert result['include_acoustic'] is True
        assert result['include_instrumentals'] is False
