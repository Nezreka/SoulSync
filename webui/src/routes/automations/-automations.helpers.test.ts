import { describe, expect, it } from 'vitest';

import {
  attentionItems,
  attentionQueue,
  automationHealth,
  buildCollections,
  cadenceSeconds,
  collectionAutomations,
  collectionCount,
  collectionGroupName,
  collectionHealth,
  filterByHealth,
  filterAutomations,
  filterOptions,
  forMusicSide,
  librarySections,
  readAutomationsList,
  recentRuns,
  scheduleLanes,
  upcomingRuns,
} from './-automations.helpers';
import { type Automation } from './-automations.types';

function auto(over: Partial<Automation> & { id: number }): Automation {
  return { name: `auto-${over.id}`, ...over };
}

describe('readAutomationsList', () => {
  it('returns the array on success', () => {
    expect(readAutomationsList([auto({ id: 1 })])).toHaveLength(1);
  });

  it('reads an {error} payload as empty rather than throwing', () => {
    // The vanilla page rendered the empty state on error; it never blanked
    // the shell or threw past the handler.
    expect(readAutomationsList({ error: 'boom' })).toEqual([]);
    expect(readAutomationsList(undefined)).toEqual([]);
  });
});

describe('forMusicSide', () => {
  it("drops the video side's rows", () => {
    const rows = [
      auto({ id: 1 }),
      auto({ id: 2, owned_by: 'video' }),
      auto({ id: 3, owned_by: 'music' }),
    ];
    expect(forMusicSide(rows).map((a) => a.id)).toEqual([1, 3]);
  });

  it('keeps rows with no owner — pre-migration rows predate the column', () => {
    expect(forMusicSide([auto({ id: 1, owned_by: null })]).map((a) => a.id)).toEqual([1]);
  });
});

describe('buildCollections', () => {
  it('orders smart lenses, then operations and maintenance, then groups by name, then ungrouped', () => {
    const rows = [
      auto({ id: 1, group_name: 'Zed' }),
      auto({ id: 2, group_name: 'Alpha' }),
      auto({ id: 3, is_system: 1 }),
    ];
    const keys = buildCollections(rows).map((c) => c.key);
    expect(keys).toEqual([
      'all',
      'attention',
      'scheduled',
      'events',
      'off',
      'guides',
      'operations',
      'maintenance',
      'group:Alpha',
      'group:Zed',
      'ungrouped',
    ]);
  });

  it('carries the group name on group collections', () => {
    const defs = buildCollections([auto({ id: 1, group_name: 'Nightly' })]);
    const g = defs.find((d) => d.key === 'group:Nightly');
    expect(g?.kind).toBe('group');
    expect(g?.groupName).toBe('Nightly');
    expect(g?.label).toBe('Nightly');
  });
});

describe('collectionAutomations', () => {
  const rows = () => [
    auto({
      id: 1,
      is_system: 1,
      enabled: 1,
      trigger_type: 'schedule',
      action_type: 'process_wishlist',
    }),
    auto({ id: 2, group_name: 'Nightly', enabled: 1, trigger_type: 'track_downloaded' }),
    auto({ id: 3, group_name: 'Nightly', enabled: 0, trigger_type: 'schedule' }),
    auto({ id: 4, enabled: 1, trigger_type: 'schedule', last_error: 'boom' }),
    auto({ id: 5, enabled: 1, trigger_type: 'daily_time' }),
  ];

  it('all returns everything', () => {
    expect(collectionAutomations(rows(), 'all').map((a) => a.id)).toEqual([1, 2, 3, 4, 5]);
  });

  it('unknown keys fall back to all', () => {
    expect(collectionAutomations(rows(), 'nope')).toHaveLength(5);
  });

  it('splits timer triggers from event triggers', () => {
    expect(collectionAutomations(rows(), 'scheduled').map((a) => a.id)).toEqual([1, 3, 4, 5]);
    expect(collectionAutomations(rows(), 'events').map((a) => a.id)).toEqual([2]);
  });

  it('attention holds failing and never-run rows, worst first', () => {
    // id 4 failed; ids 1, 2, 5 are enabled and never ran; id 3 is off so it
    // is not a problem that it never ran. Failures lead, like the overview's
    // attention queue.
    expect(collectionAutomations(rows(), 'attention').map((a) => a.id)).toEqual([4, 1, 2, 5]);
  });

  it('off holds only disabled rows', () => {
    expect(collectionAutomations(rows(), 'off').map((a) => a.id)).toEqual([3]);
  });

  it('operations returns core operational system automations and maintenance returns cleanup/tool automations', () => {
    const testRows = [
      auto({ id: 1, is_system: 1, action_type: 'process_wishlist' }),
      auto({ id: 2, is_system: 1, action_type: 'sync_downloaded_playlists' }),
      auto({ id: 3, is_system: 1, action_type: 'clean_search_history' }),
      auto({ id: 4, is_system: 1, action_type: 'run_repair_job', owned_by: 'system:repair_job' }),
      auto({ id: 5, is_system: 0, action_type: 'process_wishlist' }),
    ];
    expect(collectionAutomations(testRows, 'operations').map((a) => a.id)).toEqual([1, 2]);
    expect(collectionAutomations(testRows, 'maintenance').map((a) => a.id)).toEqual([3, 4]);
  });

  it('system wins over group_name, like the old protected section did', () => {
    const withSystemGroup = [
      ...rows(),
      auto({ id: 6, is_system: 1, group_name: 'Nightly', action_type: 'clean_search_history' }),
    ];
    expect(collectionAutomations(withSystemGroup, 'system').map((a) => a.id)).toEqual([1, 6]);
    expect(collectionAutomations(withSystemGroup, 'group:Nightly').map((a) => a.id)).toEqual([
      2, 3,
    ]);
  });

  it('ungrouped holds user rows with no group', () => {
    expect(collectionAutomations(rows(), 'ungrouped').map((a) => a.id)).toEqual([4, 5]);
  });

  it('guides holds no automations — it renders reference content', () => {
    expect(collectionAutomations(rows(), 'guides')).toEqual([]);
  });
});

describe('collectionGroupName', () => {
  it('unwraps group: keys and returns null for smart keys', () => {
    expect(collectionGroupName('group:Nightly')).toBe('Nightly');
    expect(collectionGroupName('group:My Group 2')).toBe('My Group 2');
    expect(collectionGroupName('all')).toBeNull();
    expect(collectionGroupName('system')).toBeNull();
    expect(collectionGroupName('operations')).toBeNull();
    expect(collectionGroupName('maintenance')).toBeNull();
  });
});

describe('collectionHealth', () => {
  it('reports bad when anything is failing, warn when off or never ran', () => {
    expect(collectionHealth([auto({ id: 1, last_error: 'x' })], 'all')).toBe('bad');
    expect(collectionHealth([auto({ id: 1, enabled: 0 })], 'all')).toBe('warn');
    expect(
      collectionHealth([auto({ id: 1, enabled: 1, last_run: '2026-01-01 00:00:00' })], 'all'),
    ).toBe('ok');
    expect(collectionHealth([], 'all')).toBe('none');
  });
});

describe('collectionCount', () => {
  it('counts the collection, and null for guides', () => {
    const rows = [auto({ id: 1, enabled: 1 }), auto({ id: 2, enabled: 0 })];
    expect(collectionCount(rows, 'all')).toBe(2);
    expect(collectionCount(rows, 'off')).toBe(1);
    expect(collectionCount(rows, 'guides')).toBeNull();
  });
});

describe('upcomingRuns', () => {
  // next_run is a server timestamp string; parseServerTime handles the shape.
  const stamp = (h: number) => `2026-09-28 0${h}:00:00`;
  it('returns enabled timer rows with a next_run, soonest first', () => {
    const rows = [
      auto({ id: 1, enabled: 1, trigger_type: 'schedule', next_run: stamp(3) }),
      auto({ id: 2, enabled: 1, trigger_type: 'track_downloaded', next_run: stamp(1) }),
      auto({ id: 3, enabled: 1, trigger_type: 'daily_time', next_run: stamp(1) }),
      auto({ id: 4, enabled: 0, trigger_type: 'schedule', next_run: stamp(1) }),
      auto({ id: 5, enabled: 1, trigger_type: 'schedule' }),
    ];
    expect(upcomingRuns(rows, false).map((a) => a.id)).toEqual([3, 1]);
  });

  it('returns nothing while the side is paused', () => {
    const rows = [auto({ id: 1, enabled: 1, trigger_type: 'schedule', next_run: stamp(1) })];
    expect(upcomingRuns(rows, true)).toEqual([]);
  });
});

describe('recentRuns', () => {
  it('orders by last_run, newest first, skipping never-run rows', () => {
    const rows = [
      auto({ id: 1, last_run: '2026-09-28 01:00:00' }),
      auto({ id: 2 }),
      auto({ id: 3, last_run: '2026-09-28 03:00:00' }),
    ];
    expect(recentRuns(rows).map((a) => a.id)).toEqual([3, 1]);
  });
});

describe('attentionQueue', () => {
  it('orders failing before never-run', () => {
    const rows = [
      auto({ id: 1, enabled: 1 }),
      auto({ id: 2, enabled: 1, last_error: 'boom' }),
      auto({ id: 3, enabled: 0 }),
    ];
    expect(attentionQueue(rows).map((a) => a.id)).toEqual([2, 1]);
  });
});

describe('filterAutomations', () => {
  // The vanilla filter read the RENDERED label text off the card, so the tests
  // feed labels rather than raw types.
  const labelFor = (a: Automation) => ({
    trigger: a.trigger_type === 'schedule' ? 'Every 6 hours' : 'New Release Found',
    action: a.action_type === 'process_wishlist' ? 'Process Wishlist' : 'Scan Library',
  });
  const rows = [
    auto({ id: 1, name: 'Nightly', trigger_type: 'schedule', action_type: 'process_wishlist' }),
    auto({
      id: 2,
      name: 'Scan',
      trigger_type: 'watchlist_new_release',
      action_type: 'scan_library',
    }),
    auto({
      id: 3,
      name: 'Other',
      trigger_type: 'schedule',
      action_type: 'scan_library',
      group_name: 'Chores',
    }),
  ];
  const ids = (f: Parameters<typeof filterAutomations>[1]) =>
    filterAutomations(rows, f, labelFor).map((a) => a.id);

  it('returns everything when nothing is set', () => {
    expect(ids({})).toEqual([1, 2, 3]);
    expect(ids({ q: '   ' })).toEqual([1, 2, 3]);
  });

  it('matches the rendered label, not the raw type', () => {
    // 'process wishlist' with a SPACE only exists in the label; the raw type is
    // process_wishlist, so a raw-type match would miss this.
    expect(ids({ q: 'process wishlist' })).toEqual([1]);
    expect(ids({ q: 'new release' })).toEqual([2]);
  });

  it('matches the name case-insensitively', () => {
    expect(ids({ q: 'NIGHTLY' })).toEqual([1]);
  });

  it('does NOT search group names, matching the vanilla filter', () => {
    expect(ids({ q: 'chores' })).toEqual([]);
  });

  it('matches the dropdowns on the exact raw type', () => {
    expect(ids({ trigger: 'schedule' })).toEqual([1, 3]);
    expect(ids({ action: 'scan_library' })).toEqual([2, 3]);
  });

  it('ANDs the three controls together', () => {
    expect(ids({ q: 'other', trigger: 'schedule', action: 'scan_library' })).toEqual([3]);
    expect(ids({ q: 'other', trigger: 'watchlist_new_release' })).toEqual([]);
  });
});

describe('filterOptions', () => {
  it('lists distinct types, sorted, ignoring blanks', () => {
    const out = filterOptions([
      auto({ id: 1, trigger_type: 'schedule', action_type: 'scan_library' }),
      auto({ id: 2, trigger_type: 'app_started', action_type: 'scan_library' }),
      auto({ id: 3 }),
    ]);
    expect(out.triggers).toEqual(['app_started', 'schedule']);
    expect(out.actions).toEqual(['scan_library']);
  });
});

describe('automationHealth — the verdict, not an inventory', () => {
  const a = (over: Record<string, unknown>) => ({ id: 1, name: 'a', ...over }) as never;

  it('counts what is currently broken, not what ever broke', () => {
    // last_error is rewritten on every run, so a row that failed and then
    // succeeded has already cleared it. This is "broken now".
    const health = automationHealth(
      [a({ enabled: 1, last_error: 'boom' }), a({ enabled: 1, last_run: 'x' })],
      false,
    );
    expect(health.failing).toBe(1);
    expect(health.ok).toBe(false);
  });

  it('does not call a switched-off automation "never run"', () => {
    // It has not run because you did not want it to. Flagging that trains
    // people to ignore the number.
    const health = automationHealth([a({ enabled: 0 }), a({ enabled: 1 })], false);
    expect(health.neverRun).toBe(1);
    expect(health.armed).toBe(1);
  });

  it('is not ok while the side is paused, however healthy the rows are', () => {
    const rows = [a({ enabled: 1, last_run: 'x' })];
    expect(automationHealth(rows, false).ok).toBe(true);
    expect(automationHealth(rows, true).ok).toBe(false);
  });

  it('an empty page is ok rather than alarming', () => {
    expect(automationHealth([], false).ok).toBe(true);
  });
});

describe('filterByHealth', () => {
  const a = (over: Record<string, unknown>) => ({ id: 1, name: 'a', ...over }) as never;
  const rows = [
    a({ id: 1, enabled: 1, last_error: 'boom', last_run: 'x' }),
    a({ id: 2, enabled: 1 }),
    a({ id: 3, enabled: 0 }),
    a({ id: 4, enabled: 1, last_run: 'x' }),
  ];

  it('opens onto exactly the rows the counter counted', () => {
    // The chip and the lens share this helper on purpose — a strip that says
    // "1 failing" and filters to a different set is worse than no strip.
    const health = automationHealth(rows, false);
    expect(filterByHealth(rows, 'failing')).toHaveLength(health.failing);
    expect(filterByHealth(rows, 'never')).toHaveLength(health.neverRun);
  });

  it('never excludes a disabled row from the off lens', () => {
    expect(filterByHealth(rows, 'off').map((r) => (r as { id: number }).id)).toEqual([3]);
  });

  it('an unknown lens filters nothing rather than everything', () => {
    expect(filterByHealth(rows, 'nonsense')).toHaveLength(4);
  });
});

describe('librarySections', () => {
  const sys = (id: number, action: string, over: Partial<Automation> = {}) =>
    ({ id, name: `s${id}`, is_system: 1, action_type: action, ...over }) as Automation;

  it('orders the pipeline, the groups, the unfiled, then housekeeping', () => {
    const keys = librarySections([
      sys(1, 'backup_database'),
      sys(2, 'scan_watchlist'),
      { id: 3, name: 'a', group_name: 'Zeta' },
      { id: 4, name: 'b', group_name: 'Alpha' },
      { id: 5, name: 'c' },
    ]).map((c) => c.key);
    expect(keys).toEqual(['operations', 'group:Alpha', 'group:Zeta', 'ungrouped', 'maintenance']);
  });

  it('does not make a user group out of the name a system row is filed under', () => {
    const keys = librarySections([
      sys(1, 'run_repair_job', { group_name: 'Maintenance', owned_by: 'system:repair_job' }),
    ]).map((c) => c.key);
    // and the unfiled section stays, as the place to build a first one
    expect(keys).toEqual(['ungrouped', 'maintenance']);
  });

  it('leaves out a section with nothing in it once the user has automations', () => {
    const keys = librarySections([{ id: 3, name: 'a', group_name: 'Chores' }]).map((c) => c.key);
    expect(keys).toEqual(['group:Chores']);
  });
});

describe('scheduleLanes', () => {
  const NOW = Date.parse('2026-11-11T18:00:00Z');
  const at = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();
  const timer = (
    id: number,
    every: [number, string],
    next: number,
    over: Partial<Automation> = {},
  ) =>
    ({
      id,
      name: `t${id}`,
      enabled: 1,
      is_system: 1,
      action_type: 'scan_watchlist',
      trigger_type: 'schedule',
      trigger_config: { interval: every[0], unit: every[1] },
      next_run: at(next),
      ...over,
    }) as Automation;

  it('repeats a slow automation across the day and counts a fast one', () => {
    const [lane] = scheduleLanes(
      [timer(1, [8, 'hours'], 60), timer(2, [30, 'minutes'], 5)],
      false,
      NOW,
    );
    expect(lane.key).toBe('operations');
    expect(lane.count).toBe(2);
    expect(lane.frequent).toBe(1);
    expect(lane.dots.map((d) => Math.round(d.minute))).toEqual([60, 540, 1020]);
  });

  it('draws a weekly time once, at its next run', () => {
    const [lane] = scheduleLanes(
      [
        timer(1, [1, 'hours'], 120, {
          trigger_type: 'weekly_time',
          trigger_config: { time: '02:00' },
        }),
      ],
      false,
      NOW,
    );
    expect(lane.dots).toHaveLength(1);
  });

  it('steps apart runs that land together', () => {
    const [lane] = scheduleLanes(
      [
        timer(1, [1, 'days'], 300),
        timer(2, [1, 'days'], 305),
        timer(3, [1, 'days'], 310),
        timer(4, [1, 'days'], 900),
      ],
      false,
      NOW,
    );
    expect(lane.dots.map((d) => d.step)).toEqual([0, -1, 1, 0]);
  });

  it('puts the user’s own in their lane, named for the one group they are in', () => {
    const own = { is_system: 0, owned_by: 'auto_sync', group_name: 'Playlist Auto-Sync' };
    const lanes = scheduleLanes(
      [timer(1, [1, 'days'], 60), timer(2, [1, 'days'], 90, own)],
      false,
      NOW,
    );
    expect(lanes.map((l) => [l.key, l.label])).toEqual([
      ['operations', 'Operations'],
      ['own', 'Playlist Auto-Sync'],
    ]);
    const mixed = scheduleLanes(
      [timer(2, [1, 'days'], 90, own), timer(3, [1, 'days'], 90, { is_system: 0 })],
      false,
      NOW,
    );
    expect(mixed[0].label).toBe('Yours');
  });

  it('schedules nothing while paused, and nothing that is off or past the window', () => {
    expect(scheduleLanes([timer(1, [1, 'days'], 60)], true, NOW)).toEqual([]);
    expect(scheduleLanes([timer(1, [1, 'days'], 60, { enabled: 0 })], false, NOW)).toEqual([]);
    const [lane] = scheduleLanes([timer(1, [3, 'days'], 2000)], false, NOW);
    expect(lane.dots).toEqual([]);
  });

  it('puts an overdue run at now rather than off the left edge', () => {
    const [lane] = scheduleLanes([timer(1, [1, 'days'], -30)], false, NOW);
    expect(lane.dots[0].minute).toBe(0);
  });
});

describe('attentionItems', () => {
  const ran = { last_run: '2026-01-01 00:00:00' };

  it('leads with the last-run failures, then the ones failing lately, then the idle', () => {
    const items = attentionItems(
      [
        { id: 1, name: 'Idle', enabled: 1 },
        { id: 2, name: 'Flaky', ...ran },
        { id: 3, name: 'Broken', last_error: 'boom', ...ran },
        { id: 4, name: 'Fine', ...ran },
        { id: 5, name: 'Off, never ran', enabled: 0 },
      ],
      [
        {
          automation_id: 2,
          name: 'Flaky',
          runs: 24,
          failed: 23,
          skipped: 0,
          avg_seconds: 0,
          last_error: 'Reconnect MetaSync.',
        },
        { automation_id: 3, name: 'Broken', runs: 4, failed: 1, skipped: 0, avg_seconds: 0 },
        { automation_id: 4, name: 'Fine', runs: 9, failed: 0, skipped: 2, avg_seconds: 0 },
      ],
    );
    expect(items.map((i) => [i.automation.name, i.kind, i.text])).toEqual([
      ['Broken', 'failed', 'Failed 1 of its last 4 runs. boom'],
      ['Flaky', 'flaky', 'Failed 23 of its last 24 runs. Reconnect MetaSync.'],
      ['Idle', 'idle', 'On, and never ran'],
    ]);
  });

  it('works from the last run alone until the history arrives', () => {
    const items = attentionItems([{ id: 3, name: 'Broken', last_error: 'boom', ...ran }]);
    expect(items[0].text).toBe('Failed: boom');
  });

  it('puts the one that failed most first among the flaky', () => {
    const row = (id: number, failed: number) => ({
      automation_id: id,
      name: '',
      runs: 10,
      failed,
      skipped: 0,
      avg_seconds: 0,
    });
    const items = attentionItems(
      [
        { id: 1, name: 'A', ...ran },
        { id: 2, name: 'B', ...ran },
      ],
      [row(1, 1), row(2, 7)],
    );
    expect(items.map((i) => i.automation.name)).toEqual(['B', 'A']);
    expect(items[1].text).toBe('Failed 1 of its last 10 runs');
  });
});

describe('cadenceSeconds', () => {
  it('reads the beat of an interval and of a daily time, and none for the rest', () => {
    const a = (trigger_type: string, trigger_config = {}) =>
      ({ id: 1, name: 'x', trigger_type, trigger_config }) as Automation;
    expect(cadenceSeconds(a('schedule', { interval: 30, unit: 'minutes' }))).toBe(1800);
    expect(cadenceSeconds(a('schedule', { interval: 2, unit: 'days' }))).toBe(172800);
    expect(cadenceSeconds(a('daily_time', { time: '03:00' }))).toBe(86400);
    expect(cadenceSeconds(a('weekly_time'))).toBeNull();
    expect(cadenceSeconds(a('batch_complete'))).toBeNull();
    expect(cadenceSeconds(a('schedule', { interval: 0, unit: 'hours' }))).toBeNull();
  });
});
