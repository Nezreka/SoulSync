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




def test_target_album_id_rejects_unrelated_album():
    """A targeted run must never queue a different album."""
    finding = _pinned_finding(1, 42, 'release-123')
    pages = []

    def get_findings(**kwargs):
        pages.append(kwargs['page'])
        return {'items': [finding], 'total': 1}

    def forbidden(**kwargs):
        pytest.fail("Unrelated album reached a protected operation")

    deps = SimpleNamespace(
        get_repair_findings=get_findings,
        get_current_album_pin=forbidden,
        fetch_canonical_release=forbidden,
        queue_album_completion=forbidden,
    )

    result = auto_complete_incomplete_albums(
        {
            '_event_data': {
                'job_id': 'album_completeness',
                'run_status': 'completed',
                'errors': 0,
            },
            'max_albums': 1,
            'target_album_id': 'DIFFERENT_ALBUM',
        },
        deps,
    )

    assert result['queued_albums'] == 0
    assert result['eligible_albums'] == 0
    assert pages == [0]




def test_target_album_id_finds_second_page_without_queuing_others():
    """Targeted completion searches later pages and queues only its album."""
    target_id = 42
    pages = []
    queued = []

    unrelated = [
        _pinned_finding(i, i + 100, f'release-{i}')
        for i in range(50)
    ]
    target = _pinned_finding(51, target_id, 'release-target')

    def get_findings(**kwargs):
        pages.append(kwargs['page'])
        return {
            'items': unrelated if kwargs['page'] == 0 else [target],
            'total': 51,
        }

    def get_pin(album_id):
        assert album_id == target_id
        return {
            'owner_profile_id': None,
            'canonical_source': 'spotify',
            'canonical_album_id': 'release-target',
        }

    def fetch_release(**kwargs):
        assert kwargs['canonical_album_id'] == 'release-target'
        return {
            'album': {
                'id': 'release-target',
                'source': 'spotify',
            },
            'tracks': [{
                'id': 'track-release-target',
                'name': 'Missing Song',
                'track_number': 8,
                'disc_number': 1,
            }],
        }

    def queue(**kwargs):
        queued.append(kwargs)
        return {'status': 'queued'}

    deps = SimpleNamespace(
        get_repair_findings=get_findings,
        get_current_album_pin=get_pin,
        fetch_canonical_release=fetch_release,
        queue_album_completion=queue,
    )

    result = auto_complete_incomplete_albums(
        {
            '_event_data': {
                'job_id': 'album_completeness',
                'run_status': 'completed',
                'errors': 0,
            },
            'max_albums': 1,
            'target_album_id': str(target_id),
        },
        deps,
    )

    assert pages == [0, 1]
    assert result['eligible_albums'] == 1
    assert result['queued_albums'] == 1
    assert len(queued) == 1
    assert queued[0]['album_id'] == target_id
    assert queued[0]['canonical_album_id'] == 'release-target'


def test_queues_missing_tracks_from_pinned_release():
    queued = []
    finding = _pinned_finding(1, 42, 'release-123')

    deps = SimpleNamespace(
        get_repair_findings=lambda **kwargs: {
            'items': [finding],
            'total': 1,
        },
        get_current_album_pin=lambda album_id: {"owner_profile_id": None, "canonical_source": "spotify", "canonical_album_id": "release-123" if album_id == 42 else "release-a" if album_id in (41, 42) else "release-b"},
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
        get_current_album_pin=lambda album_id: {"owner_profile_id": None, "canonical_source": "spotify", "canonical_album_id": "release-a" if album_id in (41, 42) else "release-b"},
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
        get_current_album_pin=lambda album_id: {"owner_profile_id": None, "canonical_source": "spotify", "canonical_album_id": "release-123"},
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
            "items": [_pinned_finding(1, 42, "release-123")], "total": 1
        },
        get_current_album_pin=lambda album_id: {"owner_profile_id": None,
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


@pytest.mark.parametrize(
    "pin",
    [
        {
            "canonical_source": "spotify",
            "canonical_album_id": "release-123",
            "owner_profile_id": 2,
        },
        {
            "canonical_source": "spotify",
            "canonical_album_id": "release-123",
            "owner_profile_id": 0,
        },
        {
            "canonical_source": "spotify",
            "canonical_album_id": "release-123",
        },
    ],
    ids=["personal-library", "unexpected-owner", "missing-owner"],
)
def test_album_ownership_guard(pin):
    """Unsafe ownership must block release fetching and download queuing."""
    finding = _pinned_finding(1, 42, "release-123")

    def forbidden(**kwargs):
        pytest.fail("Unsafe album reached release fetching or download queue")

    deps = SimpleNamespace(
        get_repair_findings=lambda **kwargs: {
            "items": [finding],
            "total": 1,
        },
        get_current_album_pin=lambda album_id: pin,
        fetch_canonical_release=forbidden,
        queue_album_completion=forbidden,
    )

    result = auto_complete_incomplete_albums(
        {
            "_event_data": {
                "job_id": "album_completeness",
                "run_status": "completed",
                "errors": 0,
            },
            "max_albums": 1,
        },
        deps,
    )

    assert result["queued_albums"] == 0


def test_untargeted_run_reads_beyond_first_fifty():
    """Untargeted completion must retrieve findings beyond page one."""
    pages_requested = []

    def get_findings(**kw):
        pages_requested.append(kw["page"])
        if kw["page"] == 0:
            return {
                "items": [
                    {
                        "finding_type": "other",
                        "status": "pending",
                        "details": {},
                    }
                    for _ in range(50)
                ],
                "total": 51,
            }
        if kw["page"] == 1:
            return {
                "items": [_pinned_finding(51, 42, "release-123")],
                "total": 51,
            }
        pytest.fail("Unexpected findings page")

    def verify_pin(album_id):
        assert str(album_id) == "42"
        return {
            "owner_profile_id": None,
            "canonical_source": "spotify",
            "canonical_album_id": "release-123",
        }

    deps = SimpleNamespace(
        get_repair_findings=get_findings,
        get_current_album_pin=verify_pin,
        fetch_canonical_release=lambda **kw: {
            "album": {
                "id": "different-release",
                "source": "spotify",
            },
            "tracks": [],
        },
        queue_album_completion=lambda **kw: pytest.fail(
            "Test must not queue a download"
        ),
    )

    auto_complete_incomplete_albums(
        {
            "max_albums": 1,
            "_event_data": {
                "job_id": "album_completeness",
                "run_status": "completed",
                "errors": 0,
            },
        },
        deps,
    )

    assert pages_requested == [0, 1]


def test_failed_pagination_never_queues_downloads():
    """A failed second page must prevent processing the first page."""
    pages_requested = []
    queued = []

    def get_findings(**kw):
        pages_requested.append(kw["page"])
        if kw["page"] == 0:
            return {
                "items": [
                    _pinned_finding(i, 42, "release-123")
                    for i in range(50)
                ],
                "total": 51,
            }
        return None

    deps = SimpleNamespace(
        get_repair_findings=get_findings,
        get_current_album_pin=lambda album_id: pytest.fail(
            "Must not verify pins from partial findings"
        ),
        fetch_canonical_release=lambda **kw: pytest.fail(
            "Must not fetch releases from partial findings"
        ),
        queue_album_completion=lambda **kw: queued.append(kw),
    )

    result = auto_complete_incomplete_albums(
        {
            "max_albums": 1,
            "_event_data": {
                "job_id": "album_completeness",
                "run_status": "completed",
                "errors": 0,
            },
        },
        deps,
    )

    assert pages_requested == [0, 1]
    assert result["status"] == "skipped"
    assert queued == []


def _release_deps(findings, queue, **extra):
    return SimpleNamespace(
        get_repair_findings=lambda **kw: {'items': findings, 'total': len(findings)},
        get_current_album_pin=lambda album_id: {
            'owner_profile_id': None, 'canonical_source': 'spotify',
            'canonical_album_id': f'release-{album_id}',
        },
        fetch_canonical_release=extra.pop('fetch', None) or (lambda **kw: {
            'album': {'id': kw['canonical_album_id'], 'source': kw['canonical_source']},
            'tracks': [{'id': 'track-' + kw['canonical_album_id'], 'name': 'Missing Song',
                        'track_number': 8, 'disc_number': 1}],
        }),
        queue_album_completion=queue,
        **extra,
    )


_GOOD_SCAN = {'job_id': 'album_completeness', 'run_status': 'completed', 'errors': 0}


def test_cooling_down_albums_do_not_eat_the_slots():
    # first three keep failing and sit in cooldown, the fourth must still get its turn
    findings = [_pinned_finding(i, i, f'release-{i}') for i in (1, 2, 3, 4)]
    queued = []

    def queue(**kw):
        if kw['canonical_album_id'] in ('release-1', 'release-2', 'release-3'):
            return {'status': 'skipped', 'reason': 'Release is cooling down'}
        queued.append(kw['canonical_album_id'])
        return {'status': 'queued'}

    result = auto_complete_incomplete_albums(
        {'_event_data': _GOOD_SCAN, 'max_albums': 3}, _release_deps(findings, queue))

    assert queued == ['release-4']
    assert result['queued_albums'] == 1


def test_max_albums_still_caps_what_gets_queued():
    findings = [_pinned_finding(i, i, f'release-{i}') for i in (1, 2, 3, 4)]
    queued = []
    queue = lambda **kw: queued.append(kw['canonical_album_id']) or {'status': 'queued'}  # noqa: E731

    result = auto_complete_incomplete_albums(
        {'_event_data': _GOOD_SCAN, 'max_albums': 2}, _release_deps(findings, queue))

    assert queued == ['release-1', 'release-2']
    assert result['queued_albums'] == 2


def test_cooling_down_peek_skips_the_release_fetch():
    findings = [_pinned_finding(i, i, f'release-{i}') for i in (1, 2)]
    fetched = []
    queued = []

    def fetch(**kw):
        fetched.append(kw['canonical_album_id'])
        return {
            'album': {'id': kw['canonical_album_id'], 'source': kw['canonical_source']},
            'tracks': [{'id': 'track-' + kw['canonical_album_id'], 'name': 'Missing Song',
                        'track_number': 8, 'disc_number': 1}],
        }

    deps = _release_deps(
        findings,
        lambda **kw: queued.append(kw['canonical_album_id']) or {'status': 'queued'},
        fetch=fetch,
        album_completion_cooling_down=lambda source, release_id: release_id == 'release-1',
    )
    auto_complete_incomplete_albums({'_event_data': _GOOD_SCAN, 'max_albums': 3}, deps)

    assert fetched == ['release-2']
    assert queued == ['release-2']


def test_broken_cooling_down_peek_falls_through_to_the_queue():
    findings = [_pinned_finding(1, 1, 'release-1')]
    queued = []

    def peek(source, release_id):
        raise RuntimeError('db gone')

    deps = _release_deps(
        findings,
        lambda **kw: queued.append(kw['canonical_album_id']) or {'status': 'queued'},
        album_completion_cooling_down=peek,
    )
    auto_complete_incomplete_albums({'_event_data': _GOOD_SCAN, 'max_albums': 3}, deps)

    assert queued == ['release-1']
