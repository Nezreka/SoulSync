"""Regression tests for issue #442 — AcoustID verifier alias awareness.

The reporter posted two exact cases:

Case 1 (Japanese kanji ↔ romanized):
    File:     YAMANAIAME by 澤野弘之
    Expected: YAMANAIAME by Hiroyuki Sawano
    Pre-fix: quarantined (artist_sim=0%)
    Post-fix: passes verification because MB aliases bridge the
              two spellings.

Case 2 (Cyrillic ↔ Latin):
    File:     On the Other Side by Sergey Lazarev
    Expected: On the other side by Сергей Лазарев
    Pre-fix: quarantined (artist_sim=7%)
    Post-fix: passes via aliases.

These tests pin the verifier through the helper. AcoustID's
fingerprint call is stubbed (no network), MB service's
`lookup_artist_aliases` is stubbed to return the relevant aliases.
The verifier's pass/fail decision is the assertion.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.acoustid_verification import (
    AcoustIDVerification,
    VerificationResult,
    _alias_aware_artist_sim,
    _find_best_title_artist_match,
)


# ---------------------------------------------------------------------------
# Pure helper — _alias_aware_artist_sim
# ---------------------------------------------------------------------------


class TestAliasAwareArtistSim:
    def test_returns_higher_score_when_alias_matches(self):
        score = _alias_aware_artist_sim(
            'Hiroyuki Sawano', '澤野弘之',
            aliases=['澤野弘之', 'SawanoHiroyuki'],
        )
        assert score == 1.0

    def test_no_aliases_falls_back_to_direct_similarity(self):
        """Cross-script with NO aliases → score ~0, pre-fix behaviour."""
        score = _alias_aware_artist_sim(
            'Hiroyuki Sawano', '澤野弘之', aliases=None,
        )
        assert score < 0.1

    def test_aliases_dont_mask_genuine_mismatch(self):
        """Different artist entirely → still scores low even when
        aliases are provided. Aliases bridge synonyms, not unrelated
        artists."""
        score = _alias_aware_artist_sim(
            'Hiroyuki Sawano', 'Khalil Turk & Friends',
            aliases=['澤野弘之', 'SawanoHiroyuki'],
        )
        assert score < 0.5


# ---------------------------------------------------------------------------
# _find_best_title_artist_match — accepts aliases now
# ---------------------------------------------------------------------------


class TestFindBestMatchWithAliases:
    def test_japanese_alias_picks_correct_recording(self):
        """Reporter's case 1: AcoustID returned recording with kanji
        artist. Without aliases the scorer ranks it low and the
        verifier later quarantines. With aliases it scores high."""
        recordings = [
            {'title': 'YAMANAIAME', 'artist': '澤野弘之'},
            {'title': 'Different Song', 'artist': 'Hiroyuki Sawano'},
        ]
        # Aliases provided — bridge to recording 0
        best, title_sim, artist_sim = _find_best_title_artist_match(
            recordings, 'YAMANAIAME', 'Hiroyuki Sawano',
            expected_artist_aliases=['澤野弘之', 'SawanoHiroyuki'],
        )
        assert best is recordings[0]
        assert artist_sim == 1.0

    def test_no_aliases_legacy_behaviour_preserved(self):
        """Default arg / empty aliases → identical to pre-fix
        behaviour. Critical for paths not yet wired up to alias
        lookup."""
        recordings = [
            {'title': 'Track', 'artist': 'Artist'},
        ]
        best, title_sim, artist_sim = _find_best_title_artist_match(
            recordings, 'Track', 'Artist',
        )
        assert title_sim == 1.0
        assert artist_sim == 1.0


# ---------------------------------------------------------------------------
# End-to-end — reporter's cases through the full verifier
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed_verifier(monkeypatch):
    """AcoustIDVerification with the acoustid client + MB service
    layer stubbed. Lets us drive the verifier's full decision path
    without network or DB. Returns the verifier + mutable handles
    to the stubs so each test can shape the AcoustID response +
    aliases."""
    verifier = AcoustIDVerification()
    verifier.acoustid_client = MagicMock()
    verifier.acoustid_client.is_available.return_value = (True, '')

    # Stub the MB service so verifier alias lookup doesn't touch DB
    # or network. Each test sets fake_service.lookup_artist_aliases.
    fake_service = MagicMock()
    fake_service.lookup_artist_aliases.return_value = []
    monkeypatch.setattr(
        'core.acoustid_verification._get_mb_service', lambda: fake_service,
    )

    return verifier, fake_service


class TestIssue442Regression:
    def test_japanese_kanji_artist_passes_verification(self, stubbed_verifier):
        """Reporter's case 1 — verbatim from the issue:

            File:     YAMANAIAME by 澤野弘之
            Expected: YAMANAIAME by Hiroyuki Sawano
            Pre-fix:  Quarantined (artist=0%)
        """
        verifier, fake_service = stubbed_verifier

        # AcoustID returns the recording with kanji artist
        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'YAMANAIAME', 'artist': '澤野弘之', 'mbid': 'rec-x'},
            ],
        }
        # MB knows Hiroyuki Sawano's aliases
        fake_service.lookup_artist_aliases.return_value = [
            '澤野弘之', 'SawanoHiroyuki', 'Sawano Hiroyuki',
        ]

        result, msg = verifier.verify_audio_file(
            '/fake/path.mp3', 'YAMANAIAME', 'Hiroyuki Sawano',
        )

        assert result == VerificationResult.PASS, (
            f"Reporter's exact case must pass verification post-fix; "
            f"got result={result.value!r} msg={msg!r}"
        )
        fake_service.lookup_artist_aliases.assert_called_once_with('Hiroyuki Sawano')

    def test_cyrillic_artist_passes_verification(self, stubbed_verifier):
        """Reporter's case 2 — Sergey Lazarev / Сергей Лазарев."""
        verifier, fake_service = stubbed_verifier

        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'On the Other Side', 'artist': 'Sergey Lazarev', 'mbid': 'rec-y'},
            ],
        }
        fake_service.lookup_artist_aliases.return_value = [
            'Sergey Lazarev', 'Sergei Lazarev',
        ]

        result, msg = verifier.verify_audio_file(
            '/fake/path.flac', 'On the other side', 'Сергей Лазарев',
        )

        assert result == VerificationResult.PASS


# ---------------------------------------------------------------------------
# Backward compat — no aliases available → behavior identical to pre-fix
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_no_aliases_clear_artist_mismatch_still_fails(self, stubbed_verifier):
        """Pre-fix: clear mismatches (artist sim near 0, NOT a script
        difference) should FAIL. Post-fix with empty aliases must
        preserve this — aliases bridge synonyms, not unrelated
        artists."""
        verifier, fake_service = stubbed_verifier

        # Wrong artist entirely — Latin script both sides, sim ~0
        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'Some Track', 'artist': 'Khalil Turk & Friends'},
            ],
        }
        fake_service.lookup_artist_aliases.return_value = []  # No aliases

        result, msg = verifier.verify_audio_file(
            '/fake/path.mp3', 'Some Track', 'Foreigner',
        )

        assert result == VerificationResult.FAIL

    def test_no_aliases_exact_match_still_passes(self, stubbed_verifier):
        """Exact title + artist match → PASS regardless of aliases."""
        verifier, fake_service = stubbed_verifier

        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'Dirty White Boy', 'artist': 'Foreigner'},
            ],
        }
        fake_service.lookup_artist_aliases.return_value = []

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Dirty White Boy', 'Foreigner',
        )
        assert result == VerificationResult.PASS

    def test_alias_lookup_failure_does_not_break_verification(self, stubbed_verifier):
        """MB service raises → verifier still completes with direct
        similarity (pre-fix behaviour preserved)."""
        verifier, fake_service = stubbed_verifier
        fake_service.lookup_artist_aliases.side_effect = Exception("MB down")

        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'Dirty White Boy', 'artist': 'Foreigner'},
            ],
        }

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Dirty White Boy', 'Foreigner',
        )
        # Should still pass — direct similarity works
        assert result == VerificationResult.PASS


# ---------------------------------------------------------------------------
# Performance contract — alias lookup fires ONCE per verification
# ---------------------------------------------------------------------------


class TestAliasLookupCalledOncePerVerify:
    def test_single_lookup_call_regardless_of_recordings_count(self, stubbed_verifier):
        """The verifier processes multiple recordings + scans through
        them at up to 3 sites — but should only call
        `lookup_artist_aliases` ONCE per verify_audio_file invocation.
        Otherwise verifying a track with 20 AcoustID recordings could
        fire 60+ MB lookups (cached or not, that's wasteful)."""
        verifier, fake_service = stubbed_verifier

        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'X', 'artist': '澤野弘之'},
                {'title': 'X', 'artist': 'SawanoHiroyuki'},
                {'title': 'X', 'artist': 'Different Artist'},
            ],
        }
        fake_service.lookup_artist_aliases.return_value = ['澤野弘之', 'SawanoHiroyuki']

        verifier.verify_audio_file('/fake/path.mp3', 'X', 'Hiroyuki Sawano')

        assert fake_service.lookup_artist_aliases.call_count == 1


# ---------------------------------------------------------------------------
# Lazy alias resolution — happy path skips MB lookup entirely
# ---------------------------------------------------------------------------


class TestLazyAliasResolution:
    """Issue #442 perf followup: alias lookup should ONLY fire when
    the direct artist comparison fails. Verifications where artist
    names already match (the 95% common case for same-script
    libraries) must NOT trigger the lookup chain — no wasted DB
    query, no wasted MB call."""

    def test_no_lookup_when_direct_artist_match_passes(self, stubbed_verifier):
        """Exact-match Latin-script artist passes verification with
        zero alias lookups — no DB query, no MB call. Same-script
        libraries (the 95% common case) inherit zero perf cost from
        this PR."""
        verifier, fake_service = stubbed_verifier

        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'Dirty White Boy', 'artist': 'Foreigner'},
            ],
        }

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Dirty White Boy', 'Foreigner',
        )

        assert result == VerificationResult.PASS
        # Critical — alias lookup must NOT have been called for the
        # happy path. Otherwise every successful verification adds a
        # DB query for nothing.
        fake_service.lookup_artist_aliases.assert_not_called()

    def test_lookup_fires_only_when_direct_artist_match_fails(self, stubbed_verifier):
        """Cross-script case where direct sim is 0% → lookup fires
        as expected."""
        verifier, fake_service = stubbed_verifier

        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'YAMANAIAME', 'artist': '澤野弘之'},
            ],
        }
        fake_service.lookup_artist_aliases.return_value = ['澤野弘之']

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'YAMANAIAME', 'Hiroyuki Sawano',
        )

        assert result == VerificationResult.PASS
        # Lookup fired BECAUSE direct match would have failed
        fake_service.lookup_artist_aliases.assert_called_once()

    def test_lookup_memoised_across_three_comparison_sites(self, stubbed_verifier):
        """When lookup DOES fire, the result must be reused across
        the three artist-comparison sites in the verifier (best-match
        scoring, secondary scan, fallback scan). One resolution per
        verification — not three."""
        verifier, fake_service = stubbed_verifier

        # Force a code path that hits multiple sites: title matches
        # several recordings but the best-match's artist sim is below
        # threshold (forces secondary scan path).
        verifier.acoustid_client.fingerprint_and_lookup.return_value = {
            'best_score': 0.95,
            'recordings': [
                {'title': 'X', 'artist': 'Different Latin Artist'},  # 0 alias hit
                {'title': 'X', 'artist': '澤野弘之'},                 # alias hit
            ],
        }
        fake_service.lookup_artist_aliases.return_value = ['澤野弘之']

        verifier.verify_audio_file('/fake/path.mp3', 'X', 'Hiroyuki Sawano')

        # Memoised — one resolution shared across all sites
        assert fake_service.lookup_artist_aliases.call_count == 1


# ---------------------------------------------------------------------------
# Provider-callable contract on the helper
# ---------------------------------------------------------------------------


class TestAliasProviderCallable:
    """Pin the dual-shape contract on `_alias_aware_artist_sim`:
    accepts an iterable OR a callable. Callable resolves lazily."""

    def test_iterable_passed_directly(self):
        """Plain list — used as-is, no lazy semantics."""
        score = _alias_aware_artist_sim(
            'Hiroyuki Sawano', '澤野弘之', aliases=['澤野弘之'],
        )
        assert score == 1.0

    def test_callable_resolves_lazily_only_when_direct_fails(self):
        """Callable provider — invoked ONLY when direct sim falls
        below threshold."""
        call_count = [0]

        def provider():
            call_count[0] += 1
            return ['澤野弘之']

        # Direct match passes → provider NOT called
        _alias_aware_artist_sim('Foreigner', 'Foreigner', aliases=provider)
        assert call_count[0] == 0

        # Direct match fails → provider IS called
        _alias_aware_artist_sim('Hiroyuki Sawano', '澤野弘之', aliases=provider)
        assert call_count[0] == 1

    def test_callable_returning_empty_list_falls_back_to_direct(self):
        """Provider returns empty (e.g. MB had no aliases) →
        score = direct sim, no error."""
        score = _alias_aware_artist_sim(
            'Hiroyuki Sawano', '澤野弘之', aliases=lambda: [],
        )
        # ~0 because direct cross-script comparison fails
        assert score < 0.1


# ---------------------------------------------------------------------------
# Diagnostic logging — alias rescues are visible in logs
# ---------------------------------------------------------------------------


class TestAliasRescueLogging:
    """When an alias bridges a comparison that direct similarity
    would have failed, log it at INFO level. Future bug reports
    where a file passed verification incorrectly can be traced back
    to which alias triggered which decision.

    Uses a directly-attached handler instead of pytest's caplog —
    full-suite caplog is intermittently flaky for soulsync namespace
    loggers (handler ordering, parallel test state). An owned
    handler on the specific logger sidesteps both issues, same
    pattern as the prior watchdog-test fix.
    """

    @staticmethod
    def _capture_records():
        """Attach an owned ListHandler to the verifier's logger.
        Returns (records list, teardown callable)."""
        import logging as _logging
        records: list = []

        class _ListHandler(_logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _ListHandler(level=_logging.INFO)
        # Logger name is `soulsync.acoustid.verification` per
        # `core.acoustid_verification`'s `get_logger("acoustid_verification")`
        # — dot-separated, NOT underscored.
        verifier_logger = _logging.getLogger('soulsync.acoustid.verification')
        verifier_logger.addHandler(handler)
        prior_level = verifier_logger.level
        verifier_logger.setLevel(_logging.INFO)

        def teardown():
            verifier_logger.removeHandler(handler)
            verifier_logger.setLevel(prior_level)

        return records, teardown

    def test_alias_rescue_emits_info_log(self):
        records, teardown = self._capture_records()
        try:
            _alias_aware_artist_sim(
                'Hiroyuki Sawano', '澤野弘之', aliases=['澤野弘之'],
            )
        finally:
            teardown()

        rescue_logs = [
            r.getMessage() for r in records
            if 'alias rescued' in r.getMessage().lower()
        ]
        assert len(rescue_logs) >= 1, (
            f"Expected an INFO log line about alias rescue; got "
            f"{[r.getMessage() for r in records]}"
        )

    def test_no_log_when_direct_match_succeeds(self):
        """Happy path doesn't spam logs — only rescue cases log."""
        records, teardown = self._capture_records()
        try:
            _alias_aware_artist_sim(
                'Foreigner', 'Foreigner', aliases=['ignored-alias'],
            )
        finally:
            teardown()

        rescue_logs = [
            r.getMessage() for r in records
            if 'alias rescued' in r.getMessage().lower()
        ]
        assert rescue_logs == []

    def test_no_log_when_alias_doesnt_help(self):
        """If aliases were available but didn't bridge the gap (still
        below threshold), no rescue log — there was no rescue."""
        records, teardown = self._capture_records()
        try:
            _alias_aware_artist_sim(
                'Hiroyuki Sawano', 'Khalil Turk',
                aliases=['Sergey Lazarev'],  # unrelated alias
            )
        finally:
            teardown()

        rescue_logs = [
            r.getMessage() for r in records
            if 'alias rescued' in r.getMessage().lower()
        ]
        assert rescue_logs == []
