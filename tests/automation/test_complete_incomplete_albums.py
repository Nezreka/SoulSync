"""Safety tests for opt-in automatic album completion."""

from types import SimpleNamespace

import pytest

from core.automation.handlers.complete_incomplete_albums import (
    auto_complete_incomplete_albums,
)


@pytest.mark.parametrize(
    'event',
    [
        {},
        {'job_id': 'quality_upgrade', 'run_status': 'completed', 'errors': 0},
        {'job_id': 'album_completeness', 'run_status': 'stopped', 'errors': 0},
        {'job_id': 'album_completeness', 'run_status': 'failed', 'errors': 1},
        {'job_id': 'album_completeness', 'run_status': 'completed', 'errors': 1},
    ],
)
def test_rejects_unqualified_scan_events(event):
    """No downloads without a successful, error-free completeness scan."""
    calls = []

    deps = SimpleNamespace(
        get_repair_findings=lambda **kwargs: calls.append(kwargs),
    )

    result = auto_complete_incomplete_albums(
        {'_event_data': event, 'max_albums': 3},
        deps,
    )

    assert result['status'] == 'skipped'
    assert calls == []


def test_skips_findings_without_canonical_release():
    """An unpinned album must never be guessed from its title."""
    queued = []

    deps = SimpleNamespace(
        get_repair_findings=lambda **kwargs: {
            'items': [{
                'id': 12,
                'finding_type': 'incomplete_album',
                'status': 'pending',
                'details': {
                    'album_id': 42,
                    'album_title': 'Test Album',
                    'artist': 'Test Artist',
                    'canonical_source': '',
                    'canonical_album_id': '',
                    'missing_tracks': [
                        {'name': 'Missing Song', 'track_number': 2},
                    ],
                },
            }],
            'total': 1,
        },
        queue_album_completion=lambda **kwargs: queued.append(kwargs),
    )

    result = auto_complete_incomplete_albums(
        {
            '_event_data': {
                'job_id': 'album_completeness',
                'run_status': 'completed',
                'errors': 0,
            },
            'max_albums': 3,
        },
        deps,
    )

    assert result['status'] in ('completed', 'skipped')
    assert queued == []


def _pinned_finding(finding_id, album_id, release_id):
    return {
        'id': finding_id,
        'job_id': 'album_completeness',
        'finding_type': 'incomplete_album',
        'status': 'pending',
        'details': {
            'album_id': album_id,
            'album_title': 'Test Album',
            'artist': 'Test Artist',
            'canonical_source': 'spotify',
            'canonical_album_id': release_id,
            'expected_tracks': 10,
            'actual_tracks': 9,
            'missing_tracks': [
                {
                    'name': 'Missing Song',
                    'track_number': 8,
                    'disc_number': 1,
                    'source': 'spotify',
                    'source_track_id': f'track-{release_id}',
                    'artists': ['Test Artist'],
                },
            ],
        },
    }


def test_queues_missing_tracks_from_pinned_release():
    queued = []
    finding = _pinned_finding(1, 42, 'release-123')

    deps = SimpleNamespace(
        get_repair_findings=lambda **kwargs: {
            'items': [finding],
            'total': 1,
        },
        get_current_album_pin=lambda album_id: {"canonical_source": "spotify", "canonical_album_id": "release-123" if album_id == 42 else "release-a" if album_id in (41, 42) else "release-b"},
        fetch_canonical_release=lambda **kw: {
            'album': {'id': kw['canonical_album_id'],
                      'source': kw['canonical_source']},
            'tracks': [{'id': 'track-' + kw['canonical_album_id'],
                        'name': 'Missing Song',
                        'track_number': 8,
                        'disc_number': 1}],
        },
        queue_album_completion=lambda **kwargs: queued.append(kwargs) or {
            'status': 'queued',
        },
    )

    result = auto_complete_incomplete_albums(
        {
            '_event_data': {
                'job_id': 'album_completeness',
                'run_status': 'completed',
                'errors': 0,
            },
            'max_albums': 3,
        },
        deps,
    )

    assert result['queued_albums'] == 1
    assert len(queued) == 1
    assert queued[0]['canonical_source'] == 'spotify'
    assert queued[0]['canonical_album_id'] == 'release-123'
    assert len(queued[0]['missing_tracks']) == 1
    assert queued[0]['missing_tracks'][0]['name'] == 'Missing Song'


def test_limits_albums_and_deduplicates_canonical_releases():
    queued = []
    findings = [
        _pinned_finding(1, 41, 'release-a'),
        _pinned_finding(2, 42, 'release-a'),
        _pinned_finding(3, 43, 'release-b'),
        _pinned_finding(4, 44, 'release-c'),
    ]

    deps = SimpleNamespace(
        get_repair_findings=lambda **kwargs: {
            'items': findings,
            'total': len(findings),
        },
        get_current_album_pin=lambda album_id: {"canonical_source": "spotify", "canonical_album_id": "release-a" if album_id in (41, 42) else "release-b"},
        fetch_canonical_release=lambda **kw: {
            'album': {'id': kw['canonical_album_id'],
                      'source': kw['canonical_source']},
            'tracks': [{'id': 'track-' + kw['canonical_album_id'],
                        'name': 'Missing Song',
                        'track_number': 8,
                        'disc_number': 1}],
        },
        queue_album_completion=lambda **kwargs: queued.append(kwargs) or {
            'status': 'queued',
        },
    )

    result = auto_complete_incomplete_albums(
        {
            '_event_data': {
                'job_id': 'album_completeness',
                'run_status': 'completed',
                'errors': 0,
            },
            'max_albums': 2,
        },
        deps,
    )

    assert result['queued_albums'] == 2
    assert [
        item['canonical_album_id'] for item in queued
    ] == ['release-a', 'release-b']


def test_rejects_missing_track_from_wrong_canonical_release():
    """A track absent from the pinned release must never be queued."""
    queued = []
    finding = _pinned_finding(10, 42, 'release-123')
    finding['details']['missing_tracks'] = [{
        'name': 'Bonus Track From Another Edition',
        'track_number': 15,
        'disc_number': 1,
        'source': 'spotify',
        'source_track_id': 'wrong-track-id',
        'artists': ['Test Artist'],
    }]

    deps = SimpleNamespace(
        get_current_album_pin=lambda album_id: {"canonical_source": "spotify", "canonical_album_id": "release-123"},
        get_repair_findings=lambda **kwargs: {
            'items': [finding],
            'total': 1,
        },
        fetch_canonical_release=lambda **kwargs: {
            'album': {
                'id': 'release-123',
                'name': 'Test Album',
                'total_tracks': 10,
            },
            'tracks': [{
                'id': 'correct-track-id',
                'name': 'Original Track',
                'track_number': 8,
                'disc_number': 1,
            }],
        },
        queue_album_completion=lambda **kwargs: queued.append(kwargs) or {
            'status': 'queued',
        },
    )

    result = auto_complete_incomplete_albums(
        {
            '_event_data': {
                'job_id': 'album_completeness',
                'run_status': 'completed',
                'errors': 0,
            },
            'max_albums': 3,
        },
        deps,
    )

    assert result['queued_albums'] == 0
    assert queued == []


def test_rejects_album_repinned_after_scan():
    queued = []
    deps = SimpleNamespace(
        get_repair_findings=lambda **kw: {
            "items": [_pinned_finding(1, 42, "release-123")]
        },
        get_current_album_pin=lambda album_id: {
            "canonical_source": "spotify",
            "canonical_album_id": "release-NEW",
        },
        fetch_canonical_release=lambda **kw: pytest.fail(
            "Must not fetch an outdated release"
        ),
        queue_album_completion=lambda **kw: queued.append(kw),
    )
    result = auto_complete_incomplete_albums(
        {"_event_data": {
            "job_id": "album_completeness",
            "run_status": "completed",
            "errors": 0,
        }},
        deps,
    )
    assert result["queued_albums"] == 0
    assert queued == []
