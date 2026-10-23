"""Candidate cutoff checks and preserved quality-finding automation contracts.

Native Library-v2 display and audit integration have their own regression tests.
"""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.apply_quality_upgrades import auto_apply_quality_upgrades
from core.download_plugins.types import TrackResult
from core.downloads.decisions import accept, reject
from core.quality import upgrades
from core.quality.model import QualityTarget
from database.music_database import MusicDatabase

FLAC24 = QualityTarget(label='FLAC 24-bit', format='flac', bit_depth=24)
FLAC16 = QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)
MP3_320 = QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320)


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    return d


def _finding(db, track_id, *, job='quality_upgrade', status='pending', **details):
    details.setdefault('current_format', 'MP3 192kbps')
    conn = db._get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, status, entity_type, entity_id, title, details_json) "
            "VALUES (?, 'quality_upgrade', ?, 'track', ?, 'x', ?)",
            (job, status, str(track_id), json.dumps(details)))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# Candidate inspector quality bar


def _row(quality, bitrate=None, bit_depth=None, filename='f'):
    return TrackResult(username='peer', filename=filename, size=1, bitrate=bitrate, duration=1,
                       quality=quality, free_upload_slots=1, upload_speed=1, queue_length=0,
                       bit_depth=bit_depth, artist='A', title='T')


def test_below_cutoff_rejects_what_the_download_filter_would_take():
    targets = [FLAC24, FLAC16, MP3_320]
    hi = _row('flac', bit_depth=24, filename='hi')
    cd = _row('flac', bit_depth=16, filename='cd')
    mp3 = _row('mp3', bitrate=320, filename='mp3')
    unknown = _row('flac', filename='unknown-depth')
    already = (_row('mp3', bitrate=128, filename='weak'), reject('match_weak', score=0.2))
    out = upgrades.apply_upgrade_bar(
        [(hi, accept(0.9)), (cd, accept(0.9)), (mp3, accept(0.9)), (unknown, accept(0.9)), already],
        targets, cutoff_index=1, cutoff_label='FLAC 16-bit')
    codes = {r.filename: d.code for r, d in out}
    assert codes == {'hi': 'accepted', 'cd': 'accepted', 'mp3': 'below_cutoff',
                     'unknown-depth': 'accepted', 'weak': 'match_weak'}
    mp3_decision = dict((r.filename, d) for r, d in out)['mp3']
    assert mp3_decision.stage == 'quality'
    assert mp3_decision.detail == "MP3 320kbps doesn't reach your upgrade cutoff (FLAC 16-bit)"
    assert mp3_decision.score == 0.9


def test_without_a_cutoff_any_target_counts_but_fallback_does_not():
    out = upgrades.apply_upgrade_bar(
        [(_row('mp3', bitrate=320, filename='ok'), accept(0.9)),
         (_row('mp3', bitrate=192, filename='fallback'), accept(0.9))],
        [FLAC16, MP3_320], cutoff_index=None)
    assert [d.code for _, d in out] == ['accepted', 'below_cutoff']
    assert "any of your profile's targets" in out[1][1].detail


def test_no_targets_means_nothing_to_judge():
    pairs = [(_row('mp3', bitrate=128), accept(0.9))]
    assert upgrades.apply_upgrade_bar(pairs, [], None) == pairs


def test_upgrade_bar_reads_the_profile(monkeypatch):
    profiles = {
        1: {'ranked_targets': [FLAC24.to_dict(), FLAC16.to_dict()], 'upgrade_policy': 'until_cutoff',
            'upgrade_cutoff_index': 1},
        2: {'ranked_targets': [FLAC16.to_dict()], 'upgrade_policy': 'acceptable'},
        3: {'ranked_targets': [FLAC24.to_dict()], 'upgrade_policy': 'until_top',
            'upgrade_cutoff_index': 9},
        4: {'ranked_targets': []},
    }
    monkeypatch.setattr('core.quality.selection.load_profile_by_id', lambda pid: profiles[pid])
    assert upgrades.upgrade_bar(1)[1:] == (1, 'FLAC 16-bit')
    assert upgrades.upgrade_bar(2)[1:] == (None, 'FLAC 16-bit')
    assert upgrades.upgrade_bar(3)[1:] == (0, 'FLAC 24-bit')   # clamped
    assert upgrades.upgrade_bar(4) == ([], None, '')


# ---------------------------------------------------------------------------
# Apply Quality Upgrades automation
# ---------------------------------------------------------------------------

def _profiles(db, default_policy='acceptable'):
    conn = db._get_connection()
    try:
        conn.execute("UPDATE quality_profiles SET upgrade_policy = ? WHERE is_default = 1", (default_policy,))
        default_id = conn.execute("SELECT id FROM quality_profiles WHERE is_default = 1").fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    strict = db.create_quality_profile('Strict', {})
    conn = db._get_connection()
    try:
        conn.execute("UPDATE quality_profiles SET upgrade_policy = 'until_cutoff' WHERE id = ?", (strict,))
        conn.commit()
    finally:
        conn.close()
    return default_id, strict


MATCH = {'name': 'Song', 'id': 'sp1'}


def test_only_until_cutoff_profiles_with_a_match_are_picked(db):
    default_id, strict = _profiles(db)
    want = _finding(db, 1, quality_profile_id=strict, matched_track_data=MATCH)
    _finding(db, 2, quality_profile_id=default_id, matched_track_data=MATCH)   # acceptable
    _finding(db, 3, quality_profile_id=strict)                                  # no match
    _finding(db, 4, job='quality_upgrade_scanner', quality_profile_id=strict,
             matched_track_data=MATCH)                                          # flag-only job
    _finding(db, 5, quality_profile_id=strict, matched_track_data=MATCH, status='resolved')
    assert upgrades.until_cutoff_finding_ids(db) == [want]


def test_a_finding_without_a_profile_follows_the_default(db):
    _profiles(db, default_policy='until_cutoff')
    fid = _finding(db, 1, matched_track_data=MATCH)
    assert upgrades.until_cutoff_finding_ids(db) == [fid]


def test_limit_caps_a_run(db):
    _default_id, strict = _profiles(db)
    ids = [_finding(db, t, quality_profile_id=strict, matched_track_data=MATCH) for t in (1, 2, 3)]
    assert upgrades.until_cutoff_finding_ids(db, limit=2) == ids[:2]


class _Deps:
    def __init__(self, db, fixer):
        self._db = db
        self.bulk_fix_repair_findings = fixer
        self.progress = []

    def get_database(self):
        return self._db

    def update_progress(self, *args, **kwargs):
        self.progress.append(kwargs)


def test_automation_applies_the_picked_findings(db):
    _default_id, strict = _profiles(db)
    fid = _finding(db, 1, quality_profile_id=strict, matched_track_data=MATCH)
    seen = []
    deps = _Deps(db, lambda ids, **kwargs: seen.append(ids) or {'fixed': len(ids), 'failed': 0})
    result = auto_apply_quality_upgrades({'_automation_id': 'a1', 'limit': 5}, deps)
    assert seen == [[fid]]
    assert result['applied'] == 1
    assert deps.progress[-1]['log_line'] == 'Queued 1 quality upgrade on the wishlist'


def test_automation_with_nothing_to_do_says_so(db):
    deps = _Deps(db, lambda ids: pytest.fail('nothing should be applied'))
    result = auto_apply_quality_upgrades({'_automation_id': 'a1'}, deps)
    assert result['applied'] == 0
    assert 'No pending upgrades' in deps.progress[-1]['log_line']


def test_automation_without_the_repair_worker_skips(db):
    _default_id, strict = _profiles(db)
    _finding(db, 1, quality_profile_id=strict, matched_track_data=MATCH)
    result = auto_apply_quality_upgrades({'_automation_id': 'a1'}, _Deps(db, None))
    assert result['status'] == 'skipped'
