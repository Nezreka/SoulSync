import { describe, expect, it } from 'vitest';

import {
  attentionQueue,
  automationHealth,
  buildCollections,
  collectionAutomations,
  collectionCount,
  collectionGroupName,
  collectionHealth,
  filterByHealth,
  filterAutomations,
  filterOptions,
  forMusicSide,
  readAutomationsList,
  recentRuns,
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
  it('orders smart lenses, then System, then groups by name, then ungrouped', () => {
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
      'system',
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
    auto({ id: 1, is_system: 1, enabled: 1, trigger_type: 'schedule' }),
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

  it('system wins over group_name, like the old protected section did', () => {
    const withSystemGroup = [...rows(), auto({ id: 6, is_system: 1, group_name: 'Nightly' })];
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
