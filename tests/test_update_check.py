"""Release-aware update detection (Kazimir's version-glow ask).

The old check compared commit SHAs — every dev commit glowed identically,
the glow was dismissible forever, and no severity existed. Pins the pure
evaluator (no network) + the endpoint/frontend wiring contracts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.update_check import evaluate_update, parse_semver

_ROOT = Path(__file__).resolve().parent.parent


def _rel(tag, name='', body='', draft=False, prerelease=False, url='https://gh/x'):
    return {'tag_name': tag, 'name': name, 'body': body, 'draft': draft,
            'prerelease': prerelease, 'html_url': url}


@pytest.mark.parametrize("raw,expected", [
    ('v3.0.5', (3, 0, 5)),
    ('3.0.5+abc1234', (3, 0, 5)),
    ('3.1', (3, 1, 0)),
    ('release-2.10.3', (2, 10, 3)),
    ('garbage', None),
    (None, None),
])
def test_parse_semver(raw, expected):
    assert parse_semver(raw) == expected


def test_up_to_date_and_older_releases_report_nothing():
    out = evaluate_update('3.0.5', [_rel('v3.0.5'), _rel('v3.0.4')])
    assert out['available'] is False and out['severity'] is None


def test_routine_release_is_an_update():
    out = evaluate_update('3.0.5', [_rel('v3.0.6', name='3.0.6 — polish')])
    assert out == {'available': True, 'latest_version': '3.0.6',
                   'severity': 'update', 'release_url': 'https://gh/x',
                   'notes': '3.0.6 — polish'}


def test_major_bump_is_major():
    out = evaluate_update('3.0.5', [_rel('v4.0.0')])
    assert out['severity'] == 'major' and out['latest_version'] == '4.0.0'


def test_critical_marker_wins_even_when_latest_is_routine():
    # a security release sits BETWEEN current and latest — skipping straight
    # past it is still running without the fix, so the whole jump is critical
    out = evaluate_update('3.0.5', [
        _rel('v3.0.7', name='3.0.7 — goodies'),
        _rel('v3.0.6', name='3.0.6', body='Security fix for the auth bypass'),
    ])
    assert out['severity'] == 'critical' and out['latest_version'] == '3.0.7'


def test_drafts_and_prereleases_are_ignored():
    out = evaluate_update('3.0.5', [
        _rel('v9.9.9', draft=True),
        _rel('v9.9.8', prerelease=True),
    ])
    assert out['available'] is False


def test_unparseable_current_version_reports_nothing():
    assert evaluate_update('unknown', [_rel('v3.0.6')])['available'] is False


# ── wiring contracts ──────────────────────────────────────────────────────────

def test_endpoint_is_release_aware():
    src = (_ROOT / 'web_server.py').read_text(encoding='utf-8', errors='replace')
    assert 'from core.update_check import evaluate_update, fetch_releases' in src
    assert "'latest_version': rel.get('latest_version')" in src
    assert "'severity': rel.get('severity')" in src


def test_frontend_severity_glow_wiring():
    js = (_ROOT / 'webui' / 'static' / 'downloads.js').read_text(encoding='utf-8', errors='replace')
    css = (_ROOT / 'webui' / 'static' / 'style.css').read_text(encoding='utf-8', errors='replace')
    for cls in ('update-available--update', 'update-available--major', 'update-available--critical'):
        assert cls in js and cls in css
    # a critical release never stays dismissed
    assert "severity === 'critical'" in js
    # per-version dismissal (not the forever-dismiss SHA)
    assert 'data.latest_version || data.latest_sha' in js
