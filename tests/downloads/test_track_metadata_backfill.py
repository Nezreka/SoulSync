"""Tests for ``core/downloads/track_metadata_backfill.py``.

Real-world regression these tests pin: wishlist rows carrying a
poisoned ``track_number=1`` (older payload helpers defaulted
missing values to 1) used to prevent the Spotify-API backfill
that hydrated lean ``spotify_album_context`` (release_date,
total_tracks). Result: residual per-track wishlist downloads
produced folders without a year subfolder when the wishlist row
came from a Deezer-sourced discovery match.

The split-concern fix:
  - ``track_number`` precedence: track_info → track object → API
  - album hydration: runs whenever release_date / total_tracks
    missing, INDEPENDENT of whether track_number was already known
  - single API call serves both — no double round-trip
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from core.downloads.track_metadata_backfill import (
    ResolvedTrackMetadata,
    hydrate_download_metadata,
)


@dataclass
class _FakeTrack:
    """Stand-in for the Spotify Track dataclass — only the attrs the
    backfill helper reads."""

    id: Optional[str] = 'spotify_track_id'
    track_number: Optional[int] = None
    disc_number: Optional[int] = None


def _api_payload(
    track_number: Optional[int] = 5,
    disc_number: int = 1,
    release_date: str = '2013-10-22',
    total_tracks: int = 16,
    album_id: str = 'spotify_album_id',
    image_url: str = 'https://i.scdn.co/cover.jpg',
    album_type: str = 'album',
) -> dict:
    """Build a plausible ``spotify_client.get_track_details`` response."""
    images: list[dict[str, Any]] = []
    if image_url:
        images.append({'url': image_url, 'height': 640, 'width': 640})
    return {
        'id': 'spotify_track_id',
        'name': 'Roar',
        'track_number': track_number,
        'disc_number': disc_number,
        'album': {
            'id': album_id,
            'name': 'PRISM (Deluxe)',
            'release_date': release_date,
            'total_tracks': total_tracks,
            'album_type': album_type,
            'images': images,
        },
    }


# ---------------------------------------------------------------------------
# Track-number precedence chain.
# ---------------------------------------------------------------------------


def test_track_info_track_number_wins_over_track_object():
    """track_info has a real value → use it, skip lower-priority sources."""
    client = MagicMock()
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(track_number=9),  # would resolve to 9 if track_info empty
        {'track_number': 5, 'disc_number': 2},
        album_ctx,
        client,
    )

    assert resolved == ResolvedTrackMetadata(track_number=5, disc_number=2, source='track_info')
    client.get_track_details.assert_not_called()


def test_track_object_used_when_track_info_missing():
    """track_info has no track_number → fall to track.track_number."""
    client = MagicMock()
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(track_number=7, disc_number=2),
        {},  # no track_number key
        album_ctx,
        client,
    )

    assert resolved == ResolvedTrackMetadata(track_number=7, disc_number=2, source='track_object')
    client.get_track_details.assert_not_called()


def test_api_used_when_track_info_and_track_object_missing():
    """No local source has track_number → fire the API."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(track_number=8, disc_number=1)
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(track_number=None),
        {},
        album_ctx,
        client,
    )

    assert resolved == ResolvedTrackMetadata(track_number=8, disc_number=1, source='api')
    client.get_track_details.assert_called_once_with('spotify_track_id')


def test_zero_track_number_in_track_info_treated_as_missing():
    """track_info.track_number=0 is a sentinel for "missing", not a
    valid position — fall through to the next source."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(track_number=3)
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(track_number=None),
        {'track_number': 0},
        album_ctx,
        client,
    )

    assert resolved.track_number == 3
    assert resolved.source == 'api'


def test_string_track_number_coerced_to_int():
    """Some legacy payloads pass track_number as a string. Coerce, not reject."""
    client = MagicMock()
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': '11'},
        album_ctx,
        client,
    )

    assert resolved.track_number == 11
    assert resolved.source == 'track_info'


def test_boolean_track_number_rejected_not_treated_as_int():
    """Python ints include bools — ``True == 1`` would erroneously
    pass the >0 gate. Reject explicitly so a malformed payload
    falls through instead of being mistaken for track 1."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(track_number=4)
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': True},
        album_ctx,
        client,
    )

    assert resolved.track_number == 4
    assert resolved.source == 'api'


# ---------------------------------------------------------------------------
# THE REGRESSION FIX: album backfill runs even when track_number known.
# ---------------------------------------------------------------------------


def test_poisoned_default_track_number_does_NOT_block_album_backfill():
    """Regression pin. track_info carries the poisoned ``track_number=1``
    legacy default; album_context is lean (Deezer-sourced discovery
    match, no release_date, no total_tracks). Pre-fix: API skipped
    because tn>0, folder lost year. Post-fix: API still fires for
    album hydration; track_number stays at 1 (caller's precedence),
    but release_date / total_tracks now populate."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(
        track_number=4,  # the API knows the *real* track number
        release_date='2007-03-12',
        total_tracks=12,
    )
    album_ctx = {'name': 'Welcome Interstate Managers'}  # lean

    resolved = hydrate_download_metadata(
        _FakeTrack(id='spotify_track_id', track_number=None),
        {'track_number': 1},  # POISONED default
        album_ctx,
        client,
    )

    # track_number stays at 1 — track_info precedence is sacred,
    # we don't second-guess it. The album fix is what matters.
    assert resolved.track_number == 1
    assert resolved.source == 'track_info'

    # CRITICAL: album_context got hydrated despite track_number being "resolved".
    assert album_ctx['release_date'] == '2007-03-12'
    assert album_ctx['total_tracks'] == 12

    # API call still fired — for the album, even though tn already known.
    client.get_track_details.assert_called_once_with('spotify_track_id')


def test_rich_album_context_skips_api_when_track_number_resolved():
    """Cost guard. Both concerns satisfied locally → no API call.
    Keeps the network-cost contract from the pre-extract code."""
    client = MagicMock()
    album_ctx = {
        'name': 'PRISM (Deluxe)',
        'release_date': '2013-10-22',
        'total_tracks': 16,
    }

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': 5},
        album_ctx,
        client,
    )

    assert resolved.track_number == 5
    client.get_track_details.assert_not_called()


def test_api_fires_for_lean_album_even_with_track_object_track_number():
    """track_number from the Track dataclass (search-result level) but
    album_context still lean — API must fire for album hydration."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload()
    album_ctx = {'name': 'PRISM (Deluxe)'}  # lean

    resolved = hydrate_download_metadata(
        _FakeTrack(track_number=9, disc_number=1),
        {},
        album_ctx,
        client,
    )

    assert resolved.track_number == 9
    assert resolved.source == 'track_object'
    assert album_ctx['release_date'] == '2013-10-22'
    assert album_ctx['total_tracks'] == 16
    client.get_track_details.assert_called_once()


def test_album_with_only_release_date_still_triggers_backfill_for_total_tracks():
    """release_date populated but total_tracks missing → still lean →
    API fires to fill total_tracks. Either missing field triggers."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload()
    album_ctx = {'release_date': '2013-10-22'}  # total_tracks missing

    hydrate_download_metadata(_FakeTrack(), {'track_number': 5}, album_ctx, client)

    assert album_ctx['total_tracks'] == 16
    client.get_track_details.assert_called_once()


def test_album_with_only_total_tracks_still_triggers_backfill_for_release_date():
    """Inverse: total_tracks present but release_date missing → still lean."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload()
    album_ctx = {'total_tracks': 16}  # release_date missing

    hydrate_download_metadata(_FakeTrack(), {'track_number': 5}, album_ctx, client)

    assert album_ctx['release_date'] == '2013-10-22'
    client.get_track_details.assert_called_once()


# ---------------------------------------------------------------------------
# Album backfill: preserve existing values, never overwrite.
# ---------------------------------------------------------------------------


def test_album_backfill_does_not_overwrite_existing_release_date():
    """Caller's release_date is sacred — only fill when absent. Source
    of truth may legitimately diverge between context and API (e.g.
    region-specific releases). Don't second-guess the caller."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(release_date='2099-01-01')
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16, 'name': 'Real'}

    hydrate_download_metadata(_FakeTrack(), {'track_number': 1}, album_ctx, client)

    # Not lean → API never called.
    client.get_track_details.assert_not_called()
    assert album_ctx['release_date'] == '2013-10-22'


def test_album_backfill_fills_missing_image_url_from_images_array():
    """API responses use the Spotify ``images`` array shape. Helper
    flattens to the ``image_url`` string the path builder reads."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(
        image_url='https://i.scdn.co/cover.jpg',
    )
    album_ctx = {'name': 'PRISM'}  # lean

    hydrate_download_metadata(_FakeTrack(), {}, album_ctx, client)

    assert album_ctx['image_url'] == 'https://i.scdn.co/cover.jpg'


def test_album_backfill_handles_empty_images_array_gracefully():
    """API response with empty ``images`` array (rare but possible
    on tracks the album-cover service hasn't indexed yet) must not
    crash the resolver."""
    client = MagicMock()
    payload = _api_payload(image_url='')  # produces images=[]
    client.get_track_details.return_value = payload
    album_ctx = {'name': 'PRISM'}

    hydrate_download_metadata(_FakeTrack(), {}, album_ctx, client)

    # No image_url set, but other fields hydrated.
    assert 'image_url' not in album_ctx
    assert album_ctx['release_date'] == '2013-10-22'


def test_album_backfill_skips_when_detailed_album_not_dict():
    """API response with a non-dict ``album`` field (defensive — old
    enrichment caches stored string album names) is safely skipped."""
    client = MagicMock()
    client.get_track_details.return_value = {
        'id': 'spotify_track_id',
        'track_number': 3,
        'album': 'Just a string',
    }
    album_ctx = {'name': 'PRISM'}

    resolved = hydrate_download_metadata(_FakeTrack(), {}, album_ctx, client)

    # track_number resolved, album_context untouched.
    assert resolved.track_number == 3
    assert 'release_date' not in album_ctx


# ---------------------------------------------------------------------------
# Defensive: missing IDs, exceptions, non-dict inputs.
# ---------------------------------------------------------------------------


def test_no_track_id_skips_api_call():
    """Without ``track.id`` the API can't be queried. Returns whatever
    was resolved locally, no exception."""
    client = MagicMock()
    album_ctx = {'name': 'PRISM'}  # lean — but no ID to fix it

    resolved = hydrate_download_metadata(
        _FakeTrack(id=None),
        {},
        album_ctx,
        client,
    )

    assert resolved.track_number is None
    assert resolved.source == 'none'
    client.get_track_details.assert_not_called()


def test_api_exception_does_not_propagate():
    """Network blip / Spotify 5xx must not crash the download pipeline.
    Returns whatever was resolved before the failed call."""
    client = MagicMock()
    client.get_track_details.side_effect = RuntimeError('429 rate limited')
    album_ctx = {'name': 'PRISM'}  # lean

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': 5},  # already have tn from track_info
        album_ctx,
        client,
    )

    assert resolved.track_number == 5
    assert 'release_date' not in album_ctx  # backfill failed silently


def test_api_returns_none_does_not_mutate_context():
    """API call that returns None (no result for this Spotify ID) is
    a no-op for album hydration."""
    client = MagicMock()
    client.get_track_details.return_value = None
    album_ctx = {'name': 'PRISM'}

    resolved = hydrate_download_metadata(_FakeTrack(), {}, album_ctx, client)

    assert resolved.track_number is None
    assert 'release_date' not in album_ctx


def test_non_dict_track_info_treated_as_empty():
    """Defensive — a malformed task payload with a non-dict track_info
    falls through to the track object / API instead of crashing."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(track_number=6)
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        'not a dict',  # type: ignore[arg-type]
        album_ctx,
        client,
    )

    assert resolved.source == 'api'
    assert resolved.track_number == 6


def test_non_dict_album_context_treated_as_lean():
    """Defensive — a None album_context is treated as lean, so the API
    still fires but mutation is silently skipped (helper can't mutate
    a non-dict in place)."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload()

    # Should not raise — passing None for album_context just means
    # the backfill no-ops (can't mutate None) but the call signature
    # is preserved.
    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': 5},
        None,  # type: ignore[arg-type]
        client,
    )

    # tn resolved from track_info, no crash.
    assert resolved.track_number == 5


# ---------------------------------------------------------------------------
# disc_number resolution.
# ---------------------------------------------------------------------------


def test_disc_number_from_track_info_paired_with_track_number():
    """When track_info supplies track_number, its disc_number rides along."""
    client = MagicMock()
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': 5, 'disc_number': 2},
        album_ctx,
        client,
    )

    assert resolved.disc_number == 2


def test_disc_number_defaults_to_1_when_only_track_number_present():
    """track_info has track_number but no disc_number → disc=1."""
    client = MagicMock()
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(
        _FakeTrack(),
        {'track_number': 5},
        album_ctx,
        client,
    )

    assert resolved.disc_number == 1


def test_disc_number_zero_from_api_floored_to_1():
    """API response with disc_number=0 (some niche releases) gets
    floored to 1 — albums are 1-indexed."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload(track_number=5, disc_number=0)
    album_ctx = {'release_date': '2013-10-22', 'total_tracks': 16}

    resolved = hydrate_download_metadata(_FakeTrack(), {}, album_ctx, client)

    assert resolved.disc_number == 1


# ---------------------------------------------------------------------------
# Cost guard: API called at most once.
# ---------------------------------------------------------------------------


def test_api_called_at_most_once_per_invocation():
    """Even when API serves both concerns (track_number AND album),
    only one ``get_track_details`` call is made."""
    client = MagicMock()
    client.get_track_details.return_value = _api_payload()
    album_ctx = {'name': 'PRISM'}  # lean

    hydrate_download_metadata(_FakeTrack(track_number=None), {}, album_ctx, client)

    assert client.get_track_details.call_count == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
