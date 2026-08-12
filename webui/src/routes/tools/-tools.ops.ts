/**
 * Operations — grouping thirty jobs into something with a shape, and saying
 * when each one actually runs.
 *
 * Two complaints this answers:
 *
 * 1. "Just a bunch of cards, no order or organization." Thirty identical
 *    stacked cards is a wall. The backend files each job under a family; this
 *    folds them into containers and summarises each one.
 * 2. "All jobs have their own schedules and the user can't configure it."
 *    They could — behind a gear, as a bare "Interval (hours)" box. The
 *    cadence belongs on the tile face, in the same language the auto-sync
 *    page uses.
 */

import type { RepairJob, RepairJobRun } from './-tools.types';

import { parseDbTimestamp } from './-tools.core';

/** The order families are shown in — mirrors JOB_CATEGORY_ORDER in
 *  core/repair_worker.py. A family the backend invents that is missing here
 *  sorts to the end rather than vanishing. */
export const CATEGORY_ORDER = [
  'Files & storage',
  'Audio quality',
  'Tags & metadata',
  'Artwork & lyrics',
  'Collection gaps',
  'System',
  'Other',
];

/**
 * The glow each family carries, as `R,G,B` for `--tile-glow`. Same device the
 * arcade tiles and the dashboard orbs use, so a family is recognisable by its
 * colour before you have read a word.
 */
export const CATEGORY_GLOW: Record<string, string> = {
  'Files & storage': '56,189,248',
  'Audio quality': '244,114,182',
  'Tags & metadata': '168,85,247',
  'Artwork & lyrics': '245,158,11',
  'Collection gaps': '34,197,94',
  System: '148,163,184',
  Other: '148,163,184',
};

export function categoryGlow(category: string): string {
  return CATEGORY_GLOW[category] || CATEGORY_GLOW.Other;
}

/** One line per family, so a collapsed container still says something. */
export const CATEGORY_BLURBS: Record<string, string> = {
  'Files & storage': 'What is on disk, where it belongs, and what should not be there.',
  'Audio quality': 'Whether the audio is what it claims to be, and good enough.',
  'Tags & metadata': 'What is written on and about your tracks.',
  'Artwork & lyrics': 'The extras that make a library feel finished.',
  'Collection gaps': 'Releases you are missing rather than problems you have.',
  System: 'Housekeeping SoulSync does for itself.',
  Other: 'Jobs that have not been filed under a family yet.',
};

// ── Cadence ──────────────────────────────────────────────────────────────────

export type IntervalUnit = 'hours' | 'days' | 'weeks';

export interface Cadence {
  interval: number;
  unit: IntervalUnit;
}

/**
 * Hours as the largest unit that divides them cleanly.
 *
 * The store is an integer number of hours, so minutes are not representable —
 * offering them would let a user pick a cadence the worker cannot keep.
 */
export function cadenceFromHours(hours: number | null | undefined): Cadence {
  const value = Math.max(1, Math.round(hours || 0) || 24);
  if (value % 168 === 0) return { interval: value / 168, unit: 'weeks' };
  if (value % 24 === 0) return { interval: value / 24, unit: 'days' };
  return { interval: value, unit: 'hours' };
}

export function hoursFromCadence(cadence: Cadence): number {
  const size = cadence.unit === 'weeks' ? 168 : cadence.unit === 'days' ? 24 : 1;
  return Math.max(1, Math.round((cadence.interval || 1) * size));
}

/** "every 6 hours" / "daily" / "weekly" — what the tile face reads. */
export function cadenceLabel(hours: number | null | undefined): string {
  const { interval, unit } = cadenceFromHours(hours);
  if (unit === 'days' && interval === 1) return 'daily';
  if (unit === 'weeks' && interval === 1) return 'weekly';
  if (unit === 'hours' && interval === 1) return 'hourly';
  return `every ${interval} ${unit}`;
}

// ── When it will actually run ────────────────────────────────────────────────

export type JobSchedule =
  | { state: 'running'; label: string }
  | { state: 'disabled'; label: string }
  | { state: 'never'; label: string }
  | { state: 'overdue'; label: string; overdueHours: number }
  | { state: 'due'; label: string }
  | { state: 'waiting'; label: string };

/**
 * What to say about a job's next run.
 *
 * The worker is a staleness queue, not a cron: it picks whichever enabled job
 * is furthest past its interval, whenever it is idle. So a job can be "due"
 * without a promise about the minute it will start, and the copy never claims
 * one — "due now" and "in about 3h" are both honest; "runs at 03:00" would
 * not be.
 */
export function jobSchedule(job: RepairJob, now: number = Date.now()): JobSchedule {
  if (job.is_running) return { state: 'running', label: 'running now' };
  if (!job.enabled) return { state: 'disabled', label: 'off' };
  if (!job.next_run) {
    return { state: 'never', label: job.last_run ? 'due now' : 'never run yet' };
  }

  const next = parseDbTimestamp(job.next_run);
  if (Number.isNaN(next)) return { state: 'due', label: 'due now' };

  const diffHours = (next - now) / 3600000;
  if (diffHours <= 0) {
    const overdue = Math.abs(diffHours);
    // Under an interval's grace, "due" is the honest word; a job that has
    // been waiting days is a different story and should look like one.
    if (overdue >= Math.max(2, (job.interval_hours || 24) * 0.5)) {
      return { state: 'overdue', label: `overdue by ${roughDuration(overdue)}`, overdueHours: overdue };
    }
    return { state: 'due', label: 'due now' };
  }
  return { state: 'waiting', label: `in about ${roughDuration(diffHours)}` };
}

function roughDuration(hours: number): string {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

// ── Families ─────────────────────────────────────────────────────────────────

export interface JobFamily {
  category: string;
  glow: string;
  blurb: string;
  jobs: RepairJob[];
  enabled: number;
  running: number;
  /** Enabled jobs that are due or overdue right now. */
  waiting: number;
  /** Pending findings across the family — what the container badge shows. */
  pending: number;
}

/**
 * Jobs folded into families, in the served order, alphabetical within a
 * family.
 *
 * Families with no jobs are dropped: an install where nothing is filed under
 * "Collection gaps" should not show an empty container explaining that.
 */
export function jobFamilies(jobs: readonly RepairJob[], now: number = Date.now()): JobFamily[] {
  const byCategory = new Map<string, RepairJob[]>();
  for (const job of jobs) {
    const category = job.category || 'Other';
    const bucket = byCategory.get(category);
    if (bucket) bucket.push(job);
    else byCategory.set(category, [job]);
  }

  const families: JobFamily[] = [];
  for (const [category, list] of byCategory) {
    const sorted = [...list].sort((a, b) => a.display_name.localeCompare(b.display_name));
    families.push({
      category,
      glow: categoryGlow(category),
      blurb: CATEGORY_BLURBS[category] || CATEGORY_BLURBS.Other,
      jobs: sorted,
      enabled: sorted.filter((job) => job.enabled).length,
      running: sorted.filter((job) => job.is_running).length,
      waiting: sorted.filter((job) => {
        const schedule = jobSchedule(job, now);
        return schedule.state === 'due' || schedule.state === 'overdue' || schedule.state === 'never';
      }).length,
      pending: sorted.reduce((sum, job) => sum + (job.pending_findings_count || 0), 0),
    });
  }

  return families.sort((a, b) => {
    const rankA = CATEGORY_ORDER.indexOf(a.category);
    const rankB = CATEGORY_ORDER.indexOf(b.category);
    return (
      (rankA === -1 ? CATEGORY_ORDER.length : rankA) -
        (rankB === -1 ? CATEGORY_ORDER.length : rankB) || a.category.localeCompare(b.category)
    );
  });
}

/** The container's own one-line status. Reads left to right in the order you
 *  would ask: is anything happening, is anything waiting, is anything off. */
export function familySummary(family: JobFamily): string {
  const parts: string[] = [];
  if (family.running > 0) parts.push(`${family.running} running`);
  if (family.waiting > 0) parts.push(`${family.waiting} due`);
  const off = family.jobs.length - family.enabled;
  if (off > 0) parts.push(`${off} off`);
  if (parts.length === 0) parts.push('all scheduled');
  return `${family.jobs.length} job${family.jobs.length === 1 ? '' : 's'} · ${parts.join(' · ')}`;
}

// ── Sparkline source ─────────────────────────────────────────────────────────

/** Findings-per-run for one job, oldest → newest, from the shared history. */
export function jobTrend(
  runs: readonly RepairJobRun[],
  jobId: string,
  limit = 12,
): number[] {
  return runs
    .filter((run) => run.job_id === jobId)
    .slice(0, limit)
    .map((run) => Math.max(0, run.findings_created || 0))
    .reverse();
}

/** The job the page would point at first: enabled, most overdue, and with
 *  something to show for it. Null when nothing is waiting. */
export function mostOverdueJob(jobs: readonly RepairJob[], now: number = Date.now()): RepairJob | null {
  let best: RepairJob | null = null;
  let worst = 0;
  for (const job of jobs) {
    const schedule = jobSchedule(job, now);
    if (schedule.state !== 'overdue') continue;
    if (schedule.overdueHours > worst) {
      worst = schedule.overdueHours;
      best = job;
    }
  }
  return best;
}
