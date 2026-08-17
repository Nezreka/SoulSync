"""Preferring a specific version of a track, and the detector bug in the way.

Two things live here because one blocked the other.

**The bug.** ``detect_version_type`` walks a dict of version patterns and stops
at the first hit. ``\\bedit\\b`` is one of the *remix* patterns and remix used to
sit first, so "Radio Edit" and "Clean Edit" came back as remixes. Remix is
reject-on-sight, so a radio edit scored 0.0 and got thrown away — including when
the source title asked for the radio edit by name. The 'radio' entry with its
gentle 0.08 penalty was unreachable.

**The feature.** A user adds a track to a Spotify playlist and only the radio
edit exists there, but they want the extended mix whenever one is out there.
Rather than rewriting the request (which rejects the plain version and leaves you
with nothing when no extended mix exists), the preference is expressed at the
moment of picking: search the same, rank differently.

Deciding whether a candidate really is the asked-for version OF THIS TRACK was
the hard part, and the first two attempts were both wrong. Confidence scores the
whole file PATH with fuzzy similarity, and real Soulseek paths are mostly noise
("@@dl/", "[FLAC]", "VA/Compilation Vol 3/"). Measured against 'Song' the right
file scored 0.751 and a wrong one 0.743 — eight thousandths apart — while
genuine matches in messy folders scored no better than impostors. An absolute
threshold fired on 2 of 7 realistic filename shapes; a delta threshold couldn't
separate them at all.

So the gate compares the TITLE, not the path: reduce both sides to the bare song
name and require equality. Exact match, no threshold to get wrong.

**Two holes a later review found, both covered below.** Ranking in the matching
engine turned out not to be the last word: the download walk re-sorted the same
list confidence-first and put the plain version straight back on top, so the
setting did nothing at all. And AcoustID's version gate compared the source
title's version against the fingerprinted recording's and quarantined the file
the setting had just gone and found. Neither showed up in the engine's own
tests, because neither lives in the engine — the lesson being that a ranking
change is only real if you follow the file all the way to disk.

The other load-bearing claim is that it is OFF by default and provably inert
when off — ``preferred_hit`` is 0 for every candidate with no preference set, so
the sort tuple compares exactly as it did before.
"""

from types import SimpleNamespace

import pytest

import core.matching_engine as me
from core.matching_engine import MusicMatchingEngine
from core.downloads.candidates import preferred_version_stamp
from core.imports.file_integrity import duration_reference_for_context


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _AngryConfig:
    """Config that raises on every read — the feature must fall to off."""

    def get(self, key, default=None):
        raise RuntimeError("config is unavailable")


@pytest.fixture
def engine():
    return MusicMatchingEngine()


def _set_config(monkeypatch, **values):
    monkeypatch.setattr(me, 'config_manager', _Config(values))


def _prefer(monkeypatch, version):
    _set_config(monkeypatch, **{'soulseek.preferred_version': version})


def _candidate(filename, *, duration=200_000, size=100, quality_score=0.5):
    return SimpleNamespace(
        filename=filename, duration=duration, quality='flac', bitrate=1000,
        size=size, username='peer', album=None, quality_score=quality_score,
        queue_length=0,
    )


def _source(name='Song', duration_ms=200_000):
    return SimpleNamespace(name=name, artists=['Artist'],
                           duration_ms=duration_ms, album='Album')


def _score(engine, wanted, filename, base=0.90):
    """Adjusted confidence for one candidate, with the base score pinned so
    only the version handling is under test."""
    engine.calculate_slskd_match_confidence = lambda *_a, **_k: base
    return engine.calculate_slskd_match_confidence_enhanced(
        SimpleNamespace(name=wanted), SimpleNamespace(filename=filename))


# ── reading the setting ─────────────────────────────────────────────────────

def test_preference_defaults_to_off(monkeypatch):
    _set_config(monkeypatch)
    assert MusicMatchingEngine._preferred_version() == ''


@pytest.mark.parametrize("raw,expected", [
    ('extended', 'extended'),
    ('  Extended  ', 'extended'),
    ('EXTENDED', 'extended'),
    ('', ''),
    (None, ''),
    ('bogus', ''),
    ('original', ''),          # not something you can ask us to prefer
    ('wrong_version', ''),
    (12345, ''),
])
def test_preference_only_accepts_labels_the_detector_produces(monkeypatch, raw, expected):
    _prefer(monkeypatch, raw)
    assert MusicMatchingEngine._preferred_version() == expected


def test_broken_config_turns_the_feature_off(monkeypatch):
    monkeypatch.setattr(me, 'config_manager', _AngryConfig())
    assert MusicMatchingEngine._preferred_version() == ''


def test_every_preferable_label_is_one_the_detector_can_return(engine):
    """A preference that no filename could ever match would silently do nothing.
    Each allowed value has to be reachable from a real filename."""
    samples = {
        'extended': 'Song (Extended Mix).flac',
        'radio': 'Song (Radio Edit).mp3',
        'remix': 'Song (Remix).mp3',
        'live': 'Song (Live).flac',
        'acoustic': 'Song (Acoustic).flac',
        'instrumental': 'Song (Instrumental).flac',
        'demo': 'Song (Demo).mp3',
        'clean': 'Song (Clean).mp3',
        'explicit': 'Song (Explicit).flac',
    }
    assert set(samples) == set(MusicMatchingEngine.PREFERABLE_VERSIONS)
    for label, filename in samples.items():
        assert engine.detect_version_type(filename)[0] == label, filename


# ── off by default: the ordering must not move ──────────────────────────────

def _rank(engine, monkeypatch, preference, results, source=None):
    _prefer(monkeypatch, preference)
    ranked = engine.find_best_slskd_matches_enhanced(source or _source(), list(results))
    return [r.filename for r in ranked]


def test_ordering_is_untouched_when_off(engine, monkeypatch):
    results = [
        _candidate('Artist/Album/01 - Song (Extended Mix).flac', duration=380_000),
        _candidate('Artist/Album/01 - Song.flac'),
    ]
    assert _rank(engine, monkeypatch, '', results) == [
        'Artist/Album/01 - Song.flac',
        'Artist/Album/01 - Song (Extended Mix).flac',
    ]


def test_unknown_preference_orders_exactly_like_off(engine, monkeypatch):
    results = [
        _candidate('Artist/Album/01 - Song (Extended Mix).flac', duration=380_000),
        _candidate('Artist/Album/01 - Song.flac'),
    ]
    assert (_rank(engine, monkeypatch, 'not-a-version', results)
            == _rank(engine, monkeypatch, '', results))


def test_scores_are_identical_with_and_without_an_unrelated_preference(engine, monkeypatch):
    """Preferring 'live' must not perturb the score of an extended mix. The
    preference decides order, it does not touch the confidence number — which
    is compared against cutoffs elsewhere and shown in the UI."""
    _prefer(monkeypatch, '')
    off, _ = _score(engine, 'Song', 'Artist - Song (Extended Mix).flac')
    _prefer(monkeypatch, 'live')
    unrelated, _ = _score(engine, 'Song', 'Artist - Song (Extended Mix).flac')
    assert off == unrelated


# ── on: the requested version wins, and nothing is lost when it is absent ───

def test_preferred_version_wins_despite_lower_confidence(engine, monkeypatch):
    results = [
        _candidate('Artist/Album/01 - Song.flac'),
        _candidate('Artist/Album/01 - Song (Extended Mix).flac', duration=380_000),
    ]
    ranked = _rank(engine, monkeypatch, 'extended', results)
    assert ranked[0] == 'Artist/Album/01 - Song (Extended Mix).flac'


def test_plain_version_survives_so_it_can_still_be_taken(engine, monkeypatch):
    """The whole reason for ranking instead of rewriting the request: the plain
    version is never rejected, so it is still there to fall back to."""
    results = [
        _candidate('Artist/Album/01 - Song.flac'),
        _candidate('Artist/Album/01 - Song (Extended Mix).flac', duration=380_000),
    ]
    assert 'Artist/Album/01 - Song.flac' in _rank(engine, monkeypatch, 'extended', results)


def test_nothing_changes_when_the_preferred_version_is_not_out_there(engine, monkeypatch):
    """No extended mix in the results, so the ordinary pick must be unaffected.
    This is the case that would have broken under a rewritten search."""
    results = [
        _candidate('Artist/Album/01 - Song.flac'),
        _candidate('Artist/Album/02 - Other Song.flac'),
    ]
    assert (_rank(engine, monkeypatch, 'extended', results)
            == _rank(engine, monkeypatch, '', results))


@pytest.mark.parametrize("label,filename", [
    ('live', 'Artist - Song (Live).flac'),
    ('remix', 'Artist - Song (Remix).flac'),
    ('acoustic', 'Artist - Song (Acoustic).flac'),
    ('instrumental', 'Artist - Song (Instrumental).flac'),
])
def test_preferring_a_reject_on_sight_version_lets_it_through(engine, monkeypatch,
                                                              label, filename):
    """These four are normally dropped when the source title doesn't name them.
    Asking for one on purpose is exactly the case the rejection must not apply
    to — and each branch carries its own guard, so each needs its own test."""
    _prefer(monkeypatch, label)
    confidence, version = _score(engine, 'Song', filename)
    assert version == label
    assert confidence > 0.58


@pytest.mark.parametrize("preferred,filename", [
    ('live', 'Artist - Song (Remix).flac'),
    ('remix', 'Artist - Song (Live).flac'),
    ('acoustic', 'Artist - Song (Instrumental).flac'),
    ('instrumental', 'Artist - Song (Acoustic).flac'),
])
def test_preferring_one_version_does_not_admit_the_others(engine, monkeypatch,
                                                          preferred, filename):
    """Asking for one version must not quietly open the door to the rest."""
    _prefer(monkeypatch, preferred)
    confidence, version = _score(engine, 'Song', filename)
    assert confidence == 0.0
    assert version == 'rejected_version_mismatch'


def test_preferred_version_keeps_its_penalty_off(engine, monkeypatch):
    """A variant is normally docked for not being the original. If it is what
    was asked for, that is not a demerit."""
    _prefer(monkeypatch, '')
    docked, _ = _score(engine, 'Song', 'Artist - Song (Extended Mix).flac')
    _prefer(monkeypatch, 'extended')
    asked_for, _ = _score(engine, 'Song', 'Artist - Song (Extended Mix).flac')
    assert asked_for > docked
    assert asked_for == pytest.approx(0.90)


# ── carrying the label is not enough: it has to be THIS track ───────────────

_PLAIN = ('Artist/Album/01 - Song.flac', 200_000)
_RIGHT = ('Artist/Album/01 - Song (Extended Mix).flac', 380_000)
_IMPOSTOR = ('Artist/Album/05 - Song Two (Extended Mix).flac', 380_000)
_FAR = ('Artist/Album/09 - Completely Different (Extended Mix).flac', 380_000)


def _top(engine, monkeypatch, preference, files):
    _prefer(monkeypatch, preference)
    pool = [_candidate(name, duration=duration) for name, duration in files]
    ranked = engine.find_best_slskd_matches_enhanced(_source(), pool)
    return ranked[0].filename if ranked else None


def test_a_different_song_wearing_the_label_does_not_win(engine, monkeypatch):
    """The one that has to hold. "Song Two (Extended Mix)" is an extended mix,
    just not of the song we asked for. Ranked above confidence it beat the
    correct track — and plain confidence can't tell them apart (0.751 vs 0.743),
    so the gate scores against the title plus the qualifier instead."""
    assert _top(engine, monkeypatch, 'extended', [_PLAIN, _IMPOSTOR]) == _PLAIN[0]


def test_the_real_extended_mix_still_wins(engine, monkeypatch):
    """The gate must not be so tight it refuses the thing it exists to find."""
    assert _top(engine, monkeypatch, 'extended', [_PLAIN, _RIGHT]) == _RIGHT[0]


def test_the_right_one_is_picked_out_of_a_crowded_pool(engine, monkeypatch):
    assert _top(engine, monkeypatch, 'extended',
                [_PLAIN, _IMPOSTOR, _RIGHT, _FAR]) == _RIGHT[0]


def test_an_extended_mix_alone_is_still_taken(engine, monkeypatch):
    assert _top(engine, monkeypatch, 'extended', [_RIGHT]) == _RIGHT[0]


def test_the_gate_is_inert_with_the_feature_off(engine, monkeypatch):
    assert _top(engine, monkeypatch, '', [_PLAIN, _RIGHT]) == _PLAIN[0]
    assert engine.preferred_match_ids(_source(), [], '') == set()
    assert engine.preferred_match_ids(_source(), [], 'extended') == set()


def test_asking_for_live_drops_the_blanket_cover_against_impostors(engine, monkeypatch):
    """A deliberate trade, pinned here so it stays a decision and not a surprise.

    The strict rejection dropped every live file the source hadn't named. That
    was a VERSION check, not a same-song check — blocking a live recording of a
    similarly-titled song was a side effect of it. Preferring live gives that
    side effect up, so an impostor can be taken when the real track is nowhere.

    It is not a new class of failure: the plain equivalent already downloads
    today (asserted below). Preferring a version just puts that version on the
    same footing untagged files have always had. Bounded by opt-in, by the
    confidence cut, and by the gate above — which still stops it outranking a
    correct match."""
    impostor = 'Artist/Album/05 - Song Two (Live).flac'

    _prefer(monkeypatch, '')
    assert engine.find_best_slskd_matches_enhanced(
        _source(), [_candidate(impostor)]) == [], "off, the blanket rejection holds"

    # the same wrong song without the version marker is accepted TODAY, with
    # the feature off and untouched by any of this
    plain_impostor = engine.find_best_slskd_matches_enhanced(
        _source(), [_candidate('Artist/Album/05 - Song Two.flac')])
    assert plain_impostor, "pre-existing exposure this feature is measured against"

    _prefer(monkeypatch, 'live')
    taken = engine.find_best_slskd_matches_enhanced(_source(), [_candidate(impostor)])
    assert taken, "preferring live admits it once nothing else is on offer"

    # but it must never beat the real track
    both = engine.find_best_slskd_matches_enhanced(
        _source(), [_candidate('Artist/Album/01 - Song.flac'), _candidate(impostor)])
    assert both[0].filename == 'Artist/Album/01 - Song.flac'


def test_every_preference_is_a_label_the_detector_returns(engine):
    """A preference no filename could ever produce would silently do nothing."""
    samples = {
        'extended': 'Song (Extended Mix).flac', 'radio': 'Song (Radio Edit).mp3',
        'remix': 'Song (Remix).mp3', 'live': 'Song (Live).flac',
        'acoustic': 'Song (Acoustic).flac', 'instrumental': 'Song (Instrumental).flac',
        'demo': 'Song (Demo).mp3', 'clean': 'Song (Clean).mp3',
        'explicit': 'Song (Explicit).flac',
    }
    assert set(samples) == set(MusicMatchingEngine.PREFERABLE_VERSIONS)
    for label, filename in samples.items():
        assert engine.detect_version_type(filename)[0] == label, filename


def test_a_bad_match_is_still_dropped_even_when_preferred(engine, monkeypatch):
    """The preference reorders candidates, it does not smuggle a wrong file
    past the confidence cut."""
    _prefer(monkeypatch, 'extended')
    results = [_candidate('Someone Else/Other Record/09 - A Different Song (Extended Mix).flac',
                          duration=380_000)]
    assert _rank(engine, monkeypatch, 'extended', results, _source('Song')) == []


# ── the sort that actually decides what downloads ───────────────────────────
#
# Ranking in the matching engine is NOT the last word. get_valid_candidates
# hands its list to attempt_download_with_candidates, which re-sorts it through
# order_candidates before walking it. That sort was confidence-first, so it put
# the plain version straight back on top and the whole feature came to nothing
# — the setting looked applied and changed the download not at all.

def _walk_candidate(filename, confidence, *, hit=False, quality=None):
    return SimpleNamespace(
        filename=filename, confidence=confidence, preferred_version_hit=hit,
        quality_score=50, upload_speed=100, queue_length=0,
        free_upload_slots=1, size=1000, audio_quality=quality,
    )


def test_the_download_walk_takes_the_preferred_version():
    """The regression that made the feature a no-op: the plain cut scores
    higher (it matches the source title exactly) so a confidence-first walk
    always took it, whatever the matching engine decided."""
    from core.downloads.candidates import order_candidates
    plain = _walk_candidate('Song.flac', 0.94)
    extended = _walk_candidate('Song (Extended Mix).flac', 0.86, hit=True)
    assert order_candidates([plain, extended])[0] is extended


def test_the_preferred_version_outranks_quality_too():
    """Best-quality mode ranks files OF THE SAME song. A preference picks the
    recording, and a different recording is a different song, so quality does
    not get to overrule it."""
    from core.downloads.candidates import order_candidates
    plain = _walk_candidate('Song.flac', 0.94)
    extended = _walk_candidate('Song (Extended Mix).mp3', 0.86, hit=True)
    ordered = order_candidates([plain, extended], quality_first=True, targets=[])
    assert ordered[0] is extended


@pytest.mark.parametrize("quality_first", [False, True])
def test_the_walk_order_is_untouched_with_nothing_stamped(quality_first):
    """Every other flow, and every download with the setting off, leaves the
    hit unset — so the term is 0 for all and the order is what it always was."""
    from core.downloads.candidates import order_candidates
    hi = _walk_candidate('Song.flac', 0.94)
    lo = _walk_candidate('Song (Extended Mix).flac', 0.86)
    assert order_candidates([lo, hi], quality_first=quality_first, targets=[])[0] is hi
    # not even the attribute present — an album/manual candidate object
    bare_hi = SimpleNamespace(filename='a', confidence=0.94, quality_score=50,
                              upload_speed=100, queue_length=0, free_upload_slots=1,
                              size=1000, audio_quality=None)
    bare_lo = SimpleNamespace(filename='b', confidence=0.10, quality_score=50,
                              upload_speed=100, queue_length=0, free_upload_slots=1,
                              size=1000, audio_quality=None)
    assert order_candidates([bare_lo, bare_hi], quality_first=quality_first,
                            targets=[])[0] is bare_hi


def test_the_engine_stamps_the_verdict_onto_the_files(engine, monkeypatch):
    """The two sorts have to read the same answer, and the download walk gets a
    plain list of files with no track and no config in reach. So the verdict
    rides on the file object."""
    _prefer(monkeypatch, 'extended')
    right = _candidate('Artist/Album/01 - Song (Extended Mix).flac', duration=380_000)
    wrong = _candidate('Artist/Album/02 - Song Two (Extended Mix).flac', duration=380_000)
    plain = _candidate('Artist/Album/01 - Song.flac')
    engine.find_best_slskd_matches_enhanced(_source(), [plain, right, wrong])
    assert right.preferred_version_hit is True
    assert wrong.preferred_version_hit is False
    assert plain.preferred_version_hit is False


def test_nothing_is_stamped_true_with_the_feature_off(engine, monkeypatch):
    _prefer(monkeypatch, '')
    right = _candidate('Artist/Album/01 - Song (Extended Mix).flac', duration=380_000)
    plain = _candidate('Artist/Album/01 - Song.flac')
    engine.find_best_slskd_matches_enhanced(_source(), [plain, right])
    assert not right.preferred_version_hit
    assert not plain.preferred_version_hit


# ── the AcoustID version gate ───────────────────────────────────────────────
#
# The other way a deliberate pick got quarantined. AcoustID compares the
# version in the SOURCE title against the version of the recording the
# fingerprint identified. Source says "Song" (original), the file really is the
# extended mix, so the gate called it a wrong song and quarantined it. Only
# bites users who turned AcoustID on, which is why it survived the first pass.

def _recordings(title, artist='Artist'):
    return [{'title': title, 'artist': artist}]


def _evaluate(title, expected='Song', accept=None):
    from core.matching.audio_verification import evaluate
    return evaluate(expected, 'Artist', _recordings(title),
                    fingerprint_score=0.95, accept_version=accept)


def test_a_deliberate_extended_mix_is_quarantined_without_the_stamp():
    """Pins the failure the stamp exists for."""
    from core.matching.audio_verification import Decision
    outcome = _evaluate('Song (Extended Mix)')
    assert outcome.decision is Decision.FAIL
    assert 'Version mismatch' in outcome.reason


def test_the_stamped_version_passes_the_gate():
    from core.matching.audio_verification import Decision
    assert _evaluate('Song (Extended Mix)', accept='extended').decision is Decision.PASS


def test_the_gate_only_forgives_the_version_that_was_asked_for():
    """Preferring extended must not wave through a remix or an instrumental."""
    from core.matching.audio_verification import Decision
    for other in ('Song (Remix)', 'Song (Instrumental)', 'Song (Acoustic)'):
        assert _evaluate(other, accept='extended').decision is Decision.FAIL, other


def test_a_wrong_song_still_fails_with_the_version_forgiven():
    """Only the version gate loosens. Title and artist still have to agree, so
    the protection this check exists for is intact."""
    from core.matching.audio_verification import Decision
    outcome = _evaluate('Something Else Entirely (Extended Mix)', accept='extended')
    assert outcome.decision is not Decision.PASS


def _verifier(title='Song (Extended Mix)'):
    """The import verifier with the fingerprint lookup stubbed. Title and artist
    are both filled in, so the MusicBrainz enrichment fast-path returns straight
    away and nothing reaches the network."""
    from core.acoustid_verification import AcoustIDVerification
    verifier = AcoustIDVerification()
    verifier.acoustid_client = SimpleNamespace(
        is_available=lambda: (True, 'ok'),
        lookup_with_status=lambda _path: {
            'status': 'ok', 'best_score': 0.95,
            'recordings': [{'title': title, 'artist': 'Artist'}],
        },
    )
    return verifier


def test_the_stamp_reaches_the_verifier():
    """The gate above is only loosened if the context stamp is actually read and
    handed down. Same seam the duration reference needed."""
    from core.acoustid_verification import VerificationResult
    stamped = {'_preferred_version_taken': 'extended'}
    assert _verifier().verify_audio_file(
        'x.flac', 'Song', 'Artist', stamped)[0] is VerificationResult.PASS
    assert _verifier().verify_audio_file(
        'x.flac', 'Song', 'Artist', {})[0] is VerificationResult.FAIL
    assert _verifier().verify_audio_file(
        'x.flac', 'Song', 'Artist', None)[0] is VerificationResult.FAIL


def test_the_gate_stays_strict_for_the_library_scan():
    """The scan calls evaluate with no context and no preference — it is looking
    at files on disk, not at something we just chose to fetch."""
    from core.matching.audio_verification import Decision
    assert _evaluate('Song (Extended Mix)', accept=None).decision is Decision.FAIL
    assert _evaluate('Song (Extended Mix)', accept='').decision is Decision.FAIL


# ── the stamp that stops a deliberate pick being quarantined ────────────────

def _picked(version_type='extended', duration=380_000, hit=True):
    """A candidate as the download walk sees it: scored, and carrying the
    matching engine's verdict on whether the preference actually chose it."""
    return SimpleNamespace(version_type=version_type, duration=duration,
                           preferred_version_hit=hit)


def test_no_stamp_for_an_ordinary_download():
    assert preferred_version_stamp(_picked('original', 200_000), '') == ('', None)


def test_no_stamp_when_the_pick_is_not_the_preferred_version():
    assert preferred_version_stamp(_picked('original', 200_000), 'extended') == ('', None)


def test_stamp_names_the_version_and_the_length_the_peer_advertised():
    assert preferred_version_stamp(_picked(), 'extended') == ('extended', 380_000)


@pytest.mark.parametrize("advertised", [None, 0, '', 'nonsense', -5])
def test_stamp_without_a_usable_length_means_skip_the_leg(advertised):
    """No honest reference, so the duration leg is skipped rather than guessed
    at. Size and parse legs still run. The version is still named — the AcoustID
    gate needs it whether or not the peer advertised a length."""
    assert preferred_version_stamp(_picked(duration=advertised), 'extended') == ('extended', None)


def test_stamp_ignores_a_candidate_with_no_version_recorded():
    assert preferred_version_stamp(SimpleNamespace(), 'extended') == ('', None)


def test_a_preference_for_original_can_never_stamp():
    """'original' is not a version, it is the absence of one — so preferring it
    must not put every plain download onto a different duration reference and
    open its AcoustID version gate. The dropdown can't produce it and
    ``_preferred_version`` won't return it, but this helper takes the preference
    as an argument and must hold the line on its own."""
    assert preferred_version_stamp(_picked('original', hit=True), 'original') == ('', None)


def test_a_label_carrier_that_is_a_different_song_is_never_stamped():
    """"Song Two (Extended Mix)" carries the label but the preference did not
    choose it — the matching engine already worked that out and left the hit
    unset. It can still be reached as a fallback candidate, and stamping it
    would loosen the duration and AcoustID gates for a genuinely wrong file."""
    assert preferred_version_stamp(_picked(hit=False), 'extended') == ('', None)
    assert preferred_version_stamp(SimpleNamespace(version_type='extended',
                                                   duration=380_000), 'extended') == ('', None)


@pytest.mark.parametrize("version_type", ['', None, 'original', 'extended'])
def test_no_stamp_at_all_while_the_feature_is_off(version_type):
    """Nothing may be written to the context with the setting off, whatever the
    candidate carries. A blank version_type is the one that bites: it equals the
    blank preference, so without the early return it would stamp itself and
    quietly move an ordinary download onto a different duration reference."""
    assert preferred_version_stamp(_picked(version_type), '') == ('', None)


def test_duration_reference_is_the_source_by_default():
    assert duration_reference_for_context(200_000, {}) == 200_000
    assert duration_reference_for_context(200_000, None) == 200_000


def test_duration_reference_prefers_the_stamped_length():
    context = {'_preferred_version_duration_ms': 380_000}
    assert duration_reference_for_context(200_000, context) == 380_000


def test_stamped_none_skips_the_duration_leg():
    context = {'_preferred_version_duration_ms': None}
    assert duration_reference_for_context(200_000, context) is None


def test_an_extended_mix_would_be_quarantined_without_the_stamp():
    """Pins why this plumbing exists. 3:20 expected against a 6:20 file drifts
    180s; the widest the check ever allows is 15s on the auto default and 60s
    if the user pins it. So without a stamp it is quarantined every time."""
    from core.imports import file_integrity as fi
    drift_s = (380_000 - 200_000) / 1000.0
    assert drift_s > fi._LONGER_VERSION_TOLERANCE_S
    assert drift_s > fi._MAX_USER_TOLERANCE_S


# ── the setting is actually wired to the UI ─────────────────────────────────

def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def test_settings_js_round_trips_the_preference():
    settings_js = _read('webui/static/settings.js')
    assert "preferred_version: document.getElementById('preferred-version')?.value || ''" in settings_js
    assert "settings.soulseek?.preferred_version || ''" in settings_js


def test_the_dropdown_exists_and_defaults_to_off():
    markup = _read('webui/index.html')
    assert 'id="preferred-version"' in markup
    # the empty option has to come first so an untouched install reads as off
    select_at = markup.index('id="preferred-version"')
    first_option = markup.index('<option', select_at)
    assert 'value=""' in markup[first_option:first_option + 40]


def test_every_offered_option_is_an_accepted_value():
    """A dropdown entry the backend silently discards would look like a setting
    that does nothing."""
    import re
    markup = _read('webui/index.html')
    select_at = markup.index('id="preferred-version"')
    block = markup[select_at:markup.index('</select>', select_at)]
    values = [v for v in re.findall(r'<option value="([^"]*)"', block) if v]
    assert values, "dropdown lost its options"
    for value in values:
        assert value in MusicMatchingEngine.PREFERABLE_VERSIONS, value


# ── the title gate: what separates the right song from a lookalike ──────────

@pytest.mark.parametrize("filename", [
    'Artist/Album/01 - Song (Extended Mix).flac',
    'Music/Artist/Album (2004)/01 Song (Extended Mix).flac',
    '@@dl/Artist - Album [FLAC]/01. Song (Extended Mix).flac',
    'Artist/Album/Song (Extended Mix).flac',
    'shared/Artist/2004 - Album/A1 Song (Extended Mix).flac',
    'Artist/Singles/Song (Extended Mix) [2004].flac',
    'VA/Compilation Vol 3/12 - Artist - Song (Extended Mix).flac',
    'Artist/Album/03 - Song - Extended Mix.flac',
])
def test_real_soulseek_shapes_reduce_to_the_bare_title(engine, filename):
    """These are the shapes that broke the previous gate. Scoring whole paths,
    the noise around the title swamped it and the preference fired on 2 of 7."""
    assert engine.base_title_of(filename, 'Artist', from_filename=True) == 'song'


@pytest.mark.parametrize("filename,expected", [
    ('Artist/Album/05 - Song Two (Extended Mix).flac', 'song two'),
    ('Artist/Album/09 - Another Song (Extended Mix).flac', 'another song'),
    ('Artist/Album/03 - Song Of Ice (Extended Mix).flac', 'song of ice'),
    ('Artist/Album/07 - The Song (Extended Mix).flac', 'the song'),
])
def test_a_different_song_reduces_to_a_different_title(engine, filename, expected):
    assert engine.base_title_of(filename, 'Artist', from_filename=True) == expected


def test_the_source_title_reduces_the_same_way(engine):
    """Both sides go through one function, so they meet in the same shape. A
    source that already names a version still reduces to the bare song."""
    assert engine.base_title_of('Song', 'Artist') == 'song'
    assert engine.base_title_of('Song (Radio Edit)', 'Artist') == 'song'
    assert engine.base_title_of('Song - Radio Edit', 'Artist') == 'song'


def test_the_gate_survives_junk_input(engine):
    for junk in ('', None, '   ', '.flac', '01 - '):
        assert isinstance(engine.base_title_of(junk, 'Artist'), str)


@pytest.mark.parametrize("plain,extended", [
    ('Artist/Album/01 - Song.flac',
     'Artist/Album/01 - Song (Extended Mix).flac'),
    ('Music/Artist/Album (2004)/01 Song.flac',
     'Music/Artist/Album (2004)/01 Song (Extended Mix).flac'),
    ('@@dl/Artist - Album [FLAC]/01. Song.flac',
     '@@dl/Artist - Album [FLAC]/01. Song (Extended Mix).flac'),
    ('Artist/Album/Song.flac', 'Artist/Album/Song (Extended Mix).flac'),
    ('shared/Artist/2004 - Album/A1 Song.flac',
     'shared/Artist/2004 - Album/A1 Song (Extended Mix).flac'),
    ('Artist/Album/01 - Song.flac', 'Artist/Singles/Song (Extended Mix) [2004].flac'),
])
def test_the_preference_actually_fires_on_real_shapes(engine, monkeypatch,
                                                      plain, extended):
    """The failure that sent the first design back: a setting that is safe but
    never does anything is worse than no setting."""
    _prefer(monkeypatch, 'extended')
    ranked = engine.find_best_slskd_matches_enhanced(
        _source(), [_candidate(plain, duration=200_000), _candidate(extended, duration=380_000)])
    assert ranked and ranked[0].filename == extended


@pytest.mark.parametrize("title,filename", [
    ('99 Problems', 'Artist/Album/03 - 99 Problems (Extended Mix).flac'),
    ('1979', 'Artist/Album/07 - 1979 (Extended Mix).flac'),
    ('B2 Bomber', 'Artist/Album/01 - B2 Bomber (Extended Mix).flac'),
    ('7 Rings', 'Artist/Album/02 - 7 Rings (Extended Mix).flac'),
])
def test_a_title_that_starts_with_a_number_still_matches(engine, title, filename):
    """The source title must NOT get filename stripping. "99 Problems" reduced
    to "problems" while the candidate reduced to "99 problems", so the two sides
    never met and the preference silently never fired for any such song."""
    assert (engine.base_title_of(title, 'Artist')
            == engine.base_title_of(filename, 'Artist', from_filename=True))


def test_a_numeric_title_still_fires_through_the_real_ranking(engine, monkeypatch):
    """The one above compares the two sides directly. This one goes through the
    ranking path, where the source title is passed WITHOUT ``from_filename`` —
    the guard that keeps "99 Problems" from reducing to "problems" while the
    candidate reduces to "99 problems". Get that wrong and the preference
    silently never fires for any song whose title starts with a number."""
    _prefer(monkeypatch, 'extended')
    plain = _candidate('Artist/Album/03 - 99 Problems.flac')
    extended = _candidate('Artist/Album/03 - 99 Problems (Extended Mix).flac',
                          duration=380_000)
    ranked = engine.find_best_slskd_matches_enhanced(
        _source('99 Problems'), [plain, extended])
    assert ranked and ranked[0] is extended


def test_the_known_hole_a_bare_leading_number_that_is_part_of_the_title(engine, monkeypatch):
    """A residual, recorded on purpose so it isn't a surprise later.

    "03 - 99 Problems.flac" is safe: the track-number strip runs once, takes
    "03 - " and leaves "99 problems", which never equals "problems". But a
    filename with NO track number — "99 Problems (Extended Mix).flac" — has its
    "99 " taken as the track number, reduces to "problems", and matches a source
    track genuinely called "Problems".

    It cannot be settled from the string: "99 " and "03 " are the same shape,
    and nothing in the filename says which one is a track number. Refusing to
    strip bare numbers would trade this for silently never firing on
    "10 Song.flac", which is a far more common filename.

    What actually bounds it: the artist gate in get_valid_candidates only lets
    through files whose path carries this artist's name, so it takes ONE artist
    owning both "Problems" and "99 Problems", both in one search, with the
    second as the preferred version. The confidence cut still applies on top.
    """
    _prefer(monkeypatch, 'extended')
    right = _candidate('Artist/Album/01 - Problems.flac')
    impostor = _candidate('Artist/Album/99 Problems (Extended Mix).flac', duration=380_000)
    ranked = engine.find_best_slskd_matches_enhanced(_source('Problems'), [right, impostor])
    assert ranked[0] is impostor, "known residual — change this only WITH the fix"

    # and the safe shape it is bounded by
    numbered = _candidate('Artist/Album/03 - 99 Problems (Extended Mix).flac', duration=380_000)
    ranked = engine.find_best_slskd_matches_enhanced(_source('Problems'), [right, numbered])
    assert ranked[0] is right


def test_a_source_title_keeps_its_own_leading_number(engine):
    assert engine.base_title_of('99 Problems', 'Artist') == '99 problems'
    assert engine.base_title_of('1979', 'Artist') == '1979'


def test_the_source_side_never_breaks_the_ranking(engine, monkeypatch):
    """base_title_of runs on caller-supplied data. If it throws on the source
    title the whole ranking call would die, taking the download with it."""
    _prefer(monkeypatch, 'extended')
    boom = SimpleNamespace(name=object(), artists=['Artist'], duration_ms=1, album='B')
    assert engine.preferred_match_ids(boom, [_candidate('a/b/c.flac')], 'extended') == set()
