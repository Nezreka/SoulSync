/** reading the P1 routes' shapes (scrap/help/HELP_API.md) */

import { describe, expect, it } from 'vitest';

import { mergeStatus, normalizeStatus, statusFromHealth, type HealthReport } from './api';

describe('the status poll', () => {
  it('reads an admin answer, errors only', () => {
    expect(
      normalizeStatus({ errors: 2, checked_at: '2026-11-17T21:42:07Z', refreshing: false }),
    ).toEqual({
      errors: 2,
      warnings: 0,
      checked_at: '2026-11-17T21:42:07Z',
      refreshing: false,
      top: [],
    });
  });

  it("a member's {} is no status at all", () => {
    expect(normalizeStatus({})).toBeNull();
    expect(normalizeStatus(null)).toBeNull();
  });

  it('the first run still going is refreshing, not fine', () => {
    expect(normalizeStatus({ errors: 0, checked_at: null, refreshing: true })).toMatchObject({
      refreshing: true,
      checked_at: null,
    });
  });

  it('keeps the titles Health gave while the poll agrees, drops them when it does not', () => {
    const full = statusFromHealth({
      status: 'error',
      checked_at: 'a',
      counts: { error: 1, warning: 1 },
      findings: [
        { id: 'x', severity: 'error', area: 'paths', title: 'X' },
        { id: 'y', severity: 'warning', area: 'disk', title: 'Y' },
        { id: 'z', severity: 'ok', area: 'disk', title: 'Z' },
      ],
    } satisfies HealthReport);
    expect(full.top.map((t) => t.id)).toEqual(['x', 'y']);
    expect(full.warnings).toBe(1);
    const same = mergeStatus(full, {
      errors: 1,
      warnings: 0,
      checked_at: 'b',
      refreshing: false,
      top: [],
    });
    expect(same.top).toHaveLength(2);
    expect(same.checked_at).toBe('b');
    const changed = mergeStatus(full, {
      errors: 3,
      warnings: 0,
      checked_at: 'c',
      refreshing: false,
      top: [],
    });
    expect(changed.top).toEqual([]);
    expect(changed.errors).toBe(3);
  });
});
