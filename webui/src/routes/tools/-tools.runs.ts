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
export type RunOutcome = 'success' | 'quiet' | 'failed' | 'stopped' | 'running';

/**
 * `quiet` is its own outcome on purpose: a scan that found nothing is the
 * single most common run and the best possible news, and giving it the same
 * green tick as a scan that found four hundred problems made the whole list
 * read as undifferentiated noise.
 */
export function runOutcome(run: RepairJobRun): RunOutcome {
  const status = (run.status || '').toLowerCase();
  if (status === 'failed' || status === 'error' || (run.errors || 0) > 0) return 'failed';
  // the job quit before the end on its own (a rate limit, say). not a crash,
  // but not done either, and a green tick hid that the rest went unchecked
  if (status === 'stopped') return 'stopped';
  if (status === 'running' || !run.finished_at) return 'running';
  if ((run.findings_created || 0) === 0 && (run.auto_fixed || 0) === 0) return 'quiet';
  return 'success';
}

export const RUN_OUTCOME_ICONS: Record<RunOutcome, string> = {
  success: '✓', // ✓
  quiet: '·', // ·
  failed: '✗', // ✗
  stopped: '!',
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
  if (outcome === 'stopped') {
    const foundPart = found > 0 ? ` · ${found.toLocaleString()} found` : '';
    return `stopped early · ${scanned.toLocaleString()} checked${foundPart} · ${formatDuration(run.duration_seconds)}`;
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
  return [...byJob.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
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

// ── The history page's numbers ───────────────────────────────────────────────

export interface RunTotals {
  runs: number;
  failed: number;
  /** runs that changed or flagged something */
  busy: number;
  checked: number;
  found: number;
  fixed: number;
  seconds: number;
  /** the oldest run counted, as a db timestamp, for "since ..." */
  since: string | null;
}

/** What the listed runs add up to. The history used to be a list you had to
 *  read to learn anything; these are the answers it was being read for. */
export function runTotals(runs: readonly RepairJobRun[]): RunTotals {
  const totals: RunTotals = {
    runs: 0,
    failed: 0,
    busy: 0,
    checked: 0,
    found: 0,
    fixed: 0,
    seconds: 0,
    since: null,
  };
  for (const run of runs) {
    const outcome = runOutcome(run);
    if (outcome === 'running') continue;
    totals.runs += 1;
    if (outcome === 'failed') totals.failed += 1;
    if (outcome === 'success') totals.busy += 1;
    totals.checked += run.items_scanned || 0;
    totals.found += run.findings_created || 0;
    totals.fixed += run.auto_fixed || 0;
    totals.seconds += run.duration_seconds || 0;
    if (run.started_at && (!totals.since || String(run.started_at) < String(totals.since))) {
      totals.since = run.started_at;
    }
  }
  return totals;
}

export interface RunDayBar {
  key: string;
  label: string;
  ok: number;
  failed: number;
}

/** One bar per calendar day, oldest first, for the last `days` days. A day
 *  with no runs is still a bar (an empty one): the gap is information. */
export function runDayBars(
  runs: readonly RepairJobRun[],
  days = 14,
  now: number = Date.now(),
): RunDayBar[] {
  const bars: RunDayBar[] = [];
  const index = new Map<string, RunDayBar>();
  const start = new Date(now);
  start.setHours(12, 0, 0, 0);
  for (let back = days - 1; back >= 0; back -= 1) {
    const stamp = start.getTime() - back * 86400000;
    const key = dayKey(stamp);
    const bar = {
      key,
      label: new Date(stamp).toLocaleDateString([], { month: 'short', day: 'numeric' }),
      ok: 0,
      failed: 0,
    };
    index.set(key, bar);
    bars.push(bar);
  }
  for (const run of runs) {
    const stamp = run.started_at ? parseDbTimestamp(run.started_at) : Number.NaN;
    if (Number.isNaN(stamp)) continue;
    const bar = index.get(dayKey(stamp));
    if (!bar) continue;
    const outcome = runOutcome(run);
    if (outcome === 'running') continue;
    if (outcome === 'failed') bar.failed += 1;
    else bar.ok += 1;
  }
  return bars;
}

export interface RunJobSummary {
  jobId: string;
  label: string;
  runs: number;
  failed: number;
  found: number;
  fixed: number;
  /** mean length of the runs that recorded one */
  averageSeconds: number;
  /** the newest run: its outcome and when it started */
  lastOutcome: RunOutcome;
  lastStarted: string | null;
}

/** The same runs folded per job, the job that ran most recently first. */
export function runJobSummaries(runs: readonly RepairJobRun[]): RunJobSummary[] {
  const byJob = new Map<string, RunJobSummary & { timed: number; seconds: number }>();
  for (const run of runs) {
    const jobId = run.job_id || '';
    if (!jobId) continue;
    let entry = byJob.get(jobId);
    if (!entry) {
      entry = {
        jobId,
        label: run.display_name || jobId.replace(/_/g, ' '),
        runs: 0,
        failed: 0,
        found: 0,
        fixed: 0,
        averageSeconds: 0,
        lastOutcome: runOutcome(run),
        lastStarted: run.started_at || null,
        timed: 0,
        seconds: 0,
      };
      byJob.set(jobId, entry);
    }
    const outcome = runOutcome(run);
    entry.runs += 1;
    if (outcome === 'failed') entry.failed += 1;
    entry.found += run.findings_created || 0;
    entry.fixed += run.auto_fixed || 0;
    if ((run.duration_seconds || 0) > 0) {
      entry.timed += 1;
      entry.seconds += run.duration_seconds || 0;
    }
    if (String(run.started_at || '') > String(entry.lastStarted || '')) {
      entry.lastStarted = run.started_at || null;
      entry.lastOutcome = outcome;
    }
  }
  return [...byJob.values()]
    .map(({ timed, seconds, ...entry }) => ({
      ...entry,
      averageSeconds: timed ? seconds / timed : 0,
    }))
    .sort((a, b) => String(b.lastStarted || '').localeCompare(String(a.lastStarted || '')));
}

/** Why a run did not finish cleanly, as one line for its row. */
export function runReason(run: RepairJobRun): string {
  const outcome = runOutcome(run);
  if (outcome !== 'failed' && outcome !== 'stopped') return '';
  const first = String(run.error_text || '')
    .split('\n')
    .map((line) => line.trim())
    .find(Boolean);
  if (first) return first;
  const errors = run.errors || 0;
  if (errors > 0) return `${errors.toLocaleString()} error${errors === 1 ? '' : 's'} along the way`;
  return outcome === 'stopped' ? 'Stopped before the end' : 'No reason was recorded';
}

/** A long stretch of time in the two biggest units: "3h 12m", "45s". */
export function formatSpan(seconds: number): string {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${total % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
