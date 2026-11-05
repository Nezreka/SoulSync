/** the activity chart's dates read as dates, not 2026-10-29 */

import { describe, expect, it } from 'vitest';

import { statsDateLabel } from './stats-page';

describe('statsDateLabel', () => {
  it('formats days and months, and passes anything else through', () => {
    const day = statsDateLabel('2026-10-29');
    expect(day).not.toContain('2026-10');
    expect(day).toMatch(/29/);
    expect(statsDateLabel('2026-10')).toMatch(/2026/);
    expect(statsDateLabel('2026-10')).not.toBe('2026-10');
    expect(statsDateLabel('Week 3')).toBe('Week 3');
  });

  it('reads the day in local time, so the label never slips to the day before', () => {
    // new Date('2026-11-01') is UTC midnight, which is Oct 31 west of Greenwich
    expect(statsDateLabel('2026-11-01')).toMatch(/\b1\b/);
  });
});
