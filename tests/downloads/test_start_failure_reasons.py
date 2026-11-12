"""A matched song that didn't start says why (#1981).

The search found matching files, every attempt to start one returned False,
and the row said "No specific failure reason was recorded" because nothing
was. Each skip or refusal is kept on the task now, and the first ones reach
the failure breakdown.
"""

from __future__ import annotations

from types import SimpleNamespace

import core.downloads.candidates as cands
from core.runtime_state import download_tasks


def test_every_match_dropped_by_the_size_or_length_limit_is_said(monkeypatch):
    download_tasks['t-why'] = {'status': 'searching'}
    try:
        monkeypatch.setattr('core.downloads.size_limit.filter_music_candidates',
                            lambda rows, **kw: [])
        track = SimpleNamespace(name='Run Away', duration_ms=200_000)
        row = SimpleNamespace(username='deezer_dl', filename='dz||1')
        assert cands.attempt_download_with_candidates('t-why', [row], track) is False
        assert download_tasks['t-why']['start_failures'] == [
            'every match was over the size limit or the wrong length']
    finally:
        download_tasks.pop('t-why', None)


def test_notes_are_kept_once_and_a_few_at_most():
    download_tasks['t-few'] = {}
    try:
        for i in range(10):
            cands._note_start_failure('t-few', 'already tried')
            cands._note_start_failure('t-few', f'peer{i}: refused')
        notes = download_tasks['t-few']['start_failures']
        assert notes[0] == 'already tried' and notes.count('already tried') == 1
        assert len(notes) == 6
    finally:
        download_tasks.pop('t-few', None)


def test_a_gone_task_is_left_alone():
    cands._note_start_failure('no-such-task', 'x')
    assert 'no-such-task' not in download_tasks


def test_an_upgrade_does_not_download_a_copy_that_cannot_be_better(monkeypatch):
    """#2060: a 128k file from deezer under a 320k profile was fetched from
    deezer again, measured, and thrown away, for every song, every cycle"""
    from datetime import datetime, timezone
    from core.imports.upgrade_intent import CONTEXT_KEY, issue_upgrade_intent
    from core.library2 import upgrade_attempts
    from core.quality.model import AudioQuality, QualityTarget, rank_candidate

    targets = [QualityTarget.from_dict({'label': 'MP3 320', 'format': 'mp3', 'min_bitrate': 320})]
    have = AudioQuality(format='mp3', bitrate=128)
    basis = {'targets': targets, 'rank': rank_candidate(have, targets)[0],
             'score': have.tier_score(), 'source': 'deezer_dl',
             'added': datetime.now(timezone.utc).replace(tzinfo=None)}
    asked = []
    monkeypatch.setattr(upgrade_attempts, 'upgrade_basis',
                        lambda conn, track_id: asked.append(track_id) or basis)
    monkeypatch.setattr('core.downloads.size_limit.filter_music_candidates',
                        lambda rows, **kw: rows)
    fake_db = SimpleNamespace(
        is_blacklisted=lambda *a, **k: False,
        _get_connection=lambda: SimpleNamespace(close=lambda: None))
    started = []
    deps = SimpleNamespace(
        get_database=lambda: fake_db,
        update_task_status=lambda task_id, status: started.append(status))
    monkeypatch.setattr('core.downloads.failed_blocklist.is_candidate_blocked',
                        lambda *a, **k: False)

    download_tasks['t-up'] = {'status': 'searching',
                              CONTEXT_KEY: issue_upgrade_intent(41, origin='wishlist')}
    try:
        track = SimpleNamespace(name='Another Night', duration_ms=236_000)
        same_source = SimpleNamespace(username='deezer_dl', filename='dz||1',
                                      audio_quality=AudioQuality(format='mp3', bitrate=320))
        no_better = SimpleNamespace(username='some_peer', filename='a.mp3',
                                    audio_quality=AudioQuality(format='mp3', bitrate=128))
        assert cands.attempt_download_with_candidates(
            't-up', [same_source, no_better], track, deps=deps) is False
        assert download_tasks['t-up']['start_failures'] == [
            'the same source just delivered the file this would replace',
            'no better than the file you have']
        # worked out once for the task, and nothing was started
        assert asked == [41] and started == []
    finally:
        download_tasks.pop('t-up', None)
