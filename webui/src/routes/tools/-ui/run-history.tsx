/**
 * History: what the jobs have been doing.
 *
 * It used to be a list of runs and nothing else, so the only way to learn
 * anything was to read it. The page now answers first and lists second:
 *
 * 1. What it all adds up to: runs, failures, what was found and fixed, and
 *    the time spent, over the runs on hand.
 * 2. Day by day: one bar per day for two weeks, failures in their own colour,
 *    so a bad night or a job that stopped running stands out.
 * 3. The runs themselves, as a table with the same columns on every row
 *    (when, how long, checked, found, fixed), and a failed run's reason right
 *    on its row instead of "failed after —".
 * 4. Or by job: each job's last result, how often it runs and fails, how long
 *    it takes and what it has turned up.
 *
 * Kept from the version before: every timestamp goes through
 * `parseDbTimestamp` (the server writes UTC), day headings, one chip per job,
 * a failures-only toggle, and an expansion with the full error and a way
 * into the findings a job raised.
 */

import { useMemo, useState } from 'react';

import type { RepairJobRun } from '../-tools.types';

import { formatCacheAge } from '../-tools.core';
import {
  filterRuns,
  formatDuration,
  formatSpan,
  groupRunsByDay,
  runClock,
  runCounters,
  runDayBars,
  runFullStamp,
  runJobFilters,
  runJobSummaries,
  runOutcome,
  runReason,
  runTotals,
  type RunOutcome,
} from '../-tools.runs';

export interface RunHistoryProps {
  runs: RepairJobRun[] | null;
  error: boolean;
  /** Jump to the findings this job has open. */
  onShowFindings: (
    jobId: string,
    labelOrOptions?: string | { severity?: string; findingType?: string },
  ) => void;
  /** Re-read the history — a run that just finished is not in this list yet. */
  onRefresh: () => void;
  /** job id to its display name, from the job list. a run recorded before
   *  the worker knew its jobs carries the bare id as its name. */
  jobNames?: Readonly<Record<string, string>>;
}

/** an outcome as a small mark: a tick, a cross, a dot. drawn, not typed. */
function OutcomeMark({ outcome }: { outcome: RunOutcome }) {
  const common = {
    width: 14,
    height: 14,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2.6,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };
  return (
    <span className={`repair-run-glyph ${outcome}`} title={OUTCOME_WORDS[outcome]}>
      {outcome === 'failed' ? (
        <svg {...common}>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      ) : outcome === 'stopped' ? (
        <svg {...common}>
          <path d="M12 6v8M12 18v.5" />
        </svg>
      ) : outcome === 'running' ? (
        <svg {...common} fill="currentColor" stroke="none">
          <path d="M8 5v14l11-7z" />
        </svg>
      ) : outcome === 'quiet' ? (
        <svg {...common}>
          <path d="M7 12h10" />
        </svg>
      ) : (
        <svg {...common}>
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
      )}
    </span>
  );
}

const OUTCOME_WORDS: Record<RunOutcome, string> = {
  success: 'Found or fixed something',
  quiet: 'Nothing to do',
  failed: 'Failed',
  stopped: 'Stopped early',
  running: 'Running',
};

const count = (value: number | null | undefined) => (value ? value.toLocaleString() : '–');

export function RunHistory({ runs, error, onShowFindings, onRefresh, jobNames }: RunHistoryProps) {
  const [jobId, setJobId] = useState('');
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [openRun, setOpenRun] = useState<string>('');
  const [view, setView] = useState<'runs' | 'jobs'>('runs');

  const all = useMemo(
    () =>
      (runs || []).map((run) => {
        const known = run.job_id ? jobNames?.[run.job_id] : undefined;
        const unnamed = !run.display_name || run.display_name === run.job_id;
        return known && unnamed ? { ...run, display_name: known } : run;
      }),
    [jobNames, runs],
  );
  const chips = useMemo(() => runJobFilters(all), [all]);
  const shown = useMemo(() => filterRuns(all, { jobId, failuresOnly }), [all, failuresOnly, jobId]);
  const days = useMemo(() => groupRunsByDay(shown), [shown]);
  const totals = useMemo(() => runTotals(shown), [shown]);
  const bars = useMemo(() => runDayBars(shown), [shown]);
  const jobs = useMemo(() => runJobSummaries(shown), [shown]);
  const failureCount = useMemo(
    () => all.filter((run) => runOutcome(run) === 'failed').length,
    [all],
  );

  if (error) return <div className="repair-empty">Error loading history</div>;
  if (runs === null) return <div className="repair-loading">Loading history...</div>;

  const tallest = Math.max(1, ...bars.map((bar) => bar.ok + bar.failed));
  const since = totals.since ? formatCacheAge(totals.since) : '';

  return (
    <div className="repair-runs">
      {all.length ? (
        <div className="repair-runs-overview">
          <div className="repair-runs-figures">
            <div className="repair-runs-figure">
              <span className="repair-runs-figure-value">{totals.runs.toLocaleString()}</span>
              <span className="repair-runs-figure-label">
                run{totals.runs === 1 ? '' : 's'}
                {since && since !== 'now' && since !== '—' ? ` in the last ${since}` : ''}
              </span>
            </div>
            <div className={`repair-runs-figure${totals.failed ? ' bad' : ''}`}>
              <span className="repair-runs-figure-value">{totals.failed.toLocaleString()}</span>
              <span className="repair-runs-figure-label">failed</span>
            </div>
            <div className="repair-runs-figure">
              <span className="repair-runs-figure-value">{totals.found.toLocaleString()}</span>
              <span className="repair-runs-figure-label">findings raised</span>
            </div>
            <div className="repair-runs-figure">
              <span className="repair-runs-figure-value">{totals.fixed.toLocaleString()}</span>
              <span className="repair-runs-figure-label">fixed automatically</span>
            </div>
            <div className="repair-runs-figure">
              <span className="repair-runs-figure-value">{formatSpan(totals.seconds)}</span>
              <span className="repair-runs-figure-label">spent working</span>
            </div>
          </div>

          <div className="repair-runs-days" role="img" aria-label="Runs per day, last 14 days">
            {bars.map((bar) => {
              const total = bar.ok + bar.failed;
              return (
                <div
                  className="repair-runs-daybar"
                  key={bar.key}
                  title={`${bar.label}: ${total} run${total === 1 ? '' : 's'}${
                    bar.failed ? `, ${bar.failed} failed` : ''
                  }`}
                >
                  <span className="repair-runs-daybar-stack">
                    {bar.failed ? (
                      <span
                        className="repair-runs-daybar-fill failed"
                        style={{ height: `${(bar.failed / tallest) * 100}%` }}
                      />
                    ) : null}
                    {bar.ok ? (
                      <span
                        className="repair-runs-daybar-fill"
                        style={{ height: `${(bar.ok / tallest) * 100}%` }}
                      />
                    ) : null}
                  </span>
                  <span className="repair-runs-daybar-label">{bar.label.replace(/^\D+/, '')}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="repair-runs-toolbar">
        <div className="repair-runs-chips">
          <button
            type="button"
            className={`repair-runs-chip${jobId === '' ? ' active' : ''}`}
            onClick={() => setJobId('')}
          >
            All jobs
            <span className="repair-runs-chip-count">{all.length}</span>
          </button>
          {chips.map((chip) => (
            <button
              type="button"
              key={chip.jobId}
              className={`repair-runs-chip${jobId === chip.jobId ? ' active' : ''}${
                chip.hasFailure ? ' has-failure' : ''
              }`}
              onClick={() => setJobId((current) => (current === chip.jobId ? '' : chip.jobId))}
              title={chip.hasFailure ? `${chip.label} — has a failed run` : chip.label}
            >
              {chip.label}
              <span className="repair-runs-chip-count">{chip.count}</span>
            </button>
          ))}
        </div>
        <div className="repair-runs-tools">
          {failureCount > 0 ? (
            <button
              type="button"
              className={`repair-runs-toggle${failuresOnly ? ' active' : ''}`}
              onClick={() => setFailuresOnly((current) => !current)}
              title="Show only runs that failed"
            >
              Failures only
              <span className="repair-runs-chip-count">{failureCount}</span>
            </button>
          ) : null}
          <div className="repair-view-switch" role="group" aria-label="Show history as">
            <button type="button" aria-pressed={view === 'runs'} onClick={() => setView('runs')}>
              Runs
            </button>
            <button type="button" aria-pressed={view === 'jobs'} onClick={() => setView('jobs')}>
              By job
            </button>
          </div>
          <button
            type="button"
            className="repair-runs-refresh"
            onClick={onRefresh}
            title="Reload the run history"
            aria-label="Reload the run history"
          >
            <svg
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
              <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />
            </svg>
          </button>
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="repair-empty-state">
          <div className="repair-empty-title">
            {all.length === 0 ? 'No runs yet' : 'Nothing matches'}
          </div>
          <div className="repair-empty-text">
            {all.length === 0
              ? 'Maintenance jobs record every run here once they have scanned for the first time.'
              : 'No runs match these filters.'}
          </div>
        </div>
      ) : view === 'jobs' ? (
        <div className="repair-runs-table jobs" role="table" aria-label="History by job">
          <div className="repair-runs-cols" role="row">
            <span role="columnheader">Job</span>
            <span role="columnheader">Last run</span>
            <span role="columnheader">Runs</span>
            <span role="columnheader">Failed</span>
            <span role="columnheader">Usually takes</span>
            <span role="columnheader">Found</span>
            <span role="columnheader">Fixed</span>
          </div>
          {jobs.map((job) => (
            <button
              type="button"
              className={`repair-runs-job ${job.lastOutcome}`}
              role="row"
              key={job.jobId}
              title={`Show only ${job.label}'s runs`}
              onClick={() => {
                setJobId(job.jobId);
                setView('runs');
              }}
            >
              <span className="repair-run-name" role="cell">
                <OutcomeMark outcome={job.lastOutcome} />
                {job.label}
              </span>
              <span role="cell">
                {job.lastStarted ? `${formatCacheAge(job.lastStarted)} ago` : '–'}
              </span>
              <span role="cell">{job.runs.toLocaleString()}</span>
              <span role="cell" className={job.failed ? 'bad' : ''}>
                {count(job.failed)}
              </span>
              <span role="cell">{formatDuration(job.averageSeconds)}</span>
              <span role="cell">{count(job.found)}</span>
              <span role="cell">{count(job.fixed)}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="repair-runs-table" role="table" aria-label="Runs">
          <div className="repair-runs-cols" role="row">
            <span role="columnheader">Job</span>
            <span role="columnheader">Started</span>
            <span role="columnheader">Took</span>
            <span role="columnheader">Checked</span>
            <span role="columnheader">Found</span>
            <span role="columnheader">Fixed</span>
            <span aria-hidden="true" />
          </div>
          {days.map((day) => {
            const failed = day.runs.filter((run) => runOutcome(run) === 'failed').length;
            return (
              <div className="repair-runs-day" key={day.key}>
                <div className="repair-runs-day-head">
                  <span className="repair-runs-day-label">{day.label}</span>
                  <span className="repair-runs-day-count">
                    {day.runs.length} run{day.runs.length === 1 ? '' : 's'}
                    {failed ? ` · ${failed} failed` : ''}
                  </span>
                </div>
                {day.runs.map((run, index) => (
                  <RunRow
                    run={run}
                    key={run.id ?? `${run.job_id}-${run.started_at}-${index}`}
                    rowKey={String(run.id ?? `${run.job_id}-${run.started_at}-${index}`)}
                    open={openRun === String(run.id ?? `${run.job_id}-${run.started_at}-${index}`)}
                    onToggle={setOpenRun}
                    onShowFindings={onShowFindings}
                  />
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function RunRow({
  run,
  rowKey,
  open,
  onToggle,
  onShowFindings,
}: {
  run: RepairJobRun;
  rowKey: string;
  open: boolean;
  onToggle: (key: string) => void;
  onShowFindings: (
    jobId: string,
    labelOrOptions?: string | { severity?: string; findingType?: string },
  ) => void;
}) {
  const outcome = runOutcome(run);
  const label = run.display_name || (run.job_id || '').replace(/_/g, ' ');
  const toggle = () => onToggle(open ? '' : rowKey);
  const reason = runReason(run);

  return (
    <div className={`repair-run ${outcome}${open ? ' open' : ''}`} data-job-id={run.job_id}>
      <div
        className="repair-run-head"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggle();
          }
        }}
      >
        <span className="repair-run-name">
          <OutcomeMark outcome={outcome} />
          <span className="repair-run-name-text">
            {label}
            {/* why it failed, on the row: "failed" alone sent you clicking */}
            {reason && !open ? <span className="repair-run-reason">{reason}</span> : null}
          </span>
        </span>
        <span className="repair-run-clock" title={runFullStamp(run.started_at)}>
          {runClock(run.started_at)}
          <span className="repair-run-age">{formatCacheAge(run.started_at)} ago</span>
        </span>
        <span className="repair-run-cell">
          {outcome === 'running' ? 'running' : formatDuration(run.duration_seconds)}
        </span>
        <span className="repair-run-cell">{count(run.items_scanned)}</span>
        <span className={`repair-run-cell${run.findings_created ? ' strong' : ''}`}>
          {count(run.findings_created)}
        </span>
        <span className={`repair-run-cell${run.auto_fixed ? ' strong' : ''}`}>
          {count(run.auto_fixed)}
        </span>
        <svg
          className={`repair-run-chevron${open ? ' open' : ''}`}
          width="14"
          height="14"
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
      </div>

      {open ? (
        <div className="repair-run-detail">
          <div className="repair-run-counters">
            {runCounters(run).map((counter) => (
              <div className="repair-run-counter" key={counter.label}>
                <span className="repair-run-counter-value">{counter.value}</span>
                <span className="repair-run-counter-label">{counter.label}</span>
              </div>
            ))}
          </div>

          {run.error_text ? (
            <div className="repair-run-error">
              <div className="repair-run-error-title">
                {outcome === 'stopped' ? 'Why it stopped early' : 'Why it failed'}
              </div>
              <pre className="repair-run-error-text">{run.error_text}</pre>
            </div>
          ) : outcome === 'failed' ? (
            <div className="repair-run-error">
              <div className="repair-run-error-title">Why it failed</div>
              {/* Runs recorded before phase 1 have no reason stored, and
                  saying so beats an empty panel that looks broken. */}
              <div className="repair-run-error-text muted">
                No reason was recorded for this run. Newer runs capture the error.
              </div>
            </div>
          ) : null}

          <div className="repair-run-foot">
            <span className="repair-run-stamps">
              {runFullStamp(run.started_at)}
              {run.finished_at ? ` → ${runFullStamp(run.finished_at)}` : ' → still running'}
            </span>
            {run.job_id ? (
              <button
                type="button"
                className="repair-run-link"
                onClick={() => onShowFindings(run.job_id as string, label)}
              >
                See this job&rsquo;s findings &rsaquo;
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
