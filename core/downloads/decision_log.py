"""Remember why an automatic grab took what it took.

The worker judges hits with ``evaluate_candidates`` and hands the pool here
when it either starts a download or gives up. Only a summary is kept (the
winner, the ten closest alternatives, counts per reason code): on the task in
memory for the live downloads page, and in ``download_decisions`` so it
outlives the task. Recording never gets in the way of a download.
"""

from __future__ import annotations

from typing import Iterable, Optional

from core.downloads.candidate_pool import ALTERNATIVES_KEPT, summarize_pool
from core.runtime_state import download_tasks, tasks_lock
from utils.logging_config import get_logger

logger = get_logger("downloads.decision_log")

OUTCOMES = ('chosen', 'nothing_passed', 'download_failed')


def _database():
    from database.music_database import get_database
    return get_database()


def _artist_of(track) -> str:
    artists = getattr(track, 'artists', None) or []
    first = artists[0] if artists else ''
    if isinstance(first, dict):
        first = first.get('name', '')
    return str(first or '')


def record(task_id: str, pool: Iterable[tuple], *, outcome: str, track=None,
           quality_profile_id=None, merge: bool = False, database=None) -> Optional[dict]:
    """Summarize ``pool`` for ``task_id`` and store it. Returns the summary.

    ``merge``: this search generation only re-ran the queries a quarantine
    retry hadn't tried yet, so its pool is partial. Fold it into what the task
    already knew instead of replacing it.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown decision outcome {outcome!r}")
    try:
        with tasks_lock:
            task = download_tasks.get(task_id) or {}
            chosen_key = None
            if outcome == 'chosen' and task.get('username') and task.get('filename'):
                chosen_key = (task['username'], task['filename'])
            previous = dict(task.get('decision_summary') or {}) if merge else {}
        summary = summarize_pool(pool, chosen_key=chosen_key)
        if outcome == 'chosen' and summary['chosen'] is None and chosen_key:
            summary['chosen'] = _bare_chosen(*chosen_key)
        if previous:
            summary = _merge(previous, summary)
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['decision_summary'] = {'outcome': outcome, **summary}
        (database or _database()).record_download_decision(
            task_id, outcome=outcome, summary=summary,
            track_title=str(getattr(track, 'name', '') or ''),
            track_artist=_artist_of(track),
            quality_profile_id=quality_profile_id,
        )
        return summary
    except Exception as exc:  # noqa: BLE001 - a record must never cost a download
        logger.debug("[Decisions] could not record %s for %s: %s", outcome, task_id, exc)
        return None


def note_retry_winner(task_id: str, *, database=None) -> None:
    """A retry took a different cached candidate: move 'chosen' to it."""
    try:
        with tasks_lock:
            task = download_tasks.get(task_id) or {}
            summary = dict(task.get('decision_summary') or {})
            key = (task.get('username'), task.get('filename'))
        if not summary or not all(key):
            return
        name = key[1].replace('\\', '/').rsplit('/', 1)[-1]
        previous = summary.get('chosen')
        alternatives = list(summary.get('alternatives') or [])
        match = next((a for a in alternatives
                      if a.get('username') == key[0] and a.get('display_name') == name), None)
        if match is not None:
            alternatives.remove(match)
        if previous:
            alternatives.insert(0, previous)
        summary.update(outcome='chosen', chosen=match or _bare_chosen(*key),
                       alternatives=alternatives[:ALTERNATIVES_KEPT])
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['decision_summary'] = summary
        stored = {k: v for k, v in summary.items() if k != 'outcome'}
        (database or _database()).record_download_decision(
            task_id, outcome='chosen', summary=stored,
            track_title=_title_of(task), track_artist=_task_artist(task),
            quality_profile_id=(task.get('track_info') or {}).get('quality_profile_id')
            if isinstance(task.get('track_info'), dict) else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Decisions] could not move the winner for %s: %s", task_id, exc)


def _merge(previous: dict, latest: dict) -> dict:
    counts = dict(previous.get('rejected_counts') or {})
    for code, n in (latest.get('rejected_counts') or {}).items():
        counts[code] = counts.get(code, 0) + n
    pool = list(latest.get('alternatives') or []) + list(previous.get('alternatives') or [])
    if previous.get('chosen'):
        pool.insert(0, previous['chosen'])  # tried, and it didn't stick
    seen, alternatives = set(), []
    chosen = latest.get('chosen')
    if chosen:
        seen.add((chosen.get('username'), chosen.get('display_name')))
    for alt in pool:
        key = (alt.get('username'), alt.get('display_name'))
        if key not in seen:
            seen.add(key)
            alternatives.append(alt)
    alternatives.sort(key=lambda a: (
        not (a.get('decision') or {}).get('accepted'),
        -float((a.get('decision') or {}).get('score') or a.get('confidence') or 0),
    ))
    return {
        'chosen': chosen,
        'alternatives': alternatives[:ALTERNATIVES_KEPT],
        'accepted_total': int(previous.get('accepted_total') or 0) + int(latest.get('accepted_total') or 0),
        'rejected_total': int(previous.get('rejected_total') or 0) + int(latest.get('rejected_total') or 0),
        'rejected_counts': dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }


def _bare_chosen(username: str, filename: str) -> dict:
    """A winner the pool never saw (the YouTube ytsearch fallback)."""
    return {
        'username': username,
        'display_name': str(filename).replace('\\', '/').rsplit('/', 1)[-1],
        'source_service': '',
        'quality': '', 'quality_label': '', 'bitrate': 0, 'size': 0, 'duration': 0,
        'confidence': 0.0,
        'decision': {'accepted': True, 'code': 'accepted', 'detail': '', 'stage': 'decision',
                     'score': None},
    }


def _title_of(task: dict) -> str:
    ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
    return str(ti.get('name') or '')


def _task_artist(task: dict) -> str:
    ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
    artists = ti.get('artists') or []
    first = artists[0] if artists else ''
    if isinstance(first, dict):
        first = first.get('name', '')
    return str(first or ti.get('artist') or '')
