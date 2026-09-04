"""One rule for "could this release satisfy the profile", both lanes.

A Prowlarr title almost never states a bitrate or a sample rate. The per-track
lane knows that and only enforces what a release actually claimed. The
album-bundle picker used the probed-file rule instead, so with fallback off a
strict profile refused whole albums that the per-track lane accepted from the
same indexer, on the same title. The user then got the release anyway, through
the slower path.
"""

from types import SimpleNamespace

import pytest

from core.download_plugins.album_bundle import pick_best_album_release
from core.downloads import validation
from core.quality.model import QualityTarget, satisfies_a_target_on_stated_facts

ALBUM = 'Kind Of Blue'

# Stock-shaped strict profile: the MP3 rungs carry min_bitrate, which is the
# whole reason the probed-file rule emptied this lane.
TARGETS = [
    QualityTarget(label='FLAC', format='flac'),
    QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320),
]


def _release(title, size=500 * 1024 * 1024, seeders=10):
    return SimpleNamespace(
        title=title, size=size, seeders=seeders, grabs=None, protocol='torrent',
        publish_date=None, categories=[], file_names=None, magnet_uri=None,
        indexer_priority=25,
    )


def _pick(titles, targets=TARGETS, fallback_enabled=False):
    picked = pick_best_album_release(
        [_release(t) for t in titles],
        lambda _title: None,
        album_name=ALBUM,
        quality_targets=targets,
        fallback_enabled=fallback_enabled,
    )
    return getattr(picked, 'title', None)


def test_both_lanes_share_one_definition():
    """Not two copies that agree today. The same function."""
    assert (
        validation._satisfies_a_target_on_stated_facts
        is satisfies_a_target_on_stated_facts
    )


def test_a_strict_profile_accepts_an_album_whose_title_omits_the_bitrate():
    """The regression. "[MP3]" states no bitrate, so 320 cannot disqualify it.

    Before, this returned None and the batch dropped to per-track, which
    accepted the very same release.
    """
    assert _pick(['Miles Davis - Kind Of Blue (1959) [MP3]']) == (
        'Miles Davis - Kind Of Blue (1959) [MP3]'
    )


def test_a_release_that_states_a_failing_bitrate_is_still_refused():
    """Leniency is only about silence. A stated 128 is enforced exactly."""
    assert _pick(['Miles Davis - Kind Of Blue [MP3 128]']) is None


def test_an_unreadable_format_is_still_refused():
    """No format means no target, same answer the pre-grab gate gives."""
    assert _pick(['Miles Davis - Kind Of Blue (1959) WEB']) is None


def test_a_matched_target_still_wins_over_a_rescued_one():
    """The rescue must not reorder anything. FLAC matches a target outright."""
    assert _pick([
        'Miles Davis - Kind Of Blue [MP3]',
        'Miles Davis - Kind Of Blue [FLAC]',
    ]) == 'Miles Davis - Kind Of Blue [FLAC]'


def test_a_proven_hi_res_release_still_beats_a_bare_flac():
    """The rescue only saves a release from refusal, it never promotes one.

    Under a hi-res-only profile a bare "[FLAC]" states no sample rate, so it
    survives instead of being refused, but the release that actually says
    24-96 still wins.
    """
    hires = [QualityTarget(format='flac', min_sample_rate=96000, bit_depth=24)]
    assert _pick([
        'Miles Davis - Kind Of Blue [FLAC]',
        'Miles Davis - Kind Of Blue [FLAC 24-96]',
    ], hires) == 'Miles Davis - Kind Of Blue [FLAC 24-96]'


@pytest.mark.parametrize('title', [
    'Miles Davis - Kind Of Blue (1959) [MP3]',
    'Miles Davis - Kind Of Blue [MP3 128]',
    'Miles Davis - Kind Of Blue (1959) WEB',
    'Miles Davis - Kind Of Blue [FLAC]',
])
def test_the_picker_agrees_with_the_predicate_release_by_release(title):
    """The invariant, stated directly: with fallback off, an album is refused
    exactly when the shared predicate says no release could satisfy a target."""
    from core.quality.release_format import audio_quality_from_release

    predicate = satisfies_a_target_on_stated_facts(
        audio_quality_from_release(title), TARGETS,
    )

    assert (_pick([title]) is not None) is predicate


def test_fallback_enabled_is_untouched():
    """The rescue only runs on the fallback-off branch."""
    assert _pick([
        'Miles Davis - Kind Of Blue [MP3]',
        'Miles Davis - Kind Of Blue [FLAC]',
    ], fallback_enabled=True) == 'Miles Davis - Kind Of Blue [FLAC]'
