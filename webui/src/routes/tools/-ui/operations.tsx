/**
 * Every maintenance job, as a row, grouped into the families the backend
 * serves. A row carries what you want to know about a job on its face: is it
 * on, how often it runs (click to change), when it last ran, how many
 * findings it has open, and the three things you do to it (switch, run, set).
 *
 * Two contracts that outlive the redesigns:
 *
 * 1. `.repair-job-card[data-job-id]` is the stable per-job hook the socket
 *    dispatch and the tests key on. It stays on the row root.
 * 2. The class on the card is NOT the class on the status dot. An idle
 *    enabled job gets dot 'enabled' and card class '' (see
 *    repairJobCardClass).
 *
 * The thin line along the top of a row is its progress bar: it fills left to
 * right while that job scans, so nothing appears and shoves the list around.
 */

import { Link } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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
  repairSettingText,
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
import { updateAutomationTrigger } from '../../automations/-automations.api';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

/**
 * True when a dry_run value — boolean or one of its text forms from a
 * dropdown — means the run will go live.
 */
function isDryRunOff(value: unknown): boolean {
  if (typeof value === 'boolean') return !value;
  if (typeof value === 'string') return value.toLowerCase() === 'false';
  return false;
}

// ── Cadence editor ───────────────────────────────────────────────────────────

const UNITS: IntervalUnit[] = ['hours', 'days', 'weeks'];

/**
 * The schedule control, on the tile face.
 *
 * It speaks the same language as the auto-sync page — an interval and a unit —
 * rather than the raw hours the config stores.
 *
 * #1289 item 12: the interval now writes to the job's system automation row
 * (via the automations API), not the legacy repair config. The "custom
 * schedule" link deep-links to the automation builder for daily/weekly/
 * monthly triggers.
 */
function CadenceEditor({ job, onSaved }: { job: RepairJob; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Cadence>(() =>
    // Custom non-interval triggers start the editor at 24h; saving resets to a schedule trigger.
    cadenceFromHours(job.interval_hours ?? 24),
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(cadenceFromHours(job.interval_hours));
  }, [editing, job.interval_hours]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const hours = hoursFromCadence(draft);
      if (job.automation_id) {
        // Write to the automation's schedule trigger. The trigger_type is
        // reset to 'schedule' in case the user had set a daily/weekly/monthly
        // schedule via the Custom schedule link.
        await updateAutomationTrigger(
          job.automation_id,
          {
            interval: hours,
            unit: 'hours',
          },
          'schedule',
        );
      } else {
        // Fallback: legacy path (migration hasn't seeded the row yet).
        await saveRepairJobSettings(job.job_id, hours, { ...job.settings });
      }
      toast(`${job.display_name} now runs ${cadenceLabel(hours)}`, 'success');
      setEditing(false);
      onSaved();
    } catch {
      toast('Could not save the schedule', 'error');
    } finally {
      setSaving(false);
    }
  }, [draft, job.automation_id, job.display_name, job.job_id, job.settings, onSaved]);

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
      {job.automation_id && (
        <Link
          to="/automations"
          className="repair-tile-cadence-custom"
          title="Set a daily, weekly, or monthly schedule"
        >
          Custom schedule
        </Link>
      )}
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

  /**
   * Boulder: flipping dry run OFF must warn, at the moment of the flip — with
   * dry run off the job directly manages the library on its next scheduled
   * run instead of reporting findings for review. Jobs flagged
   * `writes_library_files` move/retag/rewrite real files, so they get the
   * strongest wording; finding-only jobs get a lighter note. No dialog host
   * (or a cancelled dialog) fails closed: dry run stays on.
   */
  const confirmDryRunOff = useCallback(async (): Promise<boolean> => {
    const writesFiles = job.writes_library_files === true;
    return Boolean(
      await window.showConfirmDialog?.({
        title: 'Turn Off Dry Run?',
        message: writesFiles
          ? 'This job will now directly manage your library — it can move, retag, or rewrite files without asking first. Continue?'
          : 'This job will now apply its fixes automatically instead of only reporting findings for your review. Continue?',
        confirmText: 'Turn off dry run',
        cancelText: 'Keep dry run',
        destructive: true,
      }),
    );
  }, [job.writes_library_files]);

  // Tracks whether a dry-run-off confirm dialog is currently open. While it
  // is, the dry_run control is disabled and any stale dialog resolution is
  // ignored, so a rapid double-toggle can't defeat the guard (#1289 item 10).
  const confirmInFlight = useRef(false);
  const [confirmPending, setConfirmPending] = useState(false);

  /**
   * A setting change routed through the dry-run guard when it would turn dry
   * run off. Everything else applies to the draft immediately — it still needs
   * Save Settings to reach the server. When the guard is declined, the draft
   * never changed but the browser already flipped the control, so `revert`
   * puts the control back on the value the draft still holds.
   */
  const handleSettingChange = useCallback(
    async (key: string, next: unknown, revert: () => void) => {
      if (key === 'dry_run' && isDryRunOff(next) && !isDryRunOff(values[key])) {
        // Ignore stale input while a confirm is already pending.
        if (confirmInFlight.current) {
          revert();
          return;
        }
        confirmInFlight.current = true;
        setConfirmPending(true);
        let confirmed = false;
        try {
          confirmed = await confirmDryRunOff();
        } finally {
          confirmInFlight.current = false;
          setConfirmPending(false);
        }
        if (!confirmed) {
          revert();
          return;
        }
      }
      setValues((previous) => ({ ...previous, [key]: next }));
    },
    [confirmDryRunOff, values],
  );

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
                value={repairSettingText(current)}
                disabled={key === 'dry_run' && confirmPending}
                onChange={(event) => {
                  const target = event.target;
                  void handleSettingChange(key, target.value, () => {
                    target.value = repairSettingText(values[key]);
                  });
                }}
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
                disabled={key === 'dry_run' && confirmPending}
                onChange={(event) => {
                  const target = event.target;
                  void handleSettingChange(key, target.checked, () => {
                    target.checked = Boolean(values[key]);
                  });
                }}
              />
            ) : field.kind === 'number' ? (
              <input
                type="number"
                className="repair-setting-input"
                data-job={job.job_id}
                data-key={key}
                value={repairSettingText(current)}
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
                value={repairSettingText(current)}
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
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-hidden="true"
              >
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              className="repair-run-btn"
              type="button"
              title="Run now"
              onClick={() => void onRun()}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M7 4.5v15l13-7.5z" />
              </svg>
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
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
                <circle cx="16" cy="7" r="2" />
                <circle cx="8" cy="17" r="2" />
              </svg>
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
  onShowFindings: (jobId: string, options?: { severity?: string; findingType?: string }) => void;
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
        <div className="repair-empty-title">No Maintenance Jobs</div>
        <div className="repair-empty-text">
          Library maintenance jobs will appear here once available.
        </div>
      </div>
    );
  }

  return (
    <div className="operations-wrapper">
      <div className="repair-families">
        {families.map((family) => {
          const isOpen = !collapsed.has(family.category);
          return (
            <section
              className={`repair-family${isOpen ? '' : ' collapsed'}`}
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
                <svg
                  className={`repair-family-chevron${isOpen ? ' open' : ''}`}
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M6 9l6 6 6-6" />
                </svg>
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
    </div>
  );
}
