"""Automation handler: ``run_repair_job`` action.

Runs a music Library Maintenance job from an automation, the music twin of
``video_run_repair_job``. Lets users put a job on a cadence the Tools-page
interval can't express, or chain it ("Scan Library -> Corrupt File Detector ->
Discord me the findings" with the Maintenance Finding Raised trigger). Queuing
rides the repair worker's force-run queue, so it is one job at a time and runs
even while the Tools master switch is off, exactly like a manual Run Now.

``job_id`` config: one job id, or ``all`` = every job.

Unlike Run Now, a job switched off in Tools stays off here too: this is a
background trigger, and the toggle is the user's statement about resources
(#1207). ``run_repair_job_now(..., respect_enabled=True)`` enforces that.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_run_repair_job(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from core.repair_jobs import get_all_jobs

    automation_id = (config or {}).get('_automation_id')
    want = str((config or {}).get('job_id') or 'all').strip()
    jobs = get_all_jobs()

    if want == 'all':
        targets = list(jobs)
    elif want in jobs:
        targets = [want]
    else:
        return {'status': 'error', 'error': f"unknown maintenance job '{want}'"}

    queued = [job_id for job_id in targets
              if deps.run_repair_job_now(job_id, respect_enabled=True)]
    names = ', '.join(jobs[job_id].display_name for job_id in queued)

    if not queued:
        reason = ('every maintenance job is switched off in Tools' if want == 'all'
                  else f'{jobs[want].display_name} is switched off in Tools')
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Skipped',
            log_line=f'Nothing queued: {reason}', log_type='info')
        return {'status': 'skipped', 'queued': 0, 'jobs': '', 'reason': reason,
                '_manages_own_progress': True}

    deps.update_progress(
        automation_id, status='finished', progress=100, phase='Queued',
        log_line=f'Queued {names} (findings appear in Library Maintenance)',
        log_type='success')
    return {'status': 'completed', 'queued': len(queued), 'jobs': ', '.join(queued),
            'summary': f'Queued {len(queued)} maintenance job(s)',
            '_manages_own_progress': True}
