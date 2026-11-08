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
