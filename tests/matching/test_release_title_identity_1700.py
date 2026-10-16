"""Issue #1700 — AcoustID mismatch when a song is known under another
release's title.

AcoustID reports a recording under its canonical MusicBrainz title, but
the same recording is sold under different track titles on different
releases. The reporter's file was exactly right: the English release of
"Genshin Impact - The Shimmering Voyage Vol. 6" lists the recording as
"Myriad Sins" (expected, and the file's name), while the recording's
canonical MusicBrainz title — the only one AcoustID lists — is the
Chinese "众罪如霜露" by HOYO-MiX, where the expected metadata names the
composer Linyao Feng. Title similarity scored 0%, artist 11%, and the
scan reported a wrong download (one of 660 such findings).

The fix: a recording's ``alternate_titles`` (resolved from its MusicBrainz
ID: its track title on every other release, plus recording aliases) are
identity evidence. An exact normalized match between the expected title
and an alternate title means the fingerprinted recording IS the expected
song under a different release name, so verification passes — while a
same-title cover, a different version, or a merely similar title still
fails exactly as before.
"""

from unittest.mock import MagicMock

import pytest

from core.matching.audio_verification import evaluate, Decision
from core import acoustid_verification as _av
from core.acoustid_verification import (
    AcoustIDVerification, VerificationResult, _resolve_alternate_titles,
)


@pytest.fixture(autouse=True)
def _clear_alternate_titles_cache():
    """The resolver caches positive resolutions per MBID for the process;
    tests must not leak one test's MusicBrainz payload into another's."""
    _av._alternate_titles_cache.clear()
    yield
    _av._alternate_titles_cache.clear()

MBID = 'cbdd1322-0159-4e38-a34e-763407285c17'
CANONICAL = '众罪如霜露'
ALTERNATES = ['Myriad Sins', '衆罪は霜露の如し', '眾罪如霜露']


def _reporter_recording(**extra):
    rec = {'title': CANONICAL, 'artist': 'HOYO-MiX', 'mbid': MBID,
           'score': 1.0}
    rec.update(extra)
    return rec


# ---------------------------------------------------------------------------
# Decision core — identity via alternate titles
# ---------------------------------------------------------------------------


class TestReleaseTitleIdentity:
    def test_reporter_case_fails_without_alternate_titles(self):
        # The screenshot, verbatim: fingerprint 100%, title 0%, artist 11%.
        out = evaluate('Myriad Sins', 'Linyao Feng', [_reporter_recording()],
                       fingerprint_score=1.0)
        assert out.decision == Decision.FAIL
        assert out.title_sim == 0.0
        assert out.artist_sim < 0.30

    def test_reporter_case_passes_with_alternate_titles(self):
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [_reporter_recording(alternate_titles=list(ALTERNATES))],
            fingerprint_score=1.0)
        assert out.decision == Decision.PASS
        assert out.title_sim == 1.0
        assert 'Myriad Sins' in out.reason
        # The reported match stays the recording AcoustID actually named.
        assert out.matched_title == CANONICAL
        assert out.matched_artist == 'HOYO-MiX'

    def test_identity_found_on_a_non_first_recording(self):
        recs = [
            {'title': 'Rich Interlude', 'artist': 'Kendrick Lamar',
             'score': 0.5},
            _reporter_recording(alternate_titles=list(ALTERNATES)),
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.PASS

    def test_low_score_alternate_carrier_does_not_rescue(self):
        # A stray / mis-linked low-scored candidate that happens to carry
        # the expected title on some release proves nothing about a file
        # whose fingerprint actually matched something else.
        recs = [
            {'title': 'Real Wrong Song', 'artist': 'HOYO-MiX',
             'score': 0.99},
            {'title': 'Unrelated Canon', 'artist': 'HOYO-MiX',
             'score': 0.31, 'alternate_titles': ['Myriad Sins']},
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=0.99)
        assert out.decision == Decision.FAIL

    def test_ambiguous_top_tie_does_not_rescue(self):
        # Two recordings tied at the top naming different songs: the
        # fingerprint cannot say which one this is (#1132), so identity
        # must not pick the convenient one.
        recs = [
            {'title': 'Unrelated Canon', 'artist': 'HOYO-MiX',
             'score': 1.0, 'alternate_titles': ['Myriad Sins']},
            {'title': 'Another Song Entirely', 'artist': 'HOYO-MiX',
             'score': 1.0},
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_identity_recordings_own_version_blocks_rescue(self):
        # The alternate agrees with the expected title, but the recording
        # itself is the instrumental: a release that dropped the
        # qualifier from its track listing does not change the audio.
        recs = [
            {'title': 'Sins', 'artist': 'HOYO-MiX', 'score': 0.90},
            {'title': 'Totally Other (Instrumental)', 'artist': 'HOYO-MiX',
             'score': 1.0, 'alternate_titles': ['Myriad Sins']},
        ]
        out = evaluate('Myriad Sins', 'HOYO-MiX', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_accept_version_allows_identity_for_an_asked_for_version(self):
        # The version gate's own loosening: the caller went after the
        # instrumental on purpose, and a release titles that recording
        # with the bare expected title.
        out = evaluate(
            'Myriad Sins', 'HOYO-MiX',
            [{'title': 'Myriad Sins (Instrumental)', 'artist': 'HOYO-MiX',
              'score': 1.0, 'alternate_titles': ['Myriad Sins']}],
            fingerprint_score=1.0, accept_version='instrumental')
        assert out.decision == Decision.PASS

    def test_bracketed_unmodelled_version_alternate_does_not_rescue(self):
        # "(Piano Version)" is not a version label the detector models,
        # but it is still a different version: identity is judged on the
        # qualifier-preserving recording identity, not the
        # bracket-stripping similarity score.
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [_reporter_recording(
                alternate_titles=['Myriad Sins (Piano Version)'])],
            fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_same_title_top_tie_cover_does_not_rescue(self):
        # Two recordings tied at the top with the SAME canonical title —
        # the original and a cover. The title-keyed ambiguity check sees
        # one title and calls it unambiguous, but the top is still a coin
        # flip between two different recordings, so identity must not
        # pick the cover just because its release titles fit.
        recs = [
            {'title': CANONICAL, 'artist': 'HOYO-MiX', 'mbid': 'mb-orig',
             'score': 1.0},
            {'title': CANONICAL, 'artist': 'Cover Band', 'mbid': 'mb-cover',
             'score': 1.0, 'alternate_titles': ['Myriad Sins']},
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_identity_not_vetoed_by_another_recordings_version(self):
        # The version gate judges the best-by-similarity recording — an
        # instrumental namesake at a lower fingerprint score. Its
        # mismatch must not veto an identity established on the
        # fingerprint's own top candidate.
        recs = [
            {'title': 'Myriad Sins (Instrumental)', 'artist': 'HOYO-MiX',
             'score': 0.90},
            _reporter_recording(alternate_titles=list(ALTERNATES)),
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.PASS

    def test_weak_fingerprint_does_not_rescue(self):
        # Identity needs an absolutely strong fingerprint, not merely
        # the best of a weak bunch.
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [_reporter_recording(alternate_titles=list(ALTERNATES),
                                 score=0.42)],
            fingerprint_score=0.42)
        assert out.decision != Decision.PASS

    def test_titleless_recording_does_not_rescue(self):
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [{'title': '', 'artist': 'HOYO-MiX', 'score': 1.0,
              'alternate_titles': ['Myriad Sins']}],
            fingerprint_score=1.0)
        assert out.decision != Decision.PASS

    def test_alias_title_identity_passes_and_says_alias(self):
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [_reporter_recording(alias_titles=['Myriad Sins'])],
            fingerprint_score=1.0)
        assert out.decision == Decision.PASS
        assert 'alias' in out.reason
        assert 'another release' not in out.reason

    def test_accept_version_straddle_is_the_asked_for_version(self):
        # Canonical carries the asked-for version, the alternate the
        # bare title: the recording's version is what the caller fetched
        # on purpose, the alternate is only the name bridge.
        out = evaluate(
            'Myriad Sins', 'HOYO-MiX',
            [{'title': 'Totally Other (Remix)', 'artist': 'HOYO-MiX',
              'score': 1.0, 'alternate_titles': ['Myriad Sins']}],
            fingerprint_score=1.0, accept_version='remix')
        assert out.decision == Decision.PASS

    def test_same_title_top_tie_cover_unscored_does_not_rescue(self):
        # The unique-top gate must not depend on scores being present:
        # an unscored original/cover pair is the same coin flip.
        recs = [
            {'title': CANONICAL, 'artist': 'HOYO-MiX', 'mbid': 'mb-orig'},
            {'title': CANONICAL, 'artist': 'Cover Band', 'mbid': 'mb-cover',
             'alternate_titles': ['Myriad Sins']},
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_nan_score_carrier_does_not_rescue(self):
        recs = [
            {'title': 'Real Wrong Song', 'artist': 'HOYO-MiX',
             'score': 0.99},
            {'title': 'Unrelated Canon', 'artist': 'HOYO-MiX',
             'score': float('nan'), 'alternate_titles': ['Myriad Sins']},
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=0.99)
        assert out.decision != Decision.PASS

    def test_infinite_score_carrier_does_not_rescue(self):
        recs = [
            {'title': 'Real Wrong Song', 'artist': 'HOYO-MiX',
             'score': 0.99},
            {'title': 'Unrelated Canon', 'artist': 'HOYO-MiX',
             'score': float('inf'), 'alternate_titles': ['Myriad Sins']},
        ]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=0.99)
        assert out.decision != Decision.PASS

    def test_nonfinite_fingerprint_score_does_not_rescue(self):
        for bad_score in (float('nan'), float('inf')):
            out = evaluate(
                'Myriad Sins', 'Linyao Feng',
                [_reporter_recording(alternate_titles=list(ALTERNATES),
                                     score=0.5)],
                fingerprint_score=bad_score)
            assert out.decision != Decision.PASS, bad_score

    def test_release_provenance_wins_across_duplicate_copies(self):
        # Same recording twice: one copy carries the name as an alias,
        # the other as a release title. The PASS reason must report the
        # release listing, not the alias.
        base = {'title': CANONICAL, 'artist': 'HOYO-MiX', 'mbid': MBID,
                'score': 1.0}
        recs = [dict(base, alias_titles=['Myriad Sins']),
                dict(base, alternate_titles=['Myriad Sins'])]
        out = evaluate('Myriad Sins', 'Linyao Feng', recs,
                       fingerprint_score=1.0)
        assert out.decision == Decision.PASS
        assert 'another release' in out.reason

    def test_malformed_alternate_titles_do_not_crash_or_rescue(self):
        for bad in ('Myriad Sins', [123], [{'name': 'Myriad Sins'}], [None]):
            out = evaluate(
                'Myriad Sins', 'Linyao Feng',
                [_reporter_recording(alternate_titles=bad)],
                fingerprint_score=1.0)
            assert out.decision == Decision.FAIL, bad

    def test_traditional_chinese_alternate_is_identity(self):
        out = evaluate(
            '眾罪如霜露', 'HOYO-MiX',
            [_reporter_recording(alternate_titles=list(ALTERNATES))],
            fingerprint_score=1.0)
        assert out.decision == Decision.PASS

    def test_same_title_cover_still_fails(self):
        # The alternate set adds nothing when it only repeats the canonical
        # title: a same-title recording by a different artist is judged by
        # the ordinary comparison and still fails.
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [{'title': 'Myriad Sins', 'artist': 'Someone Else',
              'alternate_titles': ['Myriad Sins']}],
            fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_alternate_with_a_different_version_does_not_rescue(self):
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [_reporter_recording(
                alternate_titles=['Myriad Sins (Instrumental)'])],
            fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_merely_similar_alternate_does_not_rescue(self):
        out = evaluate(
            'Myriad Sins', 'Linyao Feng',
            [_reporter_recording(
                alternate_titles=['Myriad Sins and Sorrows'])],
            fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_unrelated_alternates_do_not_rescue_a_wrong_song(self):
        out = evaluate(
            'Yellow', 'Coldplay',
            [{'title': 'Rich Interlude', 'artist': 'Kendrick Lamar',
              'alternate_titles': ['Violet Skies', 'Interlude (Live)']}],
            fingerprint_score=0.85)
        assert out.decision == Decision.FAIL

    def test_no_alternate_titles_key_behaves_as_before(self):
        out = evaluate(
            'Dirty White Boy', 'Foreigner',
            [{'title': 'Dirty White Boy', 'artist': 'Foreigner'}],
            fingerprint_score=0.95)
        assert out.decision == Decision.PASS


# ---------------------------------------------------------------------------
# Alternate-title resolution from MusicBrainz
# ---------------------------------------------------------------------------


class TestSameScriptRetitleNeedsArtistEvidence:
    """the rescue skips the artist check, which is only safe for a retitle
    into another script (the reporter's chinese -> english case). with
    both titles latin and the artist wrong, an exact other-release title
    is not identity: some release listing a different artist's track as
    "Intro" would otherwise bless that wrong file."""

    def _wrong_artist_intro(self):
        return {'title': 'Prelude', 'artist': 'Some Other Band', 'mbid': 'm-x',
                'score': 1.0, 'alternate_titles': ['Intro']}

    def test_same_script_retitle_with_wrong_artist_does_not_rescue(self):
        out = evaluate('Intro', 'The Expected Artist', [self._wrong_artist_intro()],
                       fingerprint_score=1.0)
        assert out.decision == Decision.FAIL

    def test_same_script_retitle_with_matching_artist_rescues(self):
        rec = dict(self._wrong_artist_intro(), artist='The Expected Artist')
        out = evaluate('Intro', 'The Expected Artist', [rec], fingerprint_score=1.0)
        assert out.decision == Decision.PASS
        assert 'Intro' in out.reason

    def test_same_script_retitle_with_artist_alias_rescues(self):
        out = evaluate('Intro', 'Expected Alias', [self._wrong_artist_intro()],
                       fingerprint_score=1.0,
                       aliases_provider=['Some Other Band'])
        assert out.decision == Decision.PASS

    def test_same_script_retitle_without_expected_artist_rescues(self):
        out = evaluate('Intro', '', [self._wrong_artist_intro()], fingerprint_score=1.0)
        assert out.decision == Decision.PASS

    def test_cross_script_retitle_still_needs_no_artist(self):
        # the reporter's exact shape stays rescued.
        out = evaluate('Myriad Sins', 'Linyao Feng',
                       [_reporter_recording(alternate_titles=list(ALTERNATES))],
                       fingerprint_score=1.0)
        assert out.decision == Decision.PASS


def _mb_payload():
    """Shape of get_recording(inc=releases+media+aliases) for the
    reporter's recording (verified live against MusicBrainz): ``media``
    carries only this recording's track on each release."""
    return {
        'title': CANONICAL,
        'aliases': [{'name': 'All Sins Like Frost and Dew'}],
        'releases': [
            {'title': 'Genshin Impact - The Shimmering Voyage Vol. 6',
             'media': [{'tracks': [{'title': 'Myriad Sins'}]}]},
            {'title': '原神 - 真珠の歌Vol.6',
             'media': [{'tracks': [{'title': '衆罪は霜露の如し'}]}]},
            {'title': '原神-珍珠之歌6',
             'media': [{'tracks': [{'title': CANONICAL}]}]},
        ],
    }


class TestResolveAlternateTitles:
    def test_collects_release_titles_and_aliases(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = _mb_payload()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = _reporter_recording()
        assert _resolve_alternate_titles([rec]) is True
        # Release track titles and recording aliases are kept apart: the
        # alias is weaker evidence and the PASS reason must not claim a
        # release listing for it.
        assert rec['alternate_titles'] == ['Myriad Sins', '衆罪は霜露の如し']
        assert rec['alias_titles'] == ['All Sins Like Frost and Dew']
        fake_mb.get_recording.assert_called_once_with(
            MBID, includes=['releases', 'media', 'aliases'],
            raise_on_error=True)

    def test_canonical_variants_are_not_alternates(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = {
            'title': 'Song',
            'aliases': [],
            'releases': [{'media': [{'tracks': [
                {'title': 'Song'}, {'title': 'song'}]}]}],
        }
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = {'title': 'Song', 'artist': 'A', 'mbid': 'mb-1'}
        assert _resolve_alternate_titles([rec]) is False
        assert 'alternate_titles' not in rec

    def test_search_hint_aliases_are_excluded(self, monkeypatch):
        fake_mb = MagicMock()
        payload = _mb_payload()
        payload['aliases'] = [
            {'name': 'All Sins Like Frost and Dew'},
            {'name': 'Myriad Sinz', 'type': 'Search hint'},
        ]
        fake_mb.get_recording.return_value = payload
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = _reporter_recording()
        assert _resolve_alternate_titles([rec]) is True
        assert rec['alias_titles'] == ['All Sins Like Frost and Dew']

    def test_sibling_track_with_another_recording_id_is_excluded(
            self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = {
            'title': CANONICAL,
            'aliases': [],
            'releases': [{'media': [{'tracks': [
                {'title': 'Sibling Song', 'recording': {'id': 'other-id'}},
                {'title': 'Myriad Sins', 'recording': {'id': MBID}},
                {'title': 'Unreferenced Title'},
            ]}]}],
        }
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = _reporter_recording()
        assert _resolve_alternate_titles([rec]) is True
        assert rec['alternate_titles'] == ['Myriad Sins', 'Unreferenced Title']

    def test_duplicate_mbids_resolve_once_and_fill_every_copy(
            self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = _mb_payload()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        recs = [_reporter_recording(score=0.85),
                _reporter_recording(score=1.0)]
        assert _resolve_alternate_titles(recs) is True
        fake_mb.get_recording.assert_called_once()
        # Every copy gains the titles — the top-scored copy is the one
        # the identity check can use, so filling only the first dict
        # would leave the rescue structurally unreachable.
        assert all('Myriad Sins' in r['alternate_titles'] for r in recs)

    def test_lookup_cap_applies(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = _mb_payload()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        recs = [_reporter_recording(mbid=f'mb-{i}') for i in range(5)]
        assert _resolve_alternate_titles(recs) is True
        # MAX_MB_ENRICHMENT_LOOKUPS (3) bounds the resolution calls per
        # verification (the earlier title/artist enrichment has its own
        # budget of the same size).
        assert fake_mb.get_recording.call_count == 3

    def test_budget_is_spent_on_the_highest_scores_first(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = _mb_payload()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        noise = [{'title': f'Noise {i}', 'artist': 'X', 'mbid': f'n-{i}',
                  'score': 0.5} for i in range(3)]
        target = _reporter_recording()
        assert _resolve_alternate_titles(noise + [target]) is True
        # List order would spend all 3 calls on the noise; the identity
        # candidate at the top score is resolved first instead.
        assert 'Myriad Sins' in target['alternate_titles']
        called_mbids = [c.args[0] for c in fake_mb.get_recording.call_args_list]
        assert MBID in called_mbids

    def test_negative_resolution_is_cached(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = {
            'title': CANONICAL,
            'aliases': [],
            'releases': [{'media': [{'tracks': [{'title': CANONICAL}]}]}],
        }
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        assert _resolve_alternate_titles([_reporter_recording()]) is False
        assert _resolve_alternate_titles([_reporter_recording()]) is False
        # "This recording has no other names" is a resolution too — a
        # bulk scan must not re-pay the lookup for it on every file.
        fake_mb.get_recording.assert_called_once()

    def test_cache_is_canonical_relative(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = {
            'title': 'Song',
            'aliases': [],
            'releases': [{'media': [{'tracks': [{'title': 'Other Name'}]}]}],
        }
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        first = {'title': 'Song', 'artist': 'A', 'mbid': 'mb-x'}
        second = {'title': 'Other Name', 'artist': 'A', 'mbid': 'mb-x'}
        assert _resolve_alternate_titles([first]) is True
        assert first['alternate_titles'] == ['Other Name']
        assert _resolve_alternate_titles([second]) is True
        # The cache holds the raw titles; what counts as "alternate" is
        # judged against each recording's own canonical title.
        assert second['alternate_titles'] == ['Song']
        fake_mb.get_recording.assert_called_once()

    def test_positive_resolution_is_cached_per_mbid(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = _mb_payload()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        first = _reporter_recording()
        second = _reporter_recording()
        assert _resolve_alternate_titles([first]) is True
        assert _resolve_alternate_titles([second]) is True
        assert second['alternate_titles'] == first['alternate_titles']
        fake_mb.get_recording.assert_called_once()

    def test_unknown_mbid_negative_is_cached(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = None  # genuine MB 404
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        assert _resolve_alternate_titles([_reporter_recording()]) is False
        assert _resolve_alternate_titles([_reporter_recording()]) is False
        fake_mb.get_recording.assert_called_once()

    def test_partially_populated_recording_fills_the_missing_kind(
            self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = _mb_payload()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = _reporter_recording(alias_titles=['Preset Alias'])
        assert _resolve_alternate_titles([rec]) is True
        assert 'Myriad Sins' in rec['alternate_titles']

    def test_string_recording_reference_is_filtered(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.return_value = {
            'title': CANONICAL,
            'aliases': [],
            'releases': [{'media': [{'tracks': [
                {'title': 'Sibling Song', 'recording': 'other-id'},
                {'title': 'Myriad Sins', 'recording': MBID},
            ]}]}],
        }
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = _reporter_recording()
        assert _resolve_alternate_titles([rec]) is True
        assert rec['alternate_titles'] == ['Myriad Sins']

    def test_negative_cache_entry_expires_before_positive(self, monkeypatch):
        import time as _time
        from core.acoustid_verification import (
            _alternate_titles_cache, _alternate_titles_cache_get,
            _alternate_titles_cache_put)
        _alternate_titles_cache_put('mb-neg', [CANONICAL], [])
        _alternate_titles_cache_put('mb-pos', [CANONICAL, 'Other Name'], [])
        # Age both entries past the negative TTL (1h) but not the
        # positive one (24h).
        for mbid in ('mb-neg', 'mb-pos'):
            stored_at, positive, rel, ali = _alternate_titles_cache[mbid]
            _alternate_titles_cache[mbid] = (
                _time.monotonic() - 2 * 3600, positive, rel, ali)
        assert _alternate_titles_cache_get('mb-neg') is None
        assert _alternate_titles_cache_get('mb-pos') is not None

    def test_mb_client_construction_failure_is_best_effort(
            self, monkeypatch):
        def _boom():
            raise RuntimeError('no client')
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', _boom)

        assert _resolve_alternate_titles([_reporter_recording()]) is False

    def test_recordings_without_mbid_are_skipped(self, monkeypatch):
        fake_mb = MagicMock()
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        assert _resolve_alternate_titles(
            [{'title': 'X', 'artist': 'Y'}]) is False
        fake_mb.get_recording.assert_not_called()

    def test_musicbrainz_failure_is_best_effort(self, monkeypatch):
        fake_mb = MagicMock()
        fake_mb.get_recording.side_effect = Exception('MB down')
        monkeypatch.setattr(
            'core.acoustid_verification._get_mb_client', lambda: fake_mb)

        rec = _reporter_recording()
        assert _resolve_alternate_titles([rec]) is False
        assert 'alternate_titles' not in rec


# ---------------------------------------------------------------------------
# End to end through the verifier
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed_verifier(monkeypatch):
    verifier = AcoustIDVerification()
    verifier.acoustid_client = MagicMock()
    verifier.acoustid_client.is_available.return_value = (True, '')

    fake_service = MagicMock()
    fake_service.lookup_artist_aliases.return_value = []
    monkeypatch.setattr(
        'core.acoustid_verification._get_mb_service', lambda: fake_service)

    fake_mb = MagicMock()
    fake_mb.get_recording.return_value = None
    monkeypatch.setattr(
        'core.acoustid_verification._get_mb_client', lambda: fake_mb)

    return verifier, fake_mb


class TestVerifierEndToEnd:
    def test_reporter_case_passes_after_alternate_resolution(
            self, stubbed_verifier):
        verifier, fake_mb = stubbed_verifier
        fake_mb.get_recording.return_value = _mb_payload()
        verifier.acoustid_client.lookup_with_status.return_value = {
            'best_score': 1.0,
            'recordings': [_reporter_recording()],
        }

        result, msg = verifier.verify_audio_file(
            '/fake/Disk 4 - 4.08 Myriad Sins.mp3',
            'Myriad Sins', 'Linyao Feng')

        assert result == VerificationResult.PASS, msg
        assert 'different release title' in msg
        fake_mb.get_recording.assert_called_once_with(
            MBID, includes=['releases', 'media', 'aliases'],
            raise_on_error=True)

    def test_no_musicbrainz_call_when_direct_match_passes(
            self, stubbed_verifier):
        verifier, fake_mb = stubbed_verifier
        verifier.acoustid_client.lookup_with_status.return_value = {
            'best_score': 0.95,
            'recordings': [{'title': 'Dirty White Boy',
                            'artist': 'Foreigner', 'mbid': 'mb-9'}],
        }

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Dirty White Boy', 'Foreigner')

        assert result == VerificationResult.PASS
        fake_mb.get_recording.assert_not_called()

    def test_wrong_song_still_fails_after_resolution(
            self, stubbed_verifier):
        verifier, fake_mb = stubbed_verifier
        fake_mb.get_recording.return_value = _mb_payload()
        verifier.acoustid_client.lookup_with_status.return_value = {
            'best_score': 1.0,
            'recordings': [_reporter_recording()],
        }

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Completely Different Song', 'Another Artist')

        assert result == VerificationResult.FAIL

    def test_musicbrainz_failure_keeps_original_verdict(
            self, stubbed_verifier):
        verifier, fake_mb = stubbed_verifier
        fake_mb.get_recording.side_effect = Exception('MB down')
        verifier.acoustid_client.lookup_with_status.return_value = {
            'best_score': 1.0,
            'recordings': [_reporter_recording()],
        }

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Myriad Sins', 'Linyao Feng')

        # Pre-fix verdict, unchanged: the resolution is best-effort and a
        # MusicBrainz outage must not turn a decision into an error.
        assert result == VerificationResult.FAIL

    def test_ordinary_skip_pays_no_musicbrainz_call(self, stubbed_verifier):
        # Cross-script title + matching artist at an ordinary fingerprint
        # score is a SKIP, and resolution is not attempted: in a bulk
        # scan of a foreign catalogue that is the common case, and it
        # must not cost MusicBrainz calls per file.
        verifier, fake_mb = stubbed_verifier
        fake_mb.get_recording.return_value = _mb_payload()
        verifier.acoustid_client.lookup_with_status.return_value = {
            'best_score': 0.90,
            'recordings': [_reporter_recording()],
        }

        result, _ = verifier.verify_audio_file(
            '/fake/path.mp3', 'Myriad Sins', 'HOYO-MiX')

        assert result == VerificationResult.SKIP
        fake_mb.get_recording.assert_not_called()

    def test_high_confidence_skip_resolves_to_pass(self, stubbed_verifier):
        # Same shape at a near-certain fingerprint: the SKIP rests only
        # on the title being unreadable across scripts, and the release
        # titles resolve it to identity.
        verifier, fake_mb = stubbed_verifier
        fake_mb.get_recording.return_value = _mb_payload()
        verifier.acoustid_client.lookup_with_status.return_value = {
            'best_score': 0.99,
            'recordings': [_reporter_recording()],
        }

        result, msg = verifier.verify_audio_file(
            '/fake/path.mp3', 'Myriad Sins', 'HOYO-MiX')

        assert result == VerificationResult.PASS, msg
        fake_mb.get_recording.assert_called_once()
