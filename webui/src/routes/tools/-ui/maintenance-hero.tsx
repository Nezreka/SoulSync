/**
 * The Library Maintenance hero — master toggle, the four sections (health,
 * findings, operations, history) and the job list they share.
 *
 * The three tabs are gone. Tabs made the page a set of rooms you had to know
 * to walk into: the findings you needed to act on were behind a tab that
 * looked identical to the two you didn't want, and nothing on screen told you
 * whether your library was actually alright. It is one scroll surface now,
 * ordered by what you came here to learn: how healthy am I → what is wrong →
 * what is running → what happened. The nav jumps; it doesn't hide.
 *
 * The hero owns the job list because two sections need it: operations renders
 * it, and the findings filter is populated from it — which is exactly why the
 * vanilla filled that `<select>` from inside `loadRepairJobs`.
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

import type { RepairJob, RepairJobProgress, RepairJobRun, RepairSection } from '../-tools.types';

import {
  fetchDatabaseStats,
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
  repairJobBadge,
  repairJobCardClass,
  repairJobDot,
  repairJobMeta,
  repairSettingInput,
} from '../-tools.core';
import { useRepairProgressEvent, useRepairStatusEvent } from '../-tools.events';
import { FindingsSurface } from './findings-surface';
import { RunHistory } from './run-history';

/** The vanilla hides a finished job's progress panel 30s after it lands. */
const PROGRESS_HIDE_MS = 30000;

/** Optional-called: jsdom has no scrollIntoView, and a nav button is not
 *  worth failing a render over. */
function jumpToSection(anchor: string) {
  document.getElementById(anchor)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
}

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

/** The nav. Jumping, not hiding — every section is on the page already. */
const SECTIONS: Array<{ id: RepairSection; label: string; anchor: string }> = [
  { id: 'health', label: 'Health', anchor: 'repair-section-health' },
  { id: 'findings', label: 'Findings', anchor: 'repair-section-findings' },
  { id: 'operations', label: 'Operations', anchor: 'repair-section-operations' },
  { id: 'history', label: 'History', anchor: 'repair-section-history' },
];

export function MaintenanceHero() {
  const [enabled, setEnabled] = useState(false);
  // Driven by the same /api/repair/status payload as the master toggle. Hidden
  // at zero rather than showing a "0" pill, matching updateRepairStatusFromData.
  const [findingsPending, setFindingsPending] = useState(0);
  const [jobs, setJobs] = useState<RepairJob[] | null>(null);
  const [jobsError, setJobsError] = useState(false);
  const [history, setHistory] = useState<RepairJobRun[] | null>(null);
  const [historyError, setHistoryError] = useState(false);
  /** Library size — the health score is per 1,000 tracks, so 200 orphans in a
   *  2,000-track library and in a 200,000-track one don't score the same. */
  const [trackCount, setTrackCount] = useState<number | null>(null);
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
    void loadHistory();
    void fetchDatabaseStats().then((stats) => {
      if (stats) setTrackCount(stats.tracks || 0);
    });
    void fetchRepairProgress().then((frames) => {
      if (Object.keys(frames).length) setProgress(frames);
    });
  }, [loadHistory, loadJobs]);

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

  /**
   * A run row's "see this job's findings" jump. The token makes a second
   * click on the same job re-fire — without it, clicking the same row twice
   * after wandering off would change nothing.
   */
  const [jobFocus, setJobFocus] = useState<{ jobId: string; token: number } | null>(null);
  const showJobFindings = useCallback((jobId: string) => {
    setJobFocus((previous) => ({ jobId, token: (previous?.token || 0) + 1 }));
    jumpToSection('repair-section-findings');
  }, []);

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

      <nav className="repair-section-nav" aria-label="Maintenance sections">
        {SECTIONS.map((section) => (
          <button
            className="repair-section-link"
            type="button"
            data-section={section.id}
            key={section.id}
            onClick={() => jumpToSection(section.anchor)}
          >
            {section.label}
            {section.id === 'findings' ? (
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
      </nav>

      <FindingsSurface
        jobs={jobs || []}
        runs={history || []}
        trackCount={trackCount}
        focusJob={jobFocus}
        onStatusChanged={refreshStatus}
      />

      <section className="repair-section" id="repair-section-operations">
        <h4 className="repair-section-title">Maintenance jobs</h4>
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
      </section>

      <section className="repair-section" id="repair-section-history">
        <h4 className="repair-section-title">Recent runs</h4>
        <div className="repair-runs-card" id="repair-history-list">
          <RunHistory
            runs={history}
            error={historyError}
            onShowFindings={showJobFindings}
            onRefresh={() => void loadHistory()}
          />
        </div>
      </section>

      {helpJob ? <JobHelpOverlay job={helpJob} onClose={() => setHelpJob(null)} /> : null}
    </div>
  );
}
