/**
 * Run history, as arithmetic.
 *
 * The old history was a log: fifty runs of thirty jobs interleaved, each row
 * carrying a status word, a duration, and then three separate renderings of
 * the same instant — "3 hours ago · 8/3 9:00:00 AM → 8/3 9:01:00 AM". It told
 * you a run happened and nothing about whether that was good news.
 *
 * A run row should answer one question — what did this accomplish — and a
 * failed one should say why. Everything needed to do that lives here so it
 * can be pinned by tests.
 */

import type { RepairJobRun } from './-tools.types';

import { parseDbTimestamp } from './-tools.core';

/** What the row's glyph and colour say. */
export type RunOutcome = 'success' | 'quiet' | 'failed' | 'running';

/**
 * `quiet` is its own outcome on purpose: a scan that found nothing is the
 * single most common run and the best possible news, and giving it the same
 * green tick as a scan that found four hundred problems made the whole list
 * read as undifferentiated noise.
 */
export function runOutcome(run: RepairJobRun): RunOutcome {
  const status = (run.status || '').toLowerCase();
  if (status === 'failed' || status === 'error' || (run.errors || 0) > 0) return 'failed';
  if (status === 'running' || !run.finished_at) return 'running';
  if ((run.findings_created || 0) === 0 && (run.auto_fixed || 0) === 0) return 'quiet';
  return 'success';
}

export const RUN_OUTCOME_ICONS: Record<RunOutcome, string> = {
  success: '✓', // ✓
  quiet: '·', // ·
  failed: '✗', // ✗
  running: '▶', // ▶
};

export function formatDuration(seconds: number | null | undefined): string {
  const value = seconds || 0;
  if (value <= 0) return '—';
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(value % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/**
 * The one line a run row shows. It is a sentence about what happened, not a
 * dump of every counter — the counters are in the expanded panel.
 */
export function runSummary(run: RepairJobRun): string {
  const outcome = runOutcome(run);
  const scanned = run.items_scanned || 0;
  const found = run.findings_created || 0;
  const fixed = run.auto_fixed || 0;

  if (outcome === 'running') {
    return scanned > 0 ? `running · ${scanned.toLocaleString()} scanned so far` : 'running';
  }
  if (outcome === 'failed') {
    const errors = run.errors || 0;
    const suffix = errors > 1 ? ` · ${errors} errors` : '';
    return `failed after ${formatDuration(run.duration_seconds)}${suffix}`;
  }
  if (outcome === 'quiet') {
    return scanned > 0
      ? `nothing to fix · ${scanned.toLocaleString()} checked · ${formatDuration(run.duration_seconds)}`
      : `nothing to do · ${formatDuration(run.duration_seconds)}`;
  }

  const parts: string[] = [];
  if (scanned > 0) parts.push(`${scanned.toLocaleString()} checked`);
  if (found > 0) parts.push(`${found.toLocaleString()} found`);
  if (fixed > 0) parts.push(`${fixed.toLocaleString()} fixed automatically`);
  parts.push(formatDuration(run.duration_seconds));
  return parts.join(' · ');
}

/** Wall-clock start, in the viewer's timezone. Empty when unparseable rather
 *  than "Invalid Date". */
export function runClock(startedAt: string | null | undefined): string {
  if (!startedAt) return '';
  const stamp = parseDbTimestamp(startedAt);
  if (Number.isNaN(stamp)) return '';
  return new Date(stamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function runFullStamp(value: string | null | undefined): string {
  if (!value) return '';
  const stamp = parseDbTimestamp(value);
  if (Number.isNaN(stamp)) return '';
  return new Date(stamp).toLocaleString();
}

// ── Grouping ─────────────────────────────────────────────────────────────────

export interface RunDay {
  /** `YYYY-MM-DD` in the viewer's timezone — the grouping key. */
  key: string;
  label: string;
  runs: RepairJobRun[];
}

function dayKey(stamp: number): string {
  const date = new Date(stamp);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * Runs under day headings, newest day first, order preserved within a day.
 *
 * Days are what give a log a shape: "nine runs today, four yesterday, none
 * since" is a rhythm you can read at a glance, and it is invisible in a flat
 * list of fifty rows.
 *
 * Runs with no parseable start go into a trailing "Unknown" group instead of
 * being dropped — a run that happened is a fact, even if its clock is not.
 */
export function groupRunsByDay(runs: readonly RepairJobRun[], now: number = Date.now()): RunDay[] {
  const today = dayKey(now);
  const yesterday = dayKey(now - 86400000);
  const days: RunDay[] = [];
  const index = new Map<string, RunDay>();

  for (const run of runs) {
    const stamp = run.started_at ? parseDbTimestamp(run.started_at) : Number.NaN;
    const key = Number.isNaN(stamp) ? 'unknown' : dayKey(stamp);
    let bucket = index.get(key);
    if (!bucket) {
      bucket = { key, label: dayLabel(key, stamp, today, yesterday), runs: [] };
      index.set(key, bucket);
      days.push(bucket);
    }
    bucket.runs.push(run);
  }

  // 'unknown' sorts last whatever its key would compare as.
  return days.sort((a, b) => {
    if (a.key === 'unknown') return 1;
    if (b.key === 'unknown') return -1;
    return b.key.localeCompare(a.key);
  });
}

function dayLabel(key: string, stamp: number, today: string, yesterday: string): string {
  if (key === 'unknown') return 'Undated';
  if (key === today) return 'Today';
  if (key === yesterday) return 'Yesterday';
  return new Date(stamp).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// ── Filters ──────────────────────────────────────────────────────────────────

export interface RunJobFilter {
  jobId: string;
  label: string;
  count: number;
  /** True when any of this job's runs in the window failed — the chip says so
   *  before you click it. */
  hasFailure: boolean;
}

/** One chip per job present in the window, busiest first. */
export function runJobFilters(runs: readonly RepairJobRun[]): RunJobFilter[] {
  const byJob = new Map<string, RunJobFilter>();
  for (const run of runs) {
    const jobId = run.job_id || '';
    if (!jobId) continue;
    const entry = byJob.get(jobId) || {
      jobId,
      label: run.display_name || jobId.replace(/_/g, ' '),
      count: 0,
      hasFailure: false,
    };
    entry.count += 1;
    if (runOutcome(run) === 'failed') entry.hasFailure = true;
    byJob.set(jobId, entry);
  }
  return [...byJob.values()].sort(
    (a, b) => b.count - a.count || a.label.localeCompare(b.label),
  );
}

export function filterRuns(
  runs: readonly RepairJobRun[],
  options: { jobId?: string; failuresOnly?: boolean },
): RepairJobRun[] {
  return runs.filter((run) => {
    if (options.jobId && run.job_id !== options.jobId) return false;
    if (options.failuresOnly && runOutcome(run) !== 'failed') return false;
    return true;
  });
}

/** Counters worth showing in the expanded panel. Zeroes are dropped — except
 *  `checked`, where zero is the interesting fact (the job found nothing to
 *  look at, which is a different story from finding nothing wrong). */
export function runCounters(run: RepairJobRun): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [
    { label: 'Checked', value: (run.items_scanned || 0).toLocaleString() },
  ];
  if (run.findings_created) {
    rows.push({ label: 'Findings raised', value: run.findings_created.toLocaleString() });
  }
  if (run.auto_fixed) rows.push({ label: 'Auto-fixed', value: run.auto_fixed.toLocaleString() });
  if (run.errors) rows.push({ label: 'Errors', value: run.errors.toLocaleString() });
  rows.push({ label: 'Took', value: formatDuration(run.duration_seconds) });
  return rows;
}
