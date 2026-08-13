/**
 * Operations arithmetic — families, cadence, and when a job actually runs.
 *
 * The cadence round-trip matters most: the config stores an integer number of
 * hours, and a control that let a user pick something unrepresentable would
 * silently save a different schedule than the one they chose.
 */

import { describe, expect, it } from 'vitest';

import type { RepairJob } from './-tools.types';
import {
  CATEGORY_ORDER,
  cadenceFromHours,
  cadenceLabel,
  categoryGlow,
  familySummary,
  hoursFromCadence,
  jobFamilies,
  jobSchedule,
  jobTrend,
  mostOverdueJob,
} from './-tools.ops';

const job = (over: Partial<RepairJob> = {}): RepairJob =>
  ({
    job_id: 'orphan_file_detector',
    display_name: 'Orphan File Detector',
    category: 'Files & storage',
    enabled: true,
    is_running: false,
    interval_hours: 24,
    ...over,
  }) as RepairJob;

const NOW = Date.UTC(2026, 7, 12, 12, 0, 0);
const hoursFromNow = (hours: number) => new Date(NOW + hours * 3600000).toISOString();

describe('cadence', () => {
  it('reads hours back as the largest unit that divides them cleanly', () => {
    expect(cadenceFromHours(6)).toEqual({ interval: 6, unit: 'hours' });
    expect(cadenceFromHours(24)).toEqual({ interval: 1, unit: 'days' });
    expect(cadenceFromHours(72)).toEqual({ interval: 3, unit: 'days' });
    expect(cadenceFromHours(168)).toEqual({ interval: 1, unit: 'weeks' });
    expect(cadenceFromHours(336)).toEqual({ interval: 2, unit: 'weeks' });
    // 30h divides by neither, so it stays hours rather than rounding a
    // schedule the user did not ask for.
    expect(cadenceFromHours(30)).toEqual({ interval: 30, unit: 'hours' });
  });

  it('never produces a zero or negative interval', () => {
    expect(cadenceFromHours(0)).toEqual({ interval: 1, unit: 'days' });
    expect(cadenceFromHours(null)).toEqual({ interval: 1, unit: 'days' });
    expect(cadenceFromHours(-5)).toEqual({ interval: 1, unit: 'hours' });
  });

  it('round-trips through hours, which is all the config can store', () => {
    for (const hours of [1, 6, 24, 30, 72, 168, 336]) {
      expect(hoursFromCadence(cadenceFromHours(hours))).toBe(hours);
    }
  });

  it('names the common cadences the way a person would', () => {
    expect(cadenceLabel(1)).toBe('hourly');
    expect(cadenceLabel(24)).toBe('daily');
    expect(cadenceLabel(168)).toBe('weekly');
    expect(cadenceLabel(6)).toBe('every 6 hours');
    expect(cadenceLabel(72)).toBe('every 3 days');
  });
});

describe('jobSchedule', () => {
  it('running and disabled outrank any stored next_run', () => {
    expect(jobSchedule(job({ is_running: true, next_run: hoursFromNow(5) }), NOW).state).toBe(
      'running',
    );
    expect(jobSchedule(job({ enabled: false, next_run: hoursFromNow(5) }), NOW).state).toBe(
      'disabled',
    );
  });

  it('distinguishes never-run from due', () => {
    expect(jobSchedule(job({ next_run: null, last_run: null }), NOW)).toEqual({
      state: 'never',
      label: 'never run yet',
    });
    expect(jobSchedule(job({ next_run: null, last_run: { findings_created: 1 } }), NOW).label).toBe(
      'due now',
    );
  });

  it('counts down without promising a clock time', () => {
    // The worker is a staleness queue: "in about 3h" is a promise it can
    // keep, "at 15:00" is not.
    expect(jobSchedule(job({ next_run: hoursFromNow(3) }), NOW).label).toBe('in about 3h');
    expect(jobSchedule(job({ next_run: hoursFromNow(0.5) }), NOW).label).toBe('in about 30m');
    expect(jobSchedule(job({ next_run: hoursFromNow(72) }), NOW).label).toBe('in about 3d');
  });

  it('calls a job overdue only once it is meaningfully late for ITS interval', () => {
    // An hourly job an hour late and a weekly job an hour late are not the
    // same story, so the threshold scales with the interval.
    expect(jobSchedule(job({ interval_hours: 24, next_run: hoursFromNow(-2) }), NOW).state).toBe(
      'due',
    );
    expect(jobSchedule(job({ interval_hours: 24, next_run: hoursFromNow(-20) }), NOW)).toEqual({
      state: 'overdue',
      label: 'overdue by 20h',
      overdueHours: 20,
    });
    expect(jobSchedule(job({ interval_hours: 1, next_run: hoursFromNow(-3) }), NOW).state).toBe(
      'overdue',
    );
  });

  it('treats an unparseable next_run as due rather than throwing', () => {
    expect(jobSchedule(job({ next_run: 'not a date' }), NOW).state).toBe('due');
  });
});

describe('jobFamilies', () => {
  it('groups, orders by the house order, and sorts alphabetically inside', () => {
    const families = jobFamilies(
      [
        job({ job_id: 'z', display_name: 'Zeta', category: 'Tags & metadata' }),
        job({ job_id: 'a', display_name: 'Alpha', category: 'Tags & metadata' }),
        job({ job_id: 'o', display_name: 'Orphans', category: 'Files & storage' }),
      ],
      NOW,
    );
    expect(families.map((f) => f.category)).toEqual(['Files & storage', 'Tags & metadata']);
    expect(families[1].jobs.map((j) => j.display_name)).toEqual(['Alpha', 'Zeta']);
  });

  it('puts an unknown family last instead of dropping it', () => {
    const families = jobFamilies(
      [
        job({ job_id: 'x', category: 'Invented By The Backend' }),
        job({ job_id: 'o', category: 'Files & storage' }),
      ],
      NOW,
    );
    expect(families.map((f) => f.category)).toEqual([
      'Files & storage',
      'Invented By The Backend',
    ]);
  });

  it('counts running, due and pending per family', () => {
    const family = jobFamilies(
      [
        job({ job_id: 'a', is_running: true, pending_findings_count: 10 }),
        job({ job_id: 'b', enabled: false }),
        job({ job_id: 'c', next_run: hoursFromNow(-40), pending_findings_count: 5 }),
        job({ job_id: 'd', next_run: hoursFromNow(9) }),
      ],
      NOW,
    )[0];
    expect(family.running).toBe(1);
    expect(family.enabled).toBe(3);
    expect(family.waiting).toBe(1);
    expect(family.pending).toBe(15);
    expect(familySummary(family)).toBe('4 jobs · 1 running · 1 due · 1 off');
  });

  it('says all scheduled when nothing needs attention', () => {
    const family = jobFamilies([job({ next_run: hoursFromNow(9) })], NOW)[0];
    expect(familySummary(family)).toBe('1 job · all scheduled');
  });

  it('drops families with no jobs rather than showing an empty container', () => {
    expect(jobFamilies([], NOW)).toEqual([]);
  });

  it('gives every ordered family a glow', () => {
    for (const category of CATEGORY_ORDER) {
      expect(categoryGlow(category), category).toMatch(/^\d+,\d+,\d+$/);
    }
  });
});

describe('jobTrend', () => {
  it('takes only this job&rsquo;s runs, oldest first', () => {
    const runs = [
      { job_id: 'a', findings_created: 3 },
      { job_id: 'b', findings_created: 99 },
      { job_id: 'a', findings_created: 1 },
    ];
    expect(jobTrend(runs, 'a')).toEqual([1, 3]);
  });
});

describe('mostOverdueJob', () => {
  it('picks the worst offender, and nothing when none are overdue', () => {
    const jobs = [
      job({ job_id: 'a', next_run: hoursFromNow(-30) }),
      job({ job_id: 'b', next_run: hoursFromNow(-90) }),
      job({ job_id: 'c', next_run: hoursFromNow(5) }),
    ];
    expect(mostOverdueJob(jobs, NOW)?.job_id).toBe('b');
    expect(mostOverdueJob([job({ next_run: hoursFromNow(5) })], NOW)).toBeNull();
  });

  it('ignores disabled jobs — an off job is not late, it is off', () => {
    expect(
      mostOverdueJob([job({ enabled: false, next_run: hoursFromNow(-500) })], NOW),
    ).toBeNull();
  });
});
