/**
 * The Library Maintenance hero — master toggle, three tabs, the job list, the run
 * history and (via FindingsTab) the findings pane.
 *
 * The hero owns the job list because two tabs need it: the Jobs tab renders it,
 * and the Findings tab's filter dropdown is populated from it — which is exactly
 * why the vanilla filled that `<select>` from inside `loadRepairJobs`.
 *
 * Two contracts from the P0 that outlive this file:
 *
 * 1. `.repair-job-card[data-job-id]` — the vanilla socket handler used to find
 *    cards by this selector and write progress into them. My P6-era claim that
 *    the markup deletion would make that a no-op was WRONG: this component
 *    re-renders the very selector it queries, so the vanilla body was stomping
 *    React-managed nodes. The post-flip hardening reduced that handler to its
 *    ss:repair-progress dispatch; the attribute stays as the stable per-job
 *    hook the tests (and any future e2e) key on.
 *
 * 2. The class on the card is NOT the class on the status dot. An idle enabled
 *    job gets dot 'enabled' and card class '' — see repairJobCardClass.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { RepairJob, RepairJobProgress, RepairJobRun, RepairTab } from '../-tools.types';

import {
  fetchRepairHistory,
  fetchRepairJobs,
  fetchRepairProgress,
  fetchRepairStatus,
  runRepairJob,
  saveRepairJobSettings,
  setRepairJobEnabled,
  stopRepairJob,
  toggleRepairMaster,
} from '../-tools.api';
import {
  coerceRepairSettingValue,
  formatCacheAge,
  isRepairJobDryRun,
  prettifyRepairSettingKey,
  repairHistoryStats,
  repairHistoryStatusClass,
  repairJobBadge,
  repairJobCardClass,
  repairJobDot,
  repairJobMeta,
  repairSettingInput,
} from '../-tools.core';
import { useRepairProgressEvent, useRepairStatusEvent } from '../-tools.events';
import { FindingsTab } from './findings-tab';

/** The vanilla hides a finished job's progress panel 30s after it lands. */
const PROGRESS_HIDE_MS = 30000;

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

/**
 * A setting value as input text. Settings are primitives in practice, but the
 * payload is typed `unknown` because the backend is free to add a shape — and
 * `String({})` would silently render "[object Object]" into an input the user
 * could then save back.
 */
function settingText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

// ── Job settings editor ──────────────────────────────────────────────────────

interface JobSettingsProps {
  job: RepairJob;
  onSaved: () => void;
}

function JobSettings({ job, onSaved }: JobSettingsProps) {
  const [intervalHours, setIntervalHours] = useState(String(job.interval_hours));
  const [values, setValues] = useState<Record<string, unknown>>(() => ({ ...job.settings }));
  const [open, setOpen] = useState(false);

  const save = useCallback(async () => {
    const settings: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(values)) {
      // Section sentinels are display-only; they are not settings and the
      // vanilla never collects them off the DOM because they render as a div.
      if (key.startsWith('_section_')) continue;
      settings[key] = typeof value === 'string' ? coerceRepairSettingValue(value) : value;
    }
    try {
      await saveRepairJobSettings(job.job_id, Number.parseInt(intervalHours, 10) || 24, settings);
      toast('Settings saved', 'success');
      onSaved();
    } catch {
      toast('Error saving settings', 'error');
    }
  }, [intervalHours, job.job_id, onSaved, values]);

  return (
    <>
      <button
        className="repair-settings-btn"
        type="button"
        title="Settings"
        onClick={() => setOpen((previous) => !previous)}
      >
        &#9881;
      </button>
      <div
        className="repair-job-settings"
        id={`repair-settings-${job.job_id}`}
        style={{ display: open ? '' : 'none' }}
      >
        <div className="repair-setting-row">
          <label>Interval (hours)</label>
          <input
            type="number"
            className="repair-setting-input"
            data-job={job.job_id}
            data-key="_interval_hours"
            value={intervalHours}
            min="1"
            step="1"
            onChange={(event) => setIntervalHours(event.target.value)}
          />
        </div>
        {Object.entries(job.settings || {}).map(([key, value]) => {
          const field = repairSettingInput(key, value, job.setting_options?.[key]);
          if (field.kind === 'section') {
            return (
              <div className="repair-setting-section" key={key}>
                {field.title}
              </div>
            );
          }
          const current = values[key];
          return (
            <div className="repair-setting-row" key={key}>
              <label>{prettifyRepairSettingKey(key)}</label>
              {field.kind === 'select' ? (
                <select
                  className="repair-setting-input"
                  data-job={job.job_id}
                  data-key={key}
                  value={settingText(current)}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, [key]: event.target.value }))
                  }
                >
                  {field.options.map((option) => (
                    <option value={option} key={option}>
                      {prettifyRepairSettingKey(option)}
                    </option>
                  ))}
                </select>
              ) : field.kind === 'checkbox' ? (
                <input
                  type="checkbox"
                  className="repair-setting-input"
                  data-job={job.job_id}
                  data-key={key}
                  checked={Boolean(current)}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, [key]: event.target.checked }))
                  }
                />
              ) : field.kind === 'number' ? (
                <input
                  type="number"
                  className="repair-setting-input"
                  data-job={job.job_id}
                  data-key={key}
                  value={settingText(current)}
                  step="0.01"
                  // A setting that is CURRENTLY negative gets no floor — some
                  // thresholds are legitimately below zero and a min of 0 would
                  // make them un-editable.
                  {...(field.allowNegative ? {} : { min: '0' })}
                  onChange={(event) =>
                    setValues((previous) => ({
                      ...previous,
                      [key]: Number.parseFloat(event.target.value),
                    }))
                  }
                />
              ) : (
                <input
                  type="text"
                  className="repair-setting-input"
                  data-job={job.job_id}
                  data-key={key}
                  value={settingText(current)}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, [key]: event.target.value }))
                  }
                />
              )}
            </div>
          );
        })}
        <button className="repair-save-settings-btn" type="button" onClick={() => void save()}>
          Save Settings
        </button>
      </div>
    </>
  );
}

// ── Job card ─────────────────────────────────────────────────────────────────

interface JobCardProps {
  job: RepairJob;
  progress?: RepairJobProgress;
  onChanged: () => void;
  onHelp: (job: RepairJob) => void;
}

function JobCard({ job, progress, onChanged, onHelp }: JobCardProps) {
  const [enabled, setEnabled] = useState(job.enabled);
  const [stopping, setStopping] = useState(false);
  useEffect(() => setEnabled(job.enabled), [job.enabled]);

  const badge = repairJobBadge(job);
  const meta = repairJobMeta(job);
  const hasSettings = Object.keys(job.settings || {}).length > 0;
  const dryRun = isRepairJobDryRun(job);

  const onToggle = useCallback(
    async (next: boolean) => {
      setEnabled(next); // optimistic, exactly as the vanilla flips the card
      try {
        await setRepairJobEnabled(job.job_id, next);
      } catch {
        toast('Error toggling job', 'error');
      }
    },
    [job.job_id],
  );

  const onRun = useCallback(async () => {
    try {
      await runRepairJob(job.job_id);
      toast('Job started', 'success');
      setTimeout(onChanged, 1000);
    } catch {
      toast('Error starting job', 'error');
    }
  }, [job.job_id, onChanged]);

  const onStop = useCallback(async () => {
    // The scan can't unwind until its current item returns, so the stop is not
    // instant — the button says so rather than looking unresponsive.
    setStopping(true);
    try {
      const result = await stopRepairJob(job.job_id);
      toast(
        result.stopped ? 'Stopping job…' : 'Job is not running',
        result.stopped ? 'success' : 'info',
      );
      if (!result.stopped) setTimeout(onChanged, 600);
    } catch {
      toast('Error stopping job', 'error');
      setStopping(false);
    }
  }, [job.job_id, onChanged]);

  return (
    <div className={`repair-job-card ${repairJobCardClass(job)}`.trim()} data-job-id={job.job_id}>
      <div className="repair-job-main">
        <div className={`repair-job-status ${repairJobDot(job)}`} />
        <div className="repair-job-info">
          <div className="repair-job-name">{job.display_name}</div>
          <div className="repair-job-desc">{job.description || ''}</div>
          <div className="repair-job-flow">
            <span className="repair-flow-badge scan">
              {job.is_running ? <>&#9654; Running</> : 'Scan'}
            </span>
            {job.auto_fix ? (
              <>
                <span className="repair-flow-arrow">&rarr;</span>
                <span className={`repair-flow-badge ${dryRun ? 'dryrun' : 'autofix'}`}>
                  {dryRun ? 'Dry Run' : 'Auto-fix'}
                </span>
              </>
            ) : null}
            {badge.kind !== 'none' ? (
              <>
                <span className="repair-flow-arrow">&rarr;</span>
                <span
                  className={
                    badge.kind === 'pending'
                      ? 'repair-flow-badge findings'
                      : 'repair-flow-badge findings findings-historical'
                  }
                >
                  {badge.kind === 'pending'
                    ? `${badge.count.toLocaleString()} pending`
                    : `${badge.count} found in last scan`}
                </span>
              </>
            ) : null}
          </div>
          <div className="repair-job-meta">{meta.join(' · ')}</div>
        </div>
        <div className="repair-job-actions">
          <label className="repair-job-toggle">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => void onToggle(event.target.checked)}
            />
            <span className="repair-toggle-slider small" />
          </label>
          {job.is_running ? (
            <button
              className={`repair-stop-btn${stopping ? ' stopping' : ''}`}
              type="button"
              disabled={stopping}
              title={stopping ? 'Stopping…' : 'Stop this run'}
              onClick={() => void onStop()}
            >
              &#9209;
            </button>
          ) : (
            <button
              className="repair-run-btn"
              type="button"
              title="Run now"
              onClick={() => void onRun()}
            >
              &#9654;
            </button>
          )}
          {hasSettings ? <JobSettings job={job} onSaved={onChanged} /> : null}
          <button
            className="repair-help-btn"
            type="button"
            title="About this job"
            onClick={(event) => {
              event.stopPropagation();
              onHelp(job);
            }}
          >
            ?
          </button>
        </div>
      </div>
      {progress ? (
        <div
          className={`repair-job-progress visible${progress.status === 'finished' ? ' finished' : ''}${
            progress.status === 'error' ? ' error' : ''
          }`}
        >
          <div className="repair-progress-bar-wrap">
            <div className="repair-progress-bar" style={{ width: `${progress.progress || 0}%` }} />
          </div>
          <div className="repair-progress-phase">{progress.phase || ''}</div>
          <div className="repair-progress-log">
            {(progress.log || []).map((line, index) => (
              <div
                className={`repair-log-line ${line.type || 'info'}`}
                key={`${index}-${line.text}`}
              >
                {line.text}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ── History ──────────────────────────────────────────────────────────────────

function HistoryEntry({ run }: { run: RepairJobRun }) {
  const statusClass = repairHistoryStatusClass(run.status);
  const duration = run.duration_seconds ? `${run.duration_seconds.toFixed(1)}s` : '-';
  return (
    <div className="repair-history-entry">
      <div className="repair-history-header">
        <div className={`repair-history-dot ${statusClass}`} />
        <span className="repair-history-name">{run.display_name || run.job_id}</span>
        <span className={`repair-history-status ${statusClass}`}>{run.status}</span>
        <span className="repair-history-duration">{duration}</span>
      </div>
      <div className="repair-history-stats">
        {repairHistoryStats(run).map((stat) => (
          <span className={`repair-history-stat ${stat.kind}`.trim()} key={stat.label}>
            <strong>{stat.count.toLocaleString()}</strong> {stat.label}
          </span>
        ))}
      </div>
      <div className="repair-history-meta">
        {formatCacheAge(run.started_at)} &middot;{' '}
        {run.started_at ? new Date(run.started_at).toLocaleString() : '-'} &rarr;{' '}
        {run.finished_at ? new Date(run.finished_at).toLocaleString() : 'In progress'}
      </div>
    </div>
  );
}

function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
  return (
    <div className="repair-empty-state">
      <div className="repair-empty-icon">{icon}</div>
      <div className="repair-empty-title">{title}</div>
      <div className="repair-empty-text">{text}</div>
    </div>
  );
}

// ── Job help overlay ─────────────────────────────────────────────────────────

/**
 * `showRepairJobHelp`. Built from the job payload the list already holds, which
 * is why it needs no fetch.
 *
 * The help text has a small format of its own: paragraphs are split on a blank
 * line, and a paragraph starting with "Settings:" becomes a bulleted list with
 * the leading "- " stripped from each line.
 */
function JobHelpOverlay({ job, onClose }: { job: RepairJob; onClose: () => void }) {
  const dryRun = isRepairJobDryRun(job);
  const settingRows = Object.entries(job.settings || {}).filter(
    ([key]) => !key.startsWith('_section_'),
  );

  const paragraphs = (job.help_text || job.description || '').split('\n\n');

  return (
    <div
      className="repair-help-overlay"
      id="repair-help-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="repair-help-modal">
        <div className="repair-help-header">
          <h3>{job.display_name}</h3>
          <button className="repair-help-close" type="button" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="repair-help-badges">
          {job.auto_fix ? (
            <span className={`repair-flow-badge ${dryRun ? 'dryrun' : 'autofix'}`}>
              {dryRun ? 'Dry Run' : 'Auto-fix'}
            </span>
          ) : (
            <span className="repair-flow-badge scan">Scan Only</span>
          )}
          <span className="repair-flow-badge scan">Every {job.interval_hours}h</span>
          {job.enabled ? (
            <span
              className="repair-flow-badge"
              style={{ background: 'rgba(74,222,128,0.12)', color: '#4ade80' }}
            >
              Enabled
            </span>
          ) : (
            <span
              className="repair-flow-badge"
              style={{ background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.4)' }}
            >
              Disabled
            </span>
          )}
        </div>
        <div className="repair-help-body">
          {paragraphs.map((paragraph, index) =>
            paragraph.startsWith('Settings:\n') ? (
              <div className="repair-help-setting-list" key={index}>
                {paragraph
                  .split('\n')
                  .slice(1)
                  .map((line, lineIndex) => (
                    <div className="repair-help-setting-item" key={lineIndex}>
                      {line.replace(/^- /, '')}
                    </div>
                  ))}
              </div>
            ) : (
              <p key={index}>
                {paragraph.split('\n').map((line, lineIndex, lines) => (
                  <span key={lineIndex}>
                    {line}
                    {lineIndex < lines.length - 1 ? <br /> : null}
                  </span>
                ))}
              </p>
            ),
          )}
        </div>
        {settingRows.length ? (
          <div className="repair-help-settings-section">
            <div className="repair-help-section-title">Current Settings</div>
            {settingRows.map(([key, value]) => (
              <div className="repair-help-setting" key={key}>
                <span className="repair-help-setting-key">{prettifyRepairSettingKey(key)}</span>
                <span className="repair-help-setting-val">
                  {typeof value === 'boolean' ? (value ? 'Yes' : 'No') : settingText(value)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── The hero ─────────────────────────────────────────────────────────────────

export function MaintenanceHero() {
  const [tab, setTab] = useState<RepairTab>('jobs');
  const [enabled, setEnabled] = useState(false);
  // Driven by the same /api/repair/status payload as the master toggle. Hidden
  // at zero rather than showing a "0" pill, matching updateRepairStatusFromData.
  const [findingsPending, setFindingsPending] = useState(0);
  const [jobs, setJobs] = useState<RepairJob[] | null>(null);
  const [jobsError, setJobsError] = useState(false);
  const [history, setHistory] = useState<RepairJobRun[] | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const [progress, setProgress] = useState<Record<string, RepairJobProgress>>({});
  const hideTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await fetchRepairJobs());
      setJobsError(false);
    } catch {
      setJobsError(true);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await fetchRepairHistory(50));
      setHistoryError(false);
    } catch {
      setHistoryError(true);
    }
  }, []);

  /** `updateRepairStatus` — the findings tab calls this after every mutation so
   *  the pending badge tracks what it just changed. */
  const refreshStatus = useCallback(() => {
    void fetchRepairStatus().then((status) => {
      if (!status) return;
      setEnabled(Boolean(status.enabled));
      setFindingsPending(status.findings_pending || 0);
    });
  }, []);

  // Live push. The vanilla's `updateRepairStatusFromData` writes the orb and its
  // tooltip (dashboard markup it owns); the same frame arrives here for the two
  // nodes this component owns. Without it the badge and the toggle only moved
  // when something in this page happened to refetch.
  useRepairStatusEvent(
    useCallback((frame) => {
      setEnabled(Boolean(frame.enabled));
      setFindingsPending(frame.findings_pending || 0);
    }, []),
  );

  // Job frames are PARTIAL — the vanilla iterates Object.entries(data) and
  // touches only the jobs named in it, leaving the rest alone. Merge, never
  // replace, or a frame about one job would blank every other job's panel.
  useRepairProgressEvent(
    useCallback((frames) => {
      if (Object.keys(frames).length) setProgress((previous) => ({ ...previous, ...frames }));
    }, []),
  );

  // `openRepairModal` hydrated the master state, the job list and any in-flight
  // progress on open; the tab switch drove the rest.
  useEffect(() => {
    void fetchRepairStatus().then((status) => {
      if (!status) return;
      setEnabled(Boolean(status.enabled));
      setFindingsPending(status.findings_pending || 0);
    });
    void loadJobs();
    void fetchRepairProgress().then((frames) => {
      if (Object.keys(frames).length) setProgress(frames);
    });
  }, [loadJobs]);

  useEffect(() => {
    if (tab === 'history') void loadHistory();
  }, [loadHistory, tab]);

  // A finished panel hides itself after 30s and the list reloads for fresh
  // stats — same contract as the vanilla's _repairProgressHideTimers.
  useEffect(() => {
    const timers = hideTimers.current;
    for (const [jobId, frame] of Object.entries(progress)) {
      const done = frame.status === 'finished' || frame.status === 'error';
      if (done && !timers[jobId]) {
        timers[jobId] = setTimeout(() => {
          delete timers[jobId];
          setProgress((previous) => {
            const next = { ...previous };
            delete next[jobId];
            return next;
          });
          void loadJobs();
        }, PROGRESS_HIDE_MS);
      } else if (!done && timers[jobId]) {
        clearTimeout(timers[jobId]);
        delete timers[jobId];
      }
    }
  }, [loadJobs, progress]);

  useEffect(() => {
    const timers = hideTimers.current;
    return () => {
      for (const timer of Object.values(timers)) clearTimeout(timer);
    };
  }, []);

  const onMasterToggle = useCallback(async () => {
    try {
      const result = await toggleRepairMaster();
      setEnabled(result.enabled);
    } catch {
      toast('Error toggling maintenance worker', 'error');
    }
  }, []);

  const [helpJob, setHelpJob] = useState<RepairJob | null>(null);

  return (
    <div className="tools-maintenance-hero">
      <div className="tools-maintenance-header">
        <div className="tools-maintenance-header-left">
          <img src="/static/whisoul.png" alt="" className="tools-maintenance-logo" />
          <div>
            <h3 className="tools-maintenance-title">Library Maintenance</h3>
            <p className="tools-maintenance-subtitle">
              Automated scanning, detection, and repair of library issues
            </p>
          </div>
        </div>
        <label className="repair-master-toggle">
          <input
            type="checkbox"
            id="repair-master-toggle"
            checked={enabled}
            onChange={() => void onMasterToggle()}
          />
          <span className="repair-toggle-slider" />
          <span className="repair-toggle-label" id="repair-master-label">
            {enabled ? 'Enabled' : 'Disabled'}
          </span>
        </label>
      </div>

      <div className="repair-tabs">
        {(['jobs', 'findings', 'history'] as const).map((name) => (
          <button
            className={`repair-tab${tab === name ? ' active' : ''}`}
            type="button"
            data-tab={name}
            key={name}
            onClick={() => setTab(name)}
          >
            {name === 'jobs' ? 'Jobs' : name === 'findings' ? 'Findings' : 'History'}
            {name === 'findings' ? (
              <span
                className="repair-tab-badge"
                id="repair-findings-tab-badge"
                style={{ display: findingsPending > 0 ? '' : 'none' }}
              >
                {findingsPending}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      <div
        className="repair-tab-content"
        id="repair-tab-jobs"
        style={{ display: tab === 'jobs' ? '' : 'none' }}
      >
        <div className="repair-jobs-list" id="repair-jobs-list">
          {jobsError ? (
            <div className="repair-empty">Error loading jobs</div>
          ) : jobs === null ? (
            <div className="repair-loading">Loading jobs...</div>
          ) : jobs.length === 0 ? (
            <EmptyState
              icon="🔧"
              title="No Maintenance Jobs"
              text="Library maintenance jobs will appear here once available."
            />
          ) : (
            jobs.map((job) => (
              <JobCard
                job={job}
                progress={progress[job.job_id]}
                onChanged={loadJobs}
                onHelp={setHelpJob}
                key={job.job_id}
              />
            ))
          )}
        </div>
      </div>

      <div
        className="repair-tab-content"
        id="repair-tab-findings"
        style={{ display: tab === 'findings' ? '' : 'none' }}
      >
        <FindingsTab
          jobs={jobs || []}
          active={tab === 'findings'}
          onStatusChanged={refreshStatus}
        />
      </div>

      <div
        className="repair-tab-content"
        id="repair-tab-history"
        style={{ display: tab === 'history' ? '' : 'none' }}
      >
        <div className="repair-history-list" id="repair-history-list">
          {historyError ? (
            <div className="repair-empty">Error loading history</div>
          ) : history === null ? (
            <div className="repair-loading">Loading history...</div>
          ) : history.length === 0 ? (
            <EmptyState
              icon="&#128337;"
              title="No History Yet"
              text="Job run history will appear here after maintenance jobs complete their first scan."
            />
          ) : (
            history.map((run, index) => <HistoryEntry run={run} key={`${run.job_id}-${index}`} />)
          )}
        </div>
      </div>

      {helpJob ? <JobHelpOverlay job={helpJob} onClose={() => setHelpJob(null)} /> : null}
    </div>
  );
}
