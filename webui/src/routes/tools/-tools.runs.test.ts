/**
 * Run history arithmetic.
 *
 * The load-bearing test here is the UTC one: the server writes SQLite's
 * CURRENT_TIMESTAMP (UTC, no zone) and JS parses that shape as local, so west
 * of Greenwich every run read as happening in the future and printed "now".
 * That single bug is most of why the old history was unreadable.
 */

import { describe, expect, it } from 'vitest';

import type { RepairJobRun } from './-tools.types';

import { formatCacheAge, parseDbTimestamp } from './-tools.core';
import {
  filterRuns,
  formatDuration,
  formatSpan,
  groupRunsByDay,
  runClock,
  runCounters,
  runDayBars,
  runJobFilters,
  runJobSummaries,
  runOutcome,
  runReason,
  runSummary,
  runTotals,
} from './-tools.runs';

const run = (over: Partial<RepairJobRun> = {}): RepairJobRun => ({
  id: 1,
  job_id: 'orphan_file_detector',
  display_name: 'Orphan File Detector',
  started_at: '2026-08-12T09:00:00Z',
  finished_at: '2026-08-12T09:00:12Z',
  duration_seconds: 12.3,
  items_scanned: 1234,
  findings_created: 0,
  auto_fixed: 0,
  errors: 0,
  status: 'completed',
  ...over,
});

describe('parseDbTimestamp', () => {
  it('reads a bare SQLite stamp as UTC, not local', () => {
    // The whole reason the history said "now" about everything.
    expect(parseDbTimestamp('2026-08-12 09:00:00')).toBe(Date.UTC(2026, 7, 12, 9, 0, 0));
  });

  it('leaves a real ISO string alone', () => {
    expect(parseDbTimestamp('2026-08-12T09:00:00Z')).toBe(Date.UTC(2026, 7, 12, 9, 0, 0));
    expect(parseDbTimestamp('2026-08-12T09:00:00+02:00')).toBe(Date.UTC(2026, 7, 12, 7, 0, 0));
  });

  it('handles fractional seconds', () => {
    expect(parseDbTimestamp('2026-08-12 09:00:00.500')).toBe(Date.UTC(2026, 7, 12, 9, 0, 0) + 500);
  });
});

describe('formatCacheAge', () => {
  const now = Date.UTC(2026, 7, 12, 12, 0, 0);

  it('ages a bare SQLite stamp correctly', () => {
    expect(formatCacheAge('2026-08-12 09:00:00', now)).toBe('3h');
  });

  it('says "now" for a future stamp rather than printing a negative age', () => {
    expect(formatCacheAge('2026-08-12 18:00:00', now)).toBe('now');
  });
});

describe('runOutcome', () => {
  it('separates a scan that found nothing from one that found something', () => {
    // Both are "completed", and treating them alike is what made the list
    // read as undifferentiated noise.
    expect(runOutcome(run())).toBe('quiet');
    expect(runOutcome(run({ findings_created: 4 }))).toBe('success');
    expect(runOutcome(run({ auto_fixed: 2 }))).toBe('success');
  });

  it('treats a recorded failure OR a non-zero error count as failed', () => {
    expect(runOutcome(run({ status: 'failed' }))).toBe('failed');
    expect(runOutcome(run({ status: 'completed', errors: 1 }))).toBe('failed');
  });

  it('keeps a run that quit early apart from a finished one (#1289)', () => {
    expect(runOutcome(run({ status: 'stopped' }))).toBe('stopped');
    expect(runOutcome(run({ status: 'stopped', findings_created: 4 }))).toBe('stopped');
  });

  it('treats a run with no finish as still running', () => {
    expect(runOutcome(run({ finished_at: null, status: 'running' }))).toBe('running');
    expect(runOutcome(run({ finished_at: null, status: 'completed' }))).toBe('running');
  });
});

describe('formatDuration', () => {
  it('scales from seconds to hours', () => {
    expect(formatDuration(12.34)).toBe('12.3s');
    expect(formatDuration(90)).toBe('1m 30s');
    expect(formatDuration(3720)).toBe('1h 2m');
  });

  it('says nothing rather than 0.0s when there is no duration', () => {
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration(0)).toBe('—');
  });
});

describe('runSummary', () => {
  it('says what a quiet run accomplished', () => {
    expect(runSummary(run())).toBe('nothing to fix · 1,234 checked · 12.3s');
  });

  it('lists what a productive run produced', () => {
    expect(runSummary(run({ findings_created: 5, auto_fixed: 2 }))).toBe(
      '1,234 checked · 5 found · 2 fixed automatically · 12.3s',
    );
  });

  it('leads with the failure, and counts errors only when there are several', () => {
    expect(runSummary(run({ status: 'failed', errors: 1 }))).toBe('failed after 12.3s');
    expect(runSummary(run({ status: 'failed', errors: 3 }))).toBe('failed after 12.3s · 3 errors');
  });

  it('says a run stopped early, and what it found before it did', () => {
    expect(runSummary(run({ status: 'stopped', items_scanned: 3008 }))).toBe(
      'stopped early · 3,008 checked · 12.3s',
    );
    expect(runSummary(run({ status: 'stopped', items_scanned: 3008, findings_created: 7 }))).toBe(
      'stopped early · 3,008 checked · 7 found · 12.3s',
    );
  });

  it('reports progress for a run still going', () => {
    expect(runSummary(run({ finished_at: null, status: 'running' }))).toBe(
      'running · 1,234 scanned so far',
    );
    expect(runSummary(run({ finished_at: null, status: 'running', items_scanned: 0 }))).toBe(
      'running',
    );
  });
});

describe('groupRunsByDay', () => {
  const now = new Date(2026, 7, 12, 12, 0, 0).getTime(); // local noon

  it('labels today and yesterday, newest day first', () => {
    const days = groupRunsByDay(
      [
        run({ id: 1, started_at: new Date(2026, 7, 12, 9, 0, 0).toISOString() }),
        run({ id: 2, started_at: new Date(2026, 7, 11, 9, 0, 0).toISOString() }),
        run({ id: 3, started_at: new Date(2026, 7, 5, 9, 0, 0).toISOString() }),
      ],
      now,
    );
    expect(days.map((day) => day.label)).toEqual(['Today', 'Yesterday', 'Aug 5']);
    expect(days[0].runs.map((r) => r.id)).toEqual([1]);
  });

  it('keeps the given order within a day', () => {
    const days = groupRunsByDay(
      [
        run({ id: 1, started_at: new Date(2026, 7, 12, 9, 0, 0).toISOString() }),
        run({ id: 2, started_at: new Date(2026, 7, 12, 8, 0, 0).toISOString() }),
      ],
      now,
    );
    expect(days[0].runs.map((r) => r.id)).toEqual([1, 2]);
  });

  it('keeps an undated run instead of dropping it, and sorts it last', () => {
    // A run that happened is a fact even when its clock is not.
    const days = groupRunsByDay(
      [
        run({ id: 9, started_at: null }),
        run({ id: 1, started_at: new Date(2026, 7, 12, 9, 0, 0).toISOString() }),
      ],
      now,
    );
    expect(days.map((day) => day.label)).toEqual(['Today', 'Undated']);
  });

  it('returns nothing for no runs', () => {
    expect(groupRunsByDay([], now)).toEqual([]);
  });
});

describe('runJobFilters', () => {
  it('one chip per job, busiest first, flagging any job with a failure', () => {
    const chips = runJobFilters([
      run({ job_id: 'a', display_name: 'Alpha' }),
      run({ job_id: 'a', display_name: 'Alpha' }),
      run({ job_id: 'b', display_name: 'Beta', status: 'failed' }),
    ]);
    expect(chips.map((chip) => [chip.jobId, chip.count, chip.hasFailure])).toEqual([
      ['a', 2, false],
      ['b', 1, true],
    ]);
  });

  it('falls back to a spaced job id when a run has no display name', () => {
    expect(runJobFilters([run({ job_id: 'dead_file_cleaner', display_name: null })])[0].label).toBe(
      'dead file cleaner',
    );
  });
});

describe('filterRuns', () => {
  const runs = [
    run({ id: 1, job_id: 'a' }),
    run({ id: 2, job_id: 'b', status: 'failed' }),
    run({ id: 3, job_id: 'a', status: 'failed' }),
  ];

  it('scopes by job', () => {
    expect(filterRuns(runs, { jobId: 'a' }).map((r) => r.id)).toEqual([1, 3]);
  });

  it('shows failures only', () => {
    expect(filterRuns(runs, { failuresOnly: true }).map((r) => r.id)).toEqual([2, 3]);
  });

  it('combines both', () => {
    expect(filterRuns(runs, { jobId: 'a', failuresOnly: true }).map((r) => r.id)).toEqual([3]);
  });
});

describe('runCounters', () => {
  it('always shows checked and took, and drops the zeroes between them', () => {
    expect(runCounters(run()).map((c) => c.label)).toEqual(['Checked', 'Took']);
    expect(
      runCounters(run({ findings_created: 3, auto_fixed: 1, errors: 2 })).map((c) => c.label),
    ).toEqual(['Checked', 'Findings raised', 'Auto-fixed', 'Errors', 'Took']);
  });
});

describe('runClock', () => {
  it('is empty rather than "Invalid Date" for junk', () => {
    expect(runClock(null)).toBe('');
    expect(runClock('not a date')).toBe('');
  });
});

describe('the history page’s numbers', () => {
  const at = (iso: string, over: Partial<RepairJobRun> = {}): RepairJobRun => ({
    id: Math.floor(Math.random() * 1e9),
    job_id: 'alpha',
    display_name: 'Alpha',
    started_at: iso,
    finished_at: iso,
    status: 'completed',
    items_scanned: 10,
    findings_created: 0,
    auto_fixed: 0,
    duration_seconds: 30,
    errors: 0,
    ...over,
  });

  it('adds up what the runs did, leaving out one still running', () => {
    const totals = runTotals([
      at('2026-11-10 08:00:00', { findings_created: 3, auto_fixed: 2 }),
      at('2026-11-09 08:00:00', { status: 'failed', duration_seconds: 5 }),
      at('2026-11-10 09:00:00', { status: 'running', finished_at: null }),
    ]);
    expect(totals).toEqual({
      runs: 2,
      failed: 1,
      busy: 1,
      checked: 20,
      found: 3,
      fixed: 2,
      seconds: 35,
      since: '2026-11-09 08:00:00',
    });
  });

  it('draws a bar for every day, empty ones too, oldest first', () => {
    const now = new Date(2026, 10, 10, 15, 0, 0).getTime();
    const local = (day: number, hour: number) =>
      new Date(2026, 10, day, hour).toISOString().replace('T', ' ').slice(0, 19);
    const bars = runDayBars(
      [at(local(10, 9)), at(local(10, 10), { status: 'failed' }), at(local(8, 9))],
      3,
      now,
    );
    expect(bars.map((bar) => [bar.ok, bar.failed])).toEqual([
      [1, 0],
      [0, 0],
      [1, 1],
    ]);
    // a run older than the window is not squeezed into the first bar
    expect(runDayBars([at(local(1, 9))], 3, now).every((bar) => bar.ok === 0)).toBe(true);
  });

  it('folds runs per job, the one that ran last first', () => {
    const jobs = runJobSummaries([
      at('2026-11-10 08:00:00', { duration_seconds: 20, findings_created: 4 }),
      at('2026-11-09 08:00:00', { duration_seconds: 40, status: 'failed' }),
      at('2026-11-10 09:00:00', { job_id: 'beta', display_name: 'Beta', duration_seconds: 0 }),
    ]);
    expect(jobs.map((job) => job.jobId)).toEqual(['beta', 'alpha']);
    expect(jobs[1]).toMatchObject({
      runs: 2,
      failed: 1,
      found: 4,
      averageSeconds: 30,
      // its newest run, not its failed older one
      lastOutcome: 'success',
      lastStarted: '2026-11-10 08:00:00',
    });
    // a job with no timed run has no average, not a divide by zero
    expect(jobs[0].averageSeconds).toBe(0);
  });

  it('gives a failed run its reason in one line', () => {
    expect(runReason(at('2026-11-10 08:00:00'))).toBe('');
    expect(
      runReason(at('x', { status: 'failed', error_text: '\n  AcoustID key missing\nTraceback…' })),
    ).toBe('AcoustID key missing');
    expect(runReason(at('x', { status: 'completed', errors: 12 }))).toBe('12 errors along the way');
    expect(runReason(at('x', { status: 'failed' }))).toBe('No reason was recorded');
    expect(runReason(at('x', { status: 'stopped' }))).toBe('Stopped before the end');
  });

  it('says a long stretch in its two biggest units', () => {
    expect(formatSpan(45)).toBe('45s');
    expect(formatSpan(125)).toBe('2m 5s');
    expect(formatSpan(56640)).toBe('15h 44m');
    expect(formatSpan(3 * 86400 + 7200)).toBe('3d 2h');
  });
});
