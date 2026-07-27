import {
  AUTO_FILTER_BAR_MIN,
  type Automation,
  type AutomationsListResponse,
  type AutomationsView,
} from './-automations.types';

/** `enabled` / `is_system` arrive as SQLite ints (0/1), not booleans. */
function truthy(value: boolean | number | null | undefined): boolean {
  return value === true || value === 1;
}

/**
 * Unwrap GET /api/automations.
 *
 * The endpoint returns a bare array on success and `{error}` on failure, and
 * the vanilla page treated any non-array as empty rather than throwing — a
 * broken automations list must not blank the page.
 */
export function readAutomationsList(payload: AutomationsListResponse | undefined): Automation[] {
  return Array.isArray(payload) ? payload : [];
}

/**
 * Drop the other side's rows.
 *
 * The automation engine is app-wide: music and video automations share one
 * table and ONE endpoint, distinguished only by `owned_by`. The music page
 * hides 'video' rows and the video page keeps only them. Anyone without the
 * video side simply has no such rows, so this is a no-op for them.
 */
export function forMusicSide(automations: Automation[]): Automation[] {
  return automations.filter((a) => a.owned_by !== 'video');
}

/**
 * Split the list the way the page renders it: System, then named groups in
 * name order, then the ungrouped remainder.
 *
 * Group order is `sort()` on the distinct names — the vanilla page sorted the
 * names, not the automations, so two groups keep API order internally.
 */
export function buildAutomationsView(automations: Automation[]): AutomationsView {
  const system = automations.filter((a) => truthy(a.is_system));
  const user = automations.filter((a) => !truthy(a.is_system));

  const names = [...new Set(user.filter((a) => a.group_name).map((a) => a.group_name as string))];
  names.sort();

  const groups = names
    .map((name) => ({ name, automations: user.filter((a) => a.group_name === name) }))
    // A name only reaches here by being present on a row, so this cannot drop
    // anything today; it mirrors the vanilla `if (groupAutos.length)` guard so
    // the behaviour survives if grouping ever changes.
    .filter((group) => group.automations.length > 0);

  return {
    system,
    groups,
    ungrouped: user.filter((a) => !a.group_name),
    stats: {
      active: automations.filter((a) => truthy(a.enabled)).length,
      system: system.length,
      custom: user.length,
      total: automations.length,
    },
    // Counted over the whole (music-side) list, not per section.
    showFilterBar: automations.length >= AUTO_FILTER_BAR_MIN,
  };
}

/** Filter-bar match: name, plus the action/trigger so "wishlist" finds handlers. */
export function filterAutomations(automations: Automation[], query: string): Automation[] {
  const needle = query.toLowerCase().trim();
  if (!needle) return automations;
  return automations.filter((a) =>
    [a.name, a.action_type, a.trigger_type, a.group_name].some((field) =>
      (field ?? '').toLowerCase().includes(needle),
    ),
  );
}
