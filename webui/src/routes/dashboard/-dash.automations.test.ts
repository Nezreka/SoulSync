/** The Automations card's pure core. */

import { describe, expect, it } from 'vitest';

import { automationCardRows, triggerLabel } from './-dash.automations';
import { parseDbUtc } from './-dash.autosync';

const NOW = Date.parse('2026-08-11T12:00:00Z');

describe('parseDbUtc', () => {
  it('reads bare db stamps as UTC — the countdown-skew fix', () => {
    expect(parseDbUtc('2026-08-11 13:00:00')).toBe(Date.parse('2026-08-11T13:00:00Z'));
    // ISO strings with timezone info pass through untouched.
    expect(parseDbUtc('2026-08-11T13:00:00+02:00')).toBe(Date.parse('2026-08-11T13:00:00+02:00'));
  });
});

describe('triggerLabel', () => {
  it('covers the engine trigger map and falls back to events', () => {
    expect(
      triggerLabel({
        id: 1,
        trigger_type: 'schedule',
        trigger_config: { interval: 6, unit: 'hours' },
      }),
    ).toBe('Every 6 hours');
    expect(
      triggerLabel({
        id: 1,
        trigger_type: 'schedule',
        trigger_config: '{"interval": 2, "unit": "days"}',
      }),
    ).toBe('Every 2 days');
    expect(
      triggerLabel({ id: 1, trigger_type: 'daily_time', trigger_config: { time: '04:30' } }),
    ).toBe('Daily @ 04:30');
    expect(
      triggerLabel({
        id: 1,
        trigger_type: 'weekly_time',
        trigger_config: { time: '09:00', days: ['mon'] },
      }),
    ).toBe('Mon @ 09:00');
    expect(
      triggerLabel({
        id: 1,
        trigger_type: 'monthly_time',
        trigger_config: { day: 15, time: '02:00' },
      }),
    ).toBe('Monthly · day 15 @ 02:00');
    expect(
      triggerLabel({
        id: 1,
        trigger_type: 'event',
        trigger_config: { event: 'watchlist.new_release' },
      }),
    ).toBe('On watchlist new release');
  });
});

describe('automationCardRows', () => {
  const rows = [
    {
      id: 1,
      name: 'Wishlist processing',
      enabled: 1,
      trigger_type: 'schedule',
      trigger_config: { interval: 6, unit: 'hours' },
      action_type: 'process_wishlist',
      next_run: '2026-08-11 14:00:00',
      last_run: '2026-08-11 08:00:00',
      run_count: 42,
      last_error: null,
    },
    {
      id: 2,
      name: 'Pipeline row',
      enabled: 1,
      trigger_type: 'schedule',
      action_type: 'playlist_pipeline',
      next_run: '2026-08-11 12:30:00',
    },
    {
      id: 5,
      name: 'Auto-Process Episode Wishlist',
      enabled: 1,
      trigger_type: 'schedule',
      action_type: 'video_process_episode_wishlist',
      next_run: '2026-08-11 12:10:00',
    },
    {
      id: 3,
      name: 'Nightly backup',
      enabled: 1,
      trigger_type: 'daily_time',
      trigger_config: { time: '03:00' },
      action_type: 'backup',
      next_run: '2026-08-11 13:00:00',
      last_run: '2026-08-10 13:00:00',
      run_count: 7,
      last_error: 'disk full',
    },
    {
      id: 4,
      name: 'Paused thing',
      enabled: 0,
      trigger_type: 'schedule',
      trigger_config: { interval: 1, unit: 'hours' },
      action_type: 'other',
      next_run: '2026-08-11 12:05:00',
    },
  ];

  it('excludes Sync-band rows, sorts soonest-first, disabled last', () => {
    const out = automationCardRows(rows, NOW);
    expect(out.map((r) => r.name)).toEqual([
      'Nightly backup', // 13:00 UTC — in 1h
      'Wishlist processing', // 14:00 UTC — in 2h
      'Paused thing', // disabled trails despite the soonest stamp
    ]);
    const [backup, wishlist, paused] = out;
    expect(wishlist.nextRun).toBe('in 2h');
    expect(wishlist.lastRun).toEqual({ ago: '4h ago', ok: true, error: null });
    expect(wishlist.runCount).toBe(42);
    // showAutomationHistory's third argument travels on the row.
    expect(wishlist.actionType).toBe('process_wishlist');
    expect(wishlist.running).toBeNull();
    expect(backup.lastRun?.ok).toBe(false);
    expect(backup.lastRun?.error).toBe('disk full');
    expect(paused.enabled).toBe(false);
    expect(paused.nextRun).toBeNull();
  });

  it('a progress entry marks its row running and it leads the sort', () => {
    const out = automationCardRows(rows, NOW, {
      '1': { status: 'running', phase: 'Processing wishlist...', progress: 37 },
      '3': { status: 'finished', phase: 'Done' }, // finished ≠ running
    });
    expect(out.map((r) => r.name)).toEqual([
      'Wishlist processing', // running leads despite the later stamp
      'Nightly backup',
      'Paused thing',
    ]);
    expect(out[0].running).toEqual({ phase: 'Processing wishlist...', progress: 37 });
    expect(out[1].running).toBeNull();
  });

  it('survives an empty list', () => {
    expect(automationCardRows([], NOW)).toEqual([]);
  });
});
