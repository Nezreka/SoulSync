import { describe, expect, it } from 'vitest';

import {
  buildAutomationsView,
  automationHealth,
  sectionGlow,
  sectionSummary,
  filterByHealth,
  filterAutomations,
  filterOptions,
  forMusicSide,
  readAutomationsList,
} from './-automations.helpers';
import { AUTO_FILTER_BAR_MIN, type Automation } from './-automations.types';

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

describe('buildAutomationsView', () => {
  it('splits system from user automations', () => {
    const view = buildAutomationsView([auto({ id: 1, is_system: true }), auto({ id: 2 })]);
    expect(view.system.map((a) => a.id)).toEqual([1]);
    expect(view.ungrouped.map((a) => a.id)).toEqual([2]);
  });

  it('treats SQLite 1/0 as booleans', () => {
    // enabled and is_system are INTEGER columns; a plain truthiness check on
    // `=== true` would classify every real row as user-owned and disabled.
    const view = buildAutomationsView([
      auto({ id: 1, is_system: 1, enabled: 1 }),
      auto({ id: 2, is_system: 0, enabled: 0 }),
    ]);
    expect(view.system.map((a) => a.id)).toEqual([1]);
    expect(view.stats.active).toBe(1);
  });

  it('groups user automations by name, groups sorted', () => {
    const view = buildAutomationsView([
      auto({ id: 1, group_name: 'Zed' }),
      auto({ id: 2, group_name: 'Alpha' }),
      auto({ id: 3, group_name: 'Zed' }),
      auto({ id: 4 }),
    ]);
    expect(view.groups.map((g) => g.name)).toEqual(['Alpha', 'Zed']);
    // Order WITHIN a group is API order, because the vanilla page sorted the
    // distinct names and then filtered — it never re-sorted the rows.
    expect(view.groups[1].automations.map((a) => a.id)).toEqual([1, 3]);
    expect(view.ungrouped.map((a) => a.id)).toEqual([4]);
  });

  it('never files a system automation under a group', () => {
    // is_system wins: a seeded row carrying a group_name still belongs in the
    // protected System section, which is what stops it being editable.
    const view = buildAutomationsView([auto({ id: 1, is_system: 1, group_name: 'Alpha' })]);
    expect(view.system.map((a) => a.id)).toEqual([1]);
    expect(view.groups).toEqual([]);
  });

  it('counts stats over the whole list', () => {
    const view = buildAutomationsView([
      auto({ id: 1, is_system: 1, enabled: 1 }),
      auto({ id: 2, enabled: 1 }),
      auto({ id: 3, enabled: 0 }),
    ]);
    expect(view.stats).toEqual({ active: 2, system: 1, custom: 2, total: 3 });
  });

  it('shows the filter bar only at the vanilla threshold', () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => auto({ id: i }));
    expect(buildAutomationsView(many(AUTO_FILTER_BAR_MIN - 1)).showFilterBar).toBe(false);
    expect(buildAutomationsView(many(AUTO_FILTER_BAR_MIN)).showFilterBar).toBe(true);
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

describe('filterAutomations with a health lens', () => {
  const a = (over: Record<string, unknown>) => ({ id: 1, name: 'a', ...over }) as never;
  const labels = () => ({ trigger: '', action: '' });

  it('combines the lens with the text box', () => {
    const rows = [
      a({ id: 1, name: 'nightly', enabled: 1, last_error: 'boom' }),
      a({ id: 2, name: 'weekly', enabled: 1, last_error: 'boom' }),
      a({ id: 3, name: 'nightly', enabled: 1, last_run: 'x' }),
    ];
    const out = filterAutomations(rows, { q: 'night', health: 'failing' }, labels);
    expect(out.map((r) => (r as { id: number }).id)).toEqual([1]);
  });
});


describe('sectionSummary — a collapsed family still says something', () => {
  const a = (over: Record<string, unknown>) => ({ id: 1, name: 'a', ...over }) as never;

  it('leads with what is broken', () => {
    expect(
      sectionSummary([
        a({ enabled: 1, last_error: 'boom', last_run: 'x' }),
        a({ enabled: 1, last_run: 'x' }),
        a({ enabled: 0 }),
      ]),
    ).toBe('1 failing · 1 off');
  });

  it('says all healthy rather than printing three zeros', () => {
    expect(sectionSummary([a({ enabled: 1, last_run: 'x' })])).toBe('all healthy');
  });

  it('counts never-run only among enabled rows', () => {
    expect(sectionSummary([a({ enabled: 1 }), a({ enabled: 0 })])).toBe('1 never run · 1 off');
  });

  it('has a word for an empty family', () => {
    expect(sectionSummary([])).toBe('empty');
  });
});

describe('sectionGlow', () => {
  it('pins the two families that exist on every install', () => {
    expect(sectionGlow('system')).toBe('148,163,184');
    // The user's own automations carry the accent they chose.
    expect(sectionGlow('ungrouped')).toBe('var(--accent-rgb)');
  });

  it('gives a group the same colour every time, without storing anything', () => {
    expect(sectionGlow('group', 'Nightly')).toBe(sectionGlow('group', 'Nightly'));
    expect(sectionGlow('group', 'Nightly')).toMatch(/^\d+,\d+,\d+$/);
  });

  it('does not collapse every group onto one colour', () => {
    const names = ['Nightly', 'Weekend', 'Imports', 'Cleanup', 'Radio'];
    expect(new Set(names.map((n) => sectionGlow('group', n))).size).toBeGreaterThan(1);
  });
});
