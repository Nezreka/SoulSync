import { describe, expect, it } from 'vitest';

import {
  buildAutomationsView,
  filterAutomations,
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
  const rows = [
    auto({ id: 1, name: 'Nightly wishlist' }),
    auto({ id: 2, name: 'Scan', action_type: 'process_wishlist' }),
    auto({ id: 3, name: 'Other', trigger_type: 'schedule', group_name: 'Chores' }),
  ];

  it('returns everything for an empty or blank query', () => {
    expect(filterAutomations(rows, '')).toHaveLength(3);
    expect(filterAutomations(rows, '   ')).toHaveLength(3);
  });

  it('matches name, action, trigger and group, case-insensitively', () => {
    expect(filterAutomations(rows, 'WISHLIST').map((a) => a.id)).toEqual([1, 2]);
    expect(filterAutomations(rows, 'schedule').map((a) => a.id)).toEqual([3]);
    expect(filterAutomations(rows, 'chores').map((a) => a.id)).toEqual([3]);
  });

  it('does not crash on rows missing the optional columns', () => {
    expect(filterAutomations([auto({ id: 9, name: 'x' })], 'zzz')).toEqual([]);
  });
});
