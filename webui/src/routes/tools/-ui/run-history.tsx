/**
 * Recent runs — its own card, not a tail of rows under the job list.
 *
 * What was wrong with the old one, in order of how much it hurt:
 *
 * 1. Every timestamp was read as local when the server writes UTC, so a run
 *    from this morning claimed to be minutes old and everything said "now".
 *    Fixed at the source in `parseDbTimestamp`.
 * 2. It never said WHY a run failed, though the reason has been recorded
 *    since phase 1.
 * 3. Fifty runs of thirty jobs interleaved with no filter, and every row
 *    carrying three renderings of the same instant.
 *
 * So: day headings for rhythm, one chip per job, a failures-only toggle, one
 * sentence per run about what it accomplished, and an expansion carrying the
 * counters, the error and a way into the findings it raised.
 */

import { useMemo, useState } from 'react';

import type { RepairJobRun } from '../-tools.types';

import { formatCacheAge } from '../-tools.core';
import {
  filterRuns,
  groupRunsByDay,
  RUN_OUTCOME_ICONS,
  runClock,
  runCounters,
  runFullStamp,
  runJobFilters,
  runOutcome,
  runSummary,
} from '../-tools.runs';

export interface RunHistoryProps {
  runs: RepairJobRun[] | null;
  error: boolean;
  /** Jump to the findings this job has open. */
  onShowFindings: (jobId: string, label: string) => void;
  /** Re-read the history — a run that just finished is not in this list yet. */
  onRefresh: () => void;
}

export function RunHistory({ runs, error, onShowFindings, onRefresh }: RunHistoryProps) {
  const [jobId, setJobId] = useState('');
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [openRun, setOpenRun] = useState<string>('');

  const all = runs || [];
  const chips = useMemo(() => runJobFilters(all), [all]);
  const shown = useMemo(() => filterRuns(all, { jobId, failuresOnly }), [all, failuresOnly, jobId]);
  const days = useMemo(() => groupRunsByDay(shown), [shown]);
  const failureCount = useMemo(
    () => all.filter((run) => runOutcome(run) === 'failed').length,
    [all],
  );

  if (error) return <div className="repair-empty">Error loading history</div>;
  if (runs === null) return <div className="repair-loading">Loading history...</div>;

  return (
    <div className="repair-runs">
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
          <button
            type="button"
            className="repair-runs-refresh"
            onClick={onRefresh}
            title="Reload the run history"
          >
            &#8635;
          </button>
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="repair-empty-state">
          <div className="repair-empty-icon">&#128337;</div>
          <div className="repair-empty-title">
            {all.length === 0 ? 'No runs yet' : 'Nothing matches'}
          </div>
          <div className="repair-empty-text">
            {all.length === 0
              ? 'Maintenance jobs record every run here once they have scanned for the first time.'
              : 'No runs match these filters.'}
          </div>
        </div>
      ) : (
        days.map((day) => (
          <div className="repair-runs-day" key={day.key}>
            <div className="repair-runs-day-head">
              <span className="repair-runs-day-label">{day.label}</span>
              <span className="repair-runs-day-count">
                {day.runs.length} run{day.runs.length === 1 ? '' : 's'}
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
        ))
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
  onShowFindings: (jobId: string, label: string) => void;
}) {
  const outcome = runOutcome(run);
  const label = run.display_name || (run.job_id || '').replace(/_/g, ' ');
  const toggle = () => onToggle(open ? '' : rowKey);

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
        <span className={`repair-run-glyph ${outcome}`} aria-hidden="true">
          {RUN_OUTCOME_ICONS[outcome]}
        </span>
        <span className="repair-run-clock">{runClock(run.started_at)}</span>
        <span className="repair-run-name">{label}</span>
        <span className="repair-run-summary">{runSummary(run)}</span>
        <span className="repair-run-age">{formatCacheAge(run.started_at)}</span>
        <span className={`repair-run-chevron${open ? ' open' : ''}`} aria-hidden="true">
          &#9660;
        </span>
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
              <div className="repair-run-error-title">Why it failed</div>
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
