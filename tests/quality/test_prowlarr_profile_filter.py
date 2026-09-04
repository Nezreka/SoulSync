"""Torrent/Usenet candidates obey the same quality ladder as other sources."""

from core.download_plugins.types import TrackResult
from core.downloads.validation import _filter_prowlarr_by_quality
import core.quality.selection as selection


def _candidate(source, fmt, *, bitrate=None, sample_rate=None, bit_depth=None):
    return TrackResult(
        username=source,
        filename=f'token||Artist - Album [{fmt}]',
        size=500_000_000,
        bitrate=bitrate,
        duration=None,
        quality=fmt,
        free_upload_slots=1,
        upload_speed=0,
        queue_length=0,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        artist='Artist',
        title='Album',
    )


def test_strict_item_profile_rejects_lossy_prowlarr_hit(monkeypatch):
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'flac', 'bit_depth': 24, 'min_sample_rate': 96_000}],
        'fallback_enabled': False,
    })

    result = _filter_prowlarr_by_quality(
        [_candidate('usenet', 'mp3', bitrate=320)],
        profile_id=17,
    )

    assert result == []


def test_a_release_is_only_dropped_for_something_it_actually_claimed(monkeypatch):
    """Search time filtering removes what is provably wrong, not what is mute.

    This used to drop a FLAC release with no stated resolution under a 24/96
    target, on the "unproven must not over-claim" rule that ranking uses for
    probed files. A Prowlarr title is not a probed file: it almost never
    carries sample rate, bit depth or bitrate, so that rule emptied the entire
    lane. The default MP3 target's min_bitrate of 320 did it on its own.

    A stated value is still enforced (see the two tests below), and the file
    itself is still probed at import, which is where an unproven release is
    actually caught.
    """
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'flac', 'bit_depth': 24, 'min_sample_rate': 96_000}],
        'fallback_enabled': False,
    })
    generic = _candidate('torrent', 'flac')
    hires = _candidate('usenet', 'flac', sample_rate=96_000, bit_depth=24)

    result = _filter_prowlarr_by_quality([generic, hires], profile_id=17)

    assert result == [generic, hires]


def test_a_stated_value_below_the_target_is_still_dropped(monkeypatch):
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'mp3', 'min_bitrate': 320}],
        'fallback_enabled': False,
    })
    stated_low = _candidate('torrent', 'mp3', bitrate=128)
    mute = _candidate('torrent', 'mp3')

    assert _filter_prowlarr_by_quality([stated_low, mute], profile_id=17) == [mute]


def test_a_format_the_profile_never_asked_for_is_still_dropped(monkeypatch):
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'flac'}],
        'fallback_enabled': False,
    })

    assert _filter_prowlarr_by_quality(
        [_candidate('torrent', 'mp3', bitrate=320)], profile_id=17,
    ) == []


def test_an_unreadable_format_is_dropped_under_a_strict_profile(monkeypatch):
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'flac'}],
        'fallback_enabled': False,
    })

    assert _filter_prowlarr_by_quality(
        [_candidate('torrent', 'unknown')], profile_id=17,
    ) == []


def test_fallback_keeps_everything_the_lane_found(monkeypatch):
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'flac'}],
        'fallback_enabled': True,
    })
    lossy = _candidate('torrent', 'mp3', bitrate=320)

    assert _filter_prowlarr_by_quality([lossy], profile_id=17) == [lossy]


def test_non_prowlarr_sources_are_not_filtered_here(monkeypatch):
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'flac'}],
        'fallback_enabled': False,
    })
    tidal = _candidate('tidal', 'aac', bitrate=320)

    assert _filter_prowlarr_by_quality([tidal], profile_id=17) == [tidal]
