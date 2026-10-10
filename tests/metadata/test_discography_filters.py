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
    track_already_owned,
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

    def test_combined_collab_credit_string_matches_component(self):
        """#830 (Vicky-2418): iTunes packs a collab into ONE string. The
        requested artist is one of several credited — must match. This is the
        exact real case from the report (Narvent's 'Miss You (Ambient Remix)')."""
        credit = ['TRVNSPORTER, Narvent & SKVLENT']
        assert track_artist_matches(credit, 'Narvent') is True
        assert track_artist_matches(credit, 'TRVNSPORTER') is True
        assert track_artist_matches(credit, 'SKVLENT') is True

    def test_combined_credit_still_drops_absent_artist(self):
        """The #559 contamination guard survives: an artist genuinely absent
        from a combined credit is still dropped."""
        assert track_artist_matches(['TRVNSPORTER, Narvent & SKVLENT'], 'Drake') is False

    def test_feat_forms_match_component(self):
        for credit in (['Drake feat. Narvent'], ['Drake ft. Narvent'],
                       ['Drake featuring Narvent'], ['Drake x Narvent']):
            assert track_artist_matches(credit, 'Narvent') is True

    def test_no_substring_false_positive(self):
        """Component matching is exact per-name — a longer name that merely
        contains the target must NOT match."""
        assert track_artist_matches(['Narventos'], 'Narvent') is False

    def test_band_name_with_internal_separator_still_matches_exactly(self):
        """A real band name containing a separator still matches as the full
        string (we keep the whole credit as a candidate alongside the split)."""
        assert track_artist_matches(['Florence + the Machine'], 'Florence + the Machine') is True

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

    def test_substring_does_not_match_but_component_does(self):
        """No SUBSTRING matching — "Drakeo the Ruler" must not match "Drake".
        But a combined collab credit IS component-matched (#830): "Drake &
        Future" matches "Drake" because Drake is genuinely one of the credited
        artists. The #559 guard drops artists who aren't credited AT ALL, not
        legit collaborators packed into one string by sources like iTunes."""
        assert track_artist_matches(['Drakeo the Ruler'], 'Drake') is False
        assert track_artist_matches(['Drake & Future'], 'Drake') is True


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


# ---------------------------------------------------------------------------
# track_already_owned
# ---------------------------------------------------------------------------


class _FakeDB:
    """Minimal stub for the parts of MusicDatabase the helper touches."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def check_track_exists(self, title, artist, **kwargs):
        self.calls.append({'title': title, 'artist': artist, **kwargs})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class TestTrackAlreadyOwned:
    def test_returns_true_when_match_clears_threshold(self):
        """Skowl's case: the second discography click finds the track
        already in library at confidence ≥ 0.7 → skip."""
        db = _FakeDB((object(), 0.85))
        assert track_already_owned(
            db, 'Hotline Bling', 'Drake', 'Views', 'plex',
        ) is True

    def test_returns_false_when_no_match(self):
        """Library doesn't have it → return False so the caller queues
        the track. (None track returned with confidence 0.0.)"""
        db = _FakeDB((None, 0.0))
        assert track_already_owned(
            db, 'New Song', 'Drake', 'New Album', 'plex',
        ) is False

    def test_returns_false_when_match_below_threshold(self):
        """A weak fuzzy match shouldn't count — better to over-queue
        than to silently drop a real missing track. Mirrors the
        backfill repair job's `if db_track and confidence >= 0.7` guard."""
        db = _FakeDB((object(), 0.5))  # below default 0.7
        assert track_already_owned(
            db, 'Sort Of Like Hotline Bling', 'Drake', 'Views', 'plex',
        ) is False

    def test_passes_album_to_check(self):
        """Album param is what enables album-aware matching for
        multi-artist albums in `check_track_exists`. Pin it gets through."""
        db = _FakeDB((object(), 0.9))
        track_already_owned(db, 'Track', 'Artist', 'Album X', 'plex')
        assert db.calls[0]['album'] == 'Album X'

    def test_candidate_tracks_threaded_to_batched_path(self):
        """The discography endpoint pre-fetches the artist's owned tracks once
        and passes them so check_track_exists scores in-memory instead of firing
        per-track fuzzy SQL — the fix for ~15-30s/track on a large library."""
        owned = [SimpleNamespace(title='Owned')]
        db = _FakeDB((object(), 0.9))
        track_already_owned(
            db, 'Owned', 'Artist', 'Album', 'plex', candidate_tracks=owned,
        )
        # The pre-fetched candidates must reach check_track_exists verbatim.
        assert db.calls[0]['candidate_tracks'] is owned

    def test_empty_candidate_list_with_exhaustive_flag_uses_fast_path(self):
        """Owns-nothing case: an EMPTY list (not None) WITH the exhaustive flag
        must be forwarded so the check takes the fast in-memory path (scores
        against zero candidates → instant 'not owned') instead of falling back
        to the slow per-track SQL. (#1621: a bare [] without the flag now
        means "no information" and takes the legacy path.)"""
        db = _FakeDB((None, 0.0))
        result = track_already_owned(
            db, 'Anything', 'Artist', 'Album', 'plex', candidate_tracks=[],
            candidate_tracks_exhaustive=True,
        )
        assert result is False
        # [] is forwarded (not coerced to None) with the flag — that's what
        # keeps it fast.
        assert db.calls[0]['candidate_tracks'] == []
        assert db.calls[0]['candidate_tracks_exhaustive'] is True

    def test_bare_empty_list_forwards_without_exhaustive_flag(self):
        """Without the flag, [] is "no information" (#1621) — the helper must
        not claim exhaustiveness the caller didn't assert."""
        db = _FakeDB((None, 0.0))
        track_already_owned(db, 'Anything', 'Artist', 'Album', 'plex', candidate_tracks=[])
        assert db.calls[0]['candidate_tracks'] == []
        assert db.calls[0]['candidate_tracks_exhaustive'] is False

    def test_default_omits_candidate_tracks_for_legacy_callers(self):
        """Callers that don't pre-fetch get None → check_track_exists keeps its
        original per-track-SQL behaviour. No other caller is forced to change."""
        db = _FakeDB((object(), 0.9))
        track_already_owned(db, 'Track', 'Artist', 'Album', 'plex')
        assert db.calls[0]['candidate_tracks'] is None

    def test_passes_server_source_to_check(self):
        """Active media server scopes the lookup so the skip check
        only fires on tracks the user can actually see in their
        library through their currently-active server."""
        db = _FakeDB((object(), 0.9))
        track_already_owned(db, 'Track', 'Artist', 'Album', 'navidrome')
        assert db.calls[0]['server_source'] == 'navidrome'

    def test_empty_album_passed_as_none(self):
        """Empty-string album becomes None so check_track_exists's
        album-aware fallback doesn't try to match against ''."""
        db = _FakeDB((None, 0.0))
        track_already_owned(db, 'Track', 'Artist', '', 'plex')
        assert db.calls[0]['album'] is None

    def test_missing_track_or_artist_returns_false_without_calling_db(self):
        """Don't fire a DB call when we have nothing to match against —
        defensive AND avoids polluting query logs with empty lookups."""
        db = _FakeDB((object(), 0.9))
        assert track_already_owned(db, '', 'Artist', 'Album', 'plex') is False
        assert track_already_owned(db, 'Track', '', 'Album', 'plex') is False
        assert track_already_owned(db, '', '', 'Album', 'plex') is False
        assert db.calls == [], "DB must not be called when track or artist is empty"

    def test_db_exception_returns_false(self):
        """If the DB call raises (lock contention, schema mismatch,
        whatever), treat as 'not owned' and let the caller queue.
        A redundant wishlist add is much cheaper to recover from
        than a missed track."""
        db = _FakeDB(RuntimeError('db locked'))
        assert track_already_owned(db, 'Track', 'Artist', 'Album', 'plex') is False

    def test_custom_confidence_threshold_honored(self):
        """Caller can tighten or loosen the threshold. 0.95 means only
        very-high-confidence matches count as owned."""
        db = _FakeDB((object(), 0.8))
        # Default threshold (0.7): match counts
        assert track_already_owned(db, 'Track', 'Artist', 'Album', 'plex') is True
        # Tighter threshold (0.95): same match doesn't count
        assert track_already_owned(
            db, 'Track', 'Artist', 'Album', 'plex',
            confidence_threshold=0.95,
        ) is False

    def test_none_server_source_passes_through(self):
        """When the caller can't determine the active server, pass
        None — `check_track_exists` falls back to a cross-server search."""
        db = _FakeDB((None, 0.0))
        track_already_owned(db, 'Track', 'Artist', 'Album', None)
        assert db.calls[0]['server_source'] is None




# ---------------------------------------------------------------------------
# watchlist exclusion labeling  (#1550)
# ---------------------------------------------------------------------------
#
# The watchlist scanner deliberately skips releases the user's filters
# exclude, but the artist page marked them as bare "missing" — which reads
# as a broken scanner. These helpers compute the same verdict the scanner
# would, so the artist page can label the release instead.


class _FakeWatchlistDB:
    """Minimal stand-in exposing get_watchlist_artists(profile_id=...)."""

    def __init__(self, rows=(), exc=None):
        self._rows = list(rows)
        self._exc = exc
        self.seen_profiles = []

    def get_watchlist_artists(self, profile_id=1):
        self.seen_profiles.append(profile_id)
        if self._exc is not None:
            raise self._exc
        return self._rows


def _watch_artist(name, **prefs):
    defaults = dict(
        include_live=False,
        include_remixes=False,
        include_acoustic=False,
        include_instrumentals=False,
        include_compilations=False,
        include_albums=True,
        include_eps=True,
        include_singles=True,
    )
    defaults.update(prefs)
    return SimpleNamespace(artist_name=name, **defaults)


def _cfg_no_override():
    """Ambient-config pin: global override OFF unless a test says otherwise.

    Without this, the tests below would silently change behavior if the
    ambient config ever enabled the override.
    """
    return SimpleNamespace(get=lambda key, default=None: {
        'watchlist.global_override_enabled': False,
        'watchlist.exclude_terms': '',
    }.get(key, default))


@pytest.fixture()
def _no_global_override(monkeypatch):
    import core.settings

    monkeypatch.setattr(core.settings, 'config_manager', _cfg_no_override(), raising=False)


class TestResolveWatchlistContentSettings:
    def test_returns_effective_prefs_for_watched_artist(self, _no_global_override):
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB([_watch_artist('Étienne de Crécy', include_remixes=True)])
        settings = resolve_watchlist_content_settings(db, 'Étienne de Crécy')
        assert settings['include_remixes'] is True
        assert settings['include_live'] is False
        assert settings['include_singles'] is True  # release-type defaults True
        assert db.seen_profiles == [1]

    def test_unwatched_artist_returns_none(self, _no_global_override):
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB([_watch_artist('Someone Else')])
        assert resolve_watchlist_content_settings(db, 'Étienne de Crécy') is None

    def test_name_match_ignores_diacritics_and_case(self, _no_global_override):
        """Watchlist rows store the name as-added (source-dependent); the
        artist page may pass a diacritic-folded variant."""
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB([_watch_artist('Étienne de Crécy')])
        assert resolve_watchlist_content_settings(db, 'Etienne de Crecy') is not None
        assert resolve_watchlist_content_settings(db, 'ÉTIENNE DE CRÉCY') is not None

    def test_exact_match_wins_over_folded_collision(self, _no_global_override):
        """Two distinct rows folding together ('José' vs 'Jose' as separate
        artists): the exact one wins."""
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        jose_plain = _watch_artist('Jose', include_remixes=True)
        jose_accent = _watch_artist('José', include_remixes=False)
        db = _FakeWatchlistDB([jose_accent, jose_plain])
        settings = resolve_watchlist_content_settings(db, 'Jose')
        assert settings['include_remixes'] is True

    def test_ambiguous_folded_match_returns_none(self, _no_global_override):
        """No exact match and several rows fold together: ambiguous — yield
        no label rather than risk the WRONG artist's settings."""
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB([_watch_artist('José'), _watch_artist('Jose')])
        # No exact match for 'JOSÉ' (case differs from both rows); both rows
        # fold to 'jose' -> ambiguous -> None.
        assert resolve_watchlist_content_settings(db, 'JOSÉ') is None

    def test_db_failure_returns_none(self, _no_global_override):
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB(exc=RuntimeError('db locked'))
        assert resolve_watchlist_content_settings(db, 'Artist') is None

    def test_empty_artist_name_skips_db(self, _no_global_override):
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB([_watch_artist('Artist')])
        assert resolve_watchlist_content_settings(db, '') is None
        assert db.seen_profiles == []

    def test_explicit_profile_id_used(self, _no_global_override):
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        db = _FakeWatchlistDB([_watch_artist('Artist')])
        resolve_watchlist_content_settings(db, 'Artist', profile_id=3)
        assert db.seen_profiles == [3]

    def test_global_override_wins_over_row(self, monkeypatch):
        """Mirrors WatchlistScanner._apply_global_watchlist_overrides."""
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        fake_cfg = SimpleNamespace(get=lambda key, default=None: {
            'watchlist.global_override_enabled': True,
            'watchlist.global_include_live': False,
            'watchlist.global_include_remixes': True,
            'watchlist.global_include_acoustic': False,
            'watchlist.global_include_instrumentals': False,
            'watchlist.global_include_compilations': False,
            'watchlist.global_include_albums': True,
            'watchlist.global_include_eps': True,
            'watchlist.global_include_singles': False,
            'watchlist.exclude_terms': '',
        }.get(key, default))
        import core.settings

        monkeypatch.setattr(core.settings, 'config_manager', fake_cfg, raising=False)
        db = _FakeWatchlistDB([_watch_artist('Artist')])  # row: remixes off, singles on
        settings = resolve_watchlist_content_settings(db, 'Artist')
        assert settings['include_remixes'] is True   # global wins
        assert settings['include_singles'] is False  # global wins


class TestReleaseKindForScan:
    def test_track_count_buckets_match_scanner(self):
        from core.metadata.discography_filters import release_kind_for_scan

        assert release_kind_for_scan(1) == 'singles'
        assert release_kind_for_scan(3) == 'singles'
        assert release_kind_for_scan(4) == 'eps'
        assert release_kind_for_scan(6) == 'eps'
        assert release_kind_for_scan(7) == 'albums'
        assert release_kind_for_scan(12) == 'albums'
        # A 2-track provider-labeled EP is a "single" to the scanner.
        assert release_kind_for_scan(2) == 'singles'
        assert release_kind_for_scan('12') == 'albums'

    def test_unknown_count_returns_none_never_guesses(self):
        """The scan `continue`s on empty track lists before classifying, so
        an unknown count means the scan never reaches the release-type gate
        — the caller must skip that gate, not guess a bucket."""
        from core.metadata.discography_filters import release_kind_for_scan

        assert release_kind_for_scan(0) is None
        assert release_kind_for_scan(None) is None
        assert release_kind_for_scan('bogus') is None
        assert release_kind_for_scan(-3) is None


class TestContentExclusionReason:
    def _settings(self, **over):
        base = dict(
            include_live=False, include_remixes=False, include_acoustic=False,
            include_instrumentals=False, include_compilations=False,
            include_albums=True, include_eps=True, include_singles=True,
            exclude_terms=[],
        )
        base.update(over)
        return base

    def test_issue_1550_olympic_mix_is_remix(self):
        """The exact #1550 report: 'Am I Wrong (Olympic Mix)' trips the
        remix filter with default (exclude) preferences."""
        from core.metadata.discography_filters import content_exclusion_reason

        assert content_exclusion_reason(
            self._settings(), 'Am I Wrong (Olympic Mix)', 'Am I Wrong (Olympic Mix)', 'singles'
        ) == 'remix'

    def test_release_type_gate_checked_first(self):
        """A watched artist with singles off: every single is skipped by
        the scan regardless of content — the same 'missing with no
        explanation' confusion as #1550."""
        from core.metadata.discography_filters import content_exclusion_reason

        settings = self._settings(include_singles=False)
        assert content_exclusion_reason(settings, 'Plain Song', 'Plain Song', 'singles') == 'singles'
        assert content_exclusion_reason(settings, 'Plain Song', 'Plain Album', 'albums') is None
        ep_settings = self._settings(include_eps=False)
        assert content_exclusion_reason(ep_settings, 'Plain Song', 'Plain EP', 'eps') == 'eps'

    def test_unknown_kind_skips_release_type_gate(self):
        """Count unknown -> kind None -> no release-type guess, but content
        gates still apply."""
        from core.metadata.discography_filters import content_exclusion_reason

        settings = self._settings(include_singles=False)
        assert content_exclusion_reason(settings, 'Plain Song', 'Plain Song', None) is None
        assert content_exclusion_reason(settings, 'Song (Remix)', 'Song', None) == 'remix'

    def test_release_type_defaults_include(self):
        """include_albums/eps/singles default True (unlike the content
        filters) — matching the scanner's getattr defaults."""
        from core.metadata.discography_filters import content_exclusion_reason

        assert content_exclusion_reason({}, 'Plain Song', 'Plain Song', 'singles') is None

    def test_compilation_before_content_filters(self):
        from core.metadata.discography_filters import content_exclusion_reason

        assert content_exclusion_reason(
            self._settings(), 'Song', 'Greatest Hits', 'albums'
        ) == 'compilation'
        assert content_exclusion_reason(
            self._settings(include_compilations=True), 'Song', 'Greatest Hits', 'albums'
        ) is None

    def test_content_types(self):
        from core.metadata.discography_filters import content_exclusion_reason

        cases = [
            ('Song (Live at Leeds)', 'Song', 'live'),
            ('Song (Acoustic)', 'Song', 'acoustic'),
            ('Song (Instrumental)', 'Song', 'instrumental'),
        ]
        for track, album, expected in cases:
            assert content_exclusion_reason(self._settings(), track, album, 'singles') == expected

    def test_custom_exclude_terms_are_the_final_gate(self):
        """Mirrors the scan: custom terms are checked after all built-in
        content-type filters."""
        from core.metadata.discography_filters import content_exclusion_reason

        settings = self._settings(exclude_terms=['demo', 'xyz'])
        assert content_exclusion_reason(settings, 'Song (Demo)', 'Song', 'singles') == 'custom'
        assert content_exclusion_reason(settings, 'Ordinary Song', 'Ordinary', 'singles') is None
        # Built-in gates still win the ordering.
        both = self._settings(exclude_terms=['mix'])
        assert content_exclusion_reason(both, 'Song (Club Mix)', 'Song', 'singles') == 'remix'

    def test_plain_release_returns_none(self):
        from core.metadata.discography_filters import content_exclusion_reason

        assert content_exclusion_reason(
            self._settings(), 'Ordinary Song', 'Ordinary Album', 'albums'
        ) is None

    def test_empty_settings_returns_none(self):
        from core.metadata.discography_filters import content_exclusion_reason

        assert content_exclusion_reason(None, 'Song (Remix)', 'Song', 'singles') is None
        assert content_exclusion_reason({}, 'Song (Remix)', 'Song', 'singles') is None


class TestAttachWatchlistExclusion:
    def _settings_for(self, db, artist):
        from core.metadata.discography_filters import resolve_watchlist_content_settings

        return resolve_watchlist_content_settings(db, artist)

    def test_missing_single_gets_remix_label(self, _no_global_override):
        from core.metadata.discography_filters import attach_watchlist_exclusion

        db = _FakeWatchlistDB([_watch_artist('Étienne de Crécy')])
        settings = self._settings_for(db, 'Étienne de Crécy')
        event = {'status': 'missing', 'expected_tracks': 1, 'name': 'Am I Wrong (Olympic Mix)'}
        out = attach_watchlist_exclusion(event, settings, 'Am I Wrong (Olympic Mix)')
        assert out['watchlist_excluded'] == 'remix'
        assert out is event  # mutates in place

    def test_missing_album_with_albums_off_gets_release_type_label(self, _no_global_override):
        from core.metadata.discography_filters import attach_watchlist_exclusion

        db = _FakeWatchlistDB([_watch_artist('Artist', include_albums=False)])
        settings = self._settings_for(db, 'Artist')
        event = {'status': 'missing', 'expected_tracks': 12}
        out = attach_watchlist_exclusion(event, settings, 'Some Album')
        assert out['watchlist_excluded'] == 'albums'

    def test_missing_with_no_attributable_reason_stamps_none(self, _no_global_override):
        """The key is ALWAYS stamped when settings exist and the release is
        missing — None clears a stale label from an earlier event."""
        from core.metadata.discography_filters import attach_watchlist_exclusion

        db = _FakeWatchlistDB([_watch_artist('Artist')])
        settings = self._settings_for(db, 'Artist')
        event = {'status': 'missing', 'expected_tracks': 1}
        out = attach_watchlist_exclusion(event, settings, 'Ordinary Song')
        assert out['watchlist_excluded'] is None

    def test_owned_release_untouched(self, _no_global_override):
        from core.metadata.discography_filters import attach_watchlist_exclusion

        db = _FakeWatchlistDB([_watch_artist('Artist')])
        settings = self._settings_for(db, 'Artist')
        event = {'status': 'completed', 'expected_tracks': 1}
        assert attach_watchlist_exclusion(event, settings, 'Song (Remix)') == event
        assert 'watchlist_excluded' not in event

    def test_no_settings_leaves_event_untouched(self):
        from core.metadata.discography_filters import attach_watchlist_exclusion

        event = {'status': 'missing', 'expected_tracks': 1}
        assert attach_watchlist_exclusion(event, None, 'Song (Remix)') == event
        assert 'watchlist_excluded' not in event

    def test_non_dict_event_passthrough(self):
        from core.metadata.discography_filters import attach_watchlist_exclusion

        assert attach_watchlist_exclusion(None, {'include_remixes': False}, 'X') is None

    def test_custom_term_label(self, monkeypatch):
        """A release skipped solely by a user custom term gets the 'custom'
        label — the scan's final gate is mirrored."""
        from core.metadata.discography_filters import attach_watchlist_exclusion

        import core.settings

        monkeypatch.setattr(core.settings, 'config_manager', SimpleNamespace(
            get=lambda key, default=None: {
                'watchlist.global_override_enabled': False,
                'watchlist.exclude_terms': 'demo',
            }.get(key, default)
        ), raising=False)
        db = _FakeWatchlistDB([_watch_artist('Artist')])
        settings = self._settings_for(db, 'Artist')
        event = {'status': 'missing', 'expected_tracks': 1}
        out = attach_watchlist_exclusion(event, settings, 'Song (Demo)')
        assert out['watchlist_excluded'] == 'custom'


class TestWatchlistExclusionReasonWrapper:
    def test_wrapper_end_to_end(self, _no_global_override):
        from core.metadata.discography_filters import watchlist_exclusion_reason

        db = _FakeWatchlistDB([_watch_artist('Étienne de Crécy')])
        assert watchlist_exclusion_reason(
            db, 'Étienne de Crécy', 'Am I Wrong (Olympic Mix)', 'Am I Wrong (Olympic Mix)',
            release_kind='singles',
        ) == 'remix'

    def test_wrapper_release_kind_required(self, _no_global_override):
        from core.metadata.discography_filters import watchlist_exclusion_reason

        db = _FakeWatchlistDB([_watch_artist('Artist', include_singles=False)])
        assert watchlist_exclusion_reason(
            db, 'Artist', 'Plain Song', 'Plain Song', release_kind='singles'
        ) == 'singles'
        # No default to silently guess from: release_kind is required.
        with pytest.raises(TypeError):
            watchlist_exclusion_reason(db, 'Artist', 'Plain Song', 'Plain Song')

    def test_wrapper_unwatched_or_empty(self, _no_global_override):
        from core.metadata.discography_filters import watchlist_exclusion_reason

        db = _FakeWatchlistDB([_watch_artist('Other')])
        assert watchlist_exclusion_reason(
            db, 'Artist', 'Song (Remix)', 'Song', release_kind='singles'
        ) is None
        assert watchlist_exclusion_reason(
            db, '', 'Song', 'Song', release_kind='singles'
        ) is None
        assert watchlist_exclusion_reason(
            db, 'Artist', '', 'Song', release_kind='singles'
        ) is None
