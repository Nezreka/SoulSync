/**
 * Operations — the maintenance jobs, grouped and given a face.
 *
 * What this replaced: thirty identical stacked cards in one undifferentiated
 * list, each carrying a name, a description, a row of badges and a settings
 * gear. No order, no organisation, and the one thing a user actually wants to
 * know about a job — when does this run, and can I change that — was a bare
 * "Interval (hours)" box hidden inside the gear.
 *
 * Now: containers per family (served by the backend, not guessed here), each
 * summarising itself so a collapsed one still tells you something, and a tile
 * per job whose face carries the cadence as an editable control.
 *
 * Two contracts from the P0 that outlive the redesign:
 *
 * 1. `.repair-job-card[data-job-id]` — the stable per-job hook the socket
 *    dispatch and the tests key on. It stays on the tile root.
 * 2. The class on the card is NOT the class on the status dot. An idle
 *    enabled job gets dot 'enabled' and card class '' — see
 *    repairJobCardClass.
 *
 * The glow edge is the progress bar: it idles as a family-coloured hairline
 * and fills left-to-right while that job scans. Same idea as the dashboard's
 * worker orbs — one object carrying identity, state and control — rather than
 * a separate progress panel that appears and shoves the layout around.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { Cadence, IntervalUnit } from '../-tools.ops';
import type { RepairJob, RepairJobProgress, RepairJobRun } from '../-tools.types';

import {
  runRepairJob,
  saveRepairJobSettings,
  setRepairJobEnabled,
  stopRepairJob,
} from '../-tools.api';
import {
  coerceRepairSettingValue,
  isRepairJobDryRun,
  prettifyRepairSettingKey,
  repairJobBadge,
  repairJobCardClass,
  repairJobDot,
  repairJobMeta,
  repairSettingInput,
} from '../-tools.core';
import { sparklinePoints } from '../-tools.groups';
import {
  cadenceFromHours,
  cadenceLabel,
  familySummary,
  hoursFromCadence,
  jobFamilies,
  jobSchedule,
  jobTrend,
} from '../-tools.ops';

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

// ── Cadence editor ───────────────────────────────────────────────────────────

const UNITS: IntervalUnit[] = ['hours', 'days', 'weeks'];

/**
 * The schedule control, on the tile face.
 *
 * It speaks the same language as the auto-sync page — an interval and a unit —
 * rather than the raw hours the config stores. What it deliberately does NOT
 * offer is a time of day: the worker is a staleness queue that runs whichever
 * enabled job is furthest past its interval whenever it is idle, so "every 6
 * hours" is a promise it can keep and "at 03:00" is not.
 */
function CadenceEditor({ job, onSaved }: { job: RepairJob; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Cadence>(() => cadenceFromHours(job.interval_hours));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(cadenceFromHours(job.interval_hours));
  }, [editing, job.interval_hours]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      // The other settings ride along unchanged — the endpoint replaces the
      // whole settings blob, so sending only the interval would wipe them.
      await saveRepairJobSettings(job.job_id, hoursFromCadence(draft), { ...(job.settings || {}) });
      toast(`${job.display_name} now runs ${cadenceLabel(hoursFromCadence(draft))}`, 'success');
      setEditing(false);
      onSaved();
    } catch {
      toast('Could not save the schedule', 'error');
    } finally {
      setSaving(false);
    }
  }, [draft, job.display_name, job.job_id, job.settings, onSaved]);

  if (!editing) {
    return (
      <button
        type="button"
        className="repair-tile-cadence"
        title="Change how often this job runs"
        onClick={() => setEditing(true)}
      >
        {cadenceLabel(job.interval_hours)}
      </button>
    );
  }

  return (
    <span className="repair-tile-cadence-edit" onClick={(event) => event.stopPropagation()}>
      <input
        type="number"
        min="1"
        step="1"
        aria-label="Interval"
        value={String(draft.interval)}
        onChange={(event) =>
          setDraft((current) => ({
            ...current,
            interval: Math.max(1, Number.parseInt(event.target.value, 10) || 1),
          }))
        }
      />
      <select
        aria-label="Interval unit"
        value={draft.unit}
        onChange={(event) =>
          setDraft((current) => ({ ...current, unit: event.target.value as IntervalUnit }))
        }
      >
        {UNITS.map((unit) => (
          <option value={unit} key={unit}>
            {unit}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="repair-tile-cadence-save"
        disabled={saving}
        onClick={() => void save()}
      >
        {saving ? '…' : 'Save'}
      </button>
      <button
        type="button"
        className="repair-tile-cadence-cancel"
        onClick={() => setEditing(false)}
      >
        Cancel
      </button>
    </span>
  );
}

// ── Job settings drawer ──────────────────────────────────────────────────────

function JobSettings({
  job,
  onSaved,
  open,
}: {
  job: RepairJob;
  onSaved: () => void;
  open: boolean;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() => ({ ...job.settings }));

  const save = useCallback(async () => {
    const settings: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(values)) {
      // Section sentinels are display-only; they are not settings and the
      // vanilla never collects them off the DOM because they render as a div.
      if (key.startsWith('_section_')) continue;
      settings[key] = typeof value === 'string' ? coerceRepairSettingValue(value) : value;
    }
    try {
      // The interval moved to the tile face, so this drawer no longer owns
      // it — it sends the job's current value back untouched.
      await saveRepairJobSettings(job.job_id, job.interval_hours, settings);
      toast('Settings saved', 'success');
      onSaved();
    } catch {
      toast('Error saving settings', 'error');
    }
  }, [job.interval_hours, job.job_id, onSaved, values]);

  return (
    <div
      className="repair-job-settings"
      id={`repair-settings-${job.job_id}`}
      style={{ display: open ? '' : 'none' }}
    >
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
  );
}

// ── The tile ─────────────────────────────────────────────────────────────────

export interface OperationTileProps {
  job: RepairJob;
  progress?: RepairJobProgress;
  runs: readonly RepairJobRun[];
  onChanged: () => void;
  onHelp: (job: RepairJob) => void;
  /** The findings badge leads somewhere: this job's open findings. */
  onShowFindings: (jobId: string) => void;
}

export function OperationTile({
  job,
  progress,
  runs,
  onChanged,
  onHelp,
  onShowFindings,
}: OperationTileProps) {
  const [enabled, setEnabled] = useState(job.enabled);
  const [stopping, setStopping] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  useEffect(() => setEnabled(job.enabled), [job.enabled]);

  const badge = repairJobBadge(job);
  const meta = repairJobMeta(job);
  const schedule = jobSchedule(job);
  const hasSettings = Object.keys(job.settings || {}).length > 0;
  const dryRun = isRepairJobDryRun(job);
  const trend = useMemo(() => jobTrend(runs, job.job_id), [job.job_id, runs]);
  const percent = job.is_running ? Math.max(2, Math.min(100, progress?.progress || 0)) : 0;

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
    <div
      className={`repair-job-card repair-tile ${repairJobCardClass(job)} sched-${schedule.state}${
        enabled ? '' : ' off'
      }`.trim()}
      data-job-id={job.job_id}
    >
      {/* The glow edge IS the progress bar. */}
      <span
        className={`repair-tile-edge${job.is_running ? ' running' : ''}`}
        style={job.is_running ? { width: `${percent}%` } : undefined}
        aria-hidden="true"
      />

      <div className="repair-tile-head">
        <span className={`repair-job-status ${repairJobDot(job)}`} />
        <span className="repair-tile-name">{job.display_name}</span>
        {badge.kind !== 'none' ? (
          <button
            type="button"
            className={`repair-tile-badge${badge.kind === 'pending' ? '' : ' historical'}`}
            title={
              badge.kind === 'pending'
                ? 'Show these findings'
                : 'Findings this job raised on its last scan'
            }
            onClick={() => onShowFindings(job.job_id)}
          >
            {badge.count.toLocaleString()}
            {badge.kind === 'pending' ? ' open' : ' last scan'}
          </button>
        ) : null}
      </div>

      <div className="repair-tile-desc">{job.description || ''}</div>

      <div className="repair-tile-schedule">
        <CadenceEditor job={job} onSaved={onChanged} />
        <span className={`repair-tile-next ${schedule.state}`}>{schedule.label}</span>
        {job.auto_fix ? (
          <span className={`repair-flow-badge ${dryRun ? 'dryrun' : 'autofix'}`}>
            {dryRun ? 'Dry Run' : 'Auto-fix'}
          </span>
        ) : null}
      </div>

      <div className="repair-tile-foot">
        {trend.length > 1 ? (
          // A chart with no legend is decoration. It says what it is, in the
          // same words the health hero's trend uses.
          <span
            className="repair-tile-trend"
            title={`Findings raised per run — this job's last ${trend.length} runs, oldest to newest (${trend.join(', ')})`}
          >
            <svg
              className="repair-tile-spark"
              width="64"
              height="16"
              viewBox="0 0 64 16"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <polyline
                points={sparklinePoints(trend, 64, 16)}
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
            </svg>
            <span className="repair-tile-trend-label">findings / run</span>
          </span>
        ) : (
          <span className="repair-tile-meta">{meta.join(' · ')}</span>
        )}

        <div className="repair-job-actions">
          <label
            className="repair-job-toggle"
            title={enabled ? 'Disable this job' : 'Enable this job'}
          >
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
          {hasSettings ? (
            <button
              className="repair-settings-btn"
              type="button"
              title="Settings"
              onClick={(event) => {
                event.stopPropagation();
                setSettingsOpen((previous) => !previous);
              }}
            >
              &#9881;
            </button>
          ) : null}
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
      {hasSettings ? <JobSettings job={job} onSaved={onChanged} open={settingsOpen} /> : null}
    </div>
  );
}

// ── The containers ───────────────────────────────────────────────────────────

export interface OperationsProps {
  jobs: RepairJob[] | null;
  error: boolean;
  progress: Record<string, RepairJobProgress>;
  runs: RepairJobRun[];
  onChanged: () => void;
  onHelp: (job: RepairJob) => void;
  onShowFindings: (jobId: string) => void;
}

export function Operations({
  jobs,
  error,
  progress,
  runs,
  onChanged,
  onHelp,
  onShowFindings,
}: OperationsProps) {
  const families = useMemo(() => jobFamilies(jobs || []), [jobs]);
  /** Collapsed families, by name. Everything starts open — a page that hides
   *  its own contents on arrival is the tab problem again, one level down. */
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());

  if (error) return <div className="repair-empty">Error loading jobs</div>;
  if (jobs === null) return <div className="repair-loading">Loading jobs...</div>;
  if (jobs.length === 0) {
    return (
      <div className="repair-empty-state">
        <div className="repair-empty-icon">🔧</div>
        <div className="repair-empty-title">No Maintenance Jobs</div>
        <div className="repair-empty-text">
          Library maintenance jobs will appear here once available.
        </div>
      </div>
    );
  }

  return (
    <div className="repair-families">
      {families.map((family) => {
        const isOpen = !collapsed.has(family.category);
        return (
          <section
            className={`repair-family${isOpen ? '' : ' collapsed'}`}
            style={{ ['--tile-glow' as string]: family.glow }}
            data-category={family.category}
            key={family.category}
          >
            <button
              type="button"
              className="repair-family-head"
              aria-expanded={isOpen}
              onClick={() =>
                setCollapsed((current) => {
                  const next = new Set(current);
                  if (next.has(family.category)) next.delete(family.category);
                  else next.add(family.category);
                  return next;
                })
              }
            >
              <span className="repair-family-title">{family.category}</span>
              <span className="repair-family-summary">{familySummary(family)}</span>
              {family.pending > 0 ? (
                <span className="repair-family-pending">
                  {family.pending.toLocaleString()} open
                </span>
              ) : null}
              <span className={`repair-family-chevron${isOpen ? ' open' : ''}`} aria-hidden="true">
                &#9660;
              </span>
            </button>
            <div className="repair-family-blurb">{family.blurb}</div>
            {isOpen ? (
              <div className="repair-family-grid">
                {family.jobs.map((job) => (
                  <OperationTile
                    job={job}
                    progress={progress[job.job_id]}
                    runs={runs}
                    onChanged={onChanged}
                    onHelp={onHelp}
                    onShowFindings={onShowFindings}
                    key={job.job_id}
                  />
                ))}
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
