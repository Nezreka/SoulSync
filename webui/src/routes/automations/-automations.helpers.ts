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

/**
 * The verdict the page opens with.
 *
 * The stats bar counted Active / System / Custom — how many rows are in a
 * table, which is a question nobody has. What a person comes to this page to
 * learn is whether automation is WORKING, and that has exactly three bad
 * answers: something failed, something has never run, or the whole side is
 * paused so nothing is running at all.
 *
 * `failing` counts the LAST run, not history: `last_error` is cleared on the
 * next successful run (update_automation_run writes it every time), so this
 * is "currently broken", not "has ever broken".
 *
 * `neverRun` deliberately excludes disabled rows. An automation you switched
 * off has not run because you did not want it to; calling that out as a
 * problem would train people to ignore the number.
 */
export function automationHealth(
  automations: Automation[],
  paused: boolean,
): { failing: number; neverRun: number; armed: number; paused: boolean; ok: boolean } {
  const enabled = automations.filter((a) => truthy(a.enabled));
  const failing = automations.filter((a) => Boolean(a.last_error)).length;
  const neverRun = enabled.filter((a) => !a.last_run).length;
  return {
    failing,
    neverRun,
    armed: enabled.length,
    paused,
    ok: failing === 0 && neverRun === 0 && !paused,
  };
}

/** Rows matching a health lens. Kept beside the counter so the number and the
 *  filter can never disagree about what they mean. */
export function filterByHealth(automations: Automation[], lens: string): Automation[] {
  if (lens === 'failing') return automations.filter((a) => Boolean(a.last_error));
  if (lens === 'never') return automations.filter((a) => truthy(a.enabled) && !a.last_run);
  if (lens === 'off') return automations.filter((a) => !truthy(a.enabled));
  return automations;
}

/**
 * The three filter-bar controls, applied together.
 *
 * The text box matches the RENDERED labels, not the raw types: _filterAutomations
 * read `.flow-trigger` / `.flow-action` textContent, so a user typing "process
 * wishlist" matches the label while the underlying `process_wishlist` is never
 * what they are aiming at. `labelFor` supplies those strings, keeping this pure
 * and DOM-free. Group names are deliberately NOT searched — the vanilla filter
 * did not, and silently widening the match would change what people find.
 *
 * The dropdowns compare the raw type exactly, as they did through the card's
 * data-trigger-type / data-action-type attributes.
 */
export function filterAutomations(
  automations: Automation[],
  filters: { q?: string; trigger?: string; action?: string; health?: string },
  labelFor: (a: Automation) => { trigger: string; action: string },
): Automation[] {
  const q = (filters.q ?? '').toLowerCase().trim();
  const trigger = filters.trigger ?? '';
  const action = filters.action ?? '';
  const health = filters.health ?? '';
  if (!q && !trigger && !action && !health) return automations;

  // The health lens runs first and through the same helper the counter uses,
  // so a chip that says "3 failing" can never open onto a different three.
  const scoped = health ? filterByHealth(automations, health) : automations;

  return scoped.filter((a) => {
    const labels = labelFor(a);
    const matchesQuery =
      !q ||
      (a.name ?? '').toLowerCase().includes(q) ||
      labels.trigger.toLowerCase().includes(q) ||
      labels.action.toLowerCase().includes(q);
    return (
      matchesQuery &&
      (!trigger || (a.trigger_type ?? '') === trigger) &&
      (!action || (a.action_type ?? '') === action)
    );
  });
}

/** Distinct trigger/action types, sorted — the two dropdowns' option lists. */
export function filterOptions(automations: Automation[]): {
  triggers: string[];
  actions: string[];
} {
  const triggers = [...new Set(automations.map((a) => a.trigger_type ?? ''))].filter(Boolean);
  const actions = [...new Set(automations.map((a) => a.action_type ?? ''))].filter(Boolean);
  triggers.sort();
  actions.sort();
  return { triggers, actions };
}

/**
 * A section's one-line status, so a COLLAPSED family still says something.
 *
 * Reads left to right in the order you would ask: is anything broken, is
 * anything running blind, is anything switched off. A family with none of
 * those says so rather than printing three zeros.
 */
export function sectionSummary(automations: Automation[]): string {
  if (automations.length === 0) return 'empty';
  const failing = automations.filter((a) => Boolean(a.last_error)).length;
  const off = automations.filter((a) => !truthy(a.enabled)).length;
  const neverRun = automations.filter((a) => truthy(a.enabled) && !a.last_run).length;
  const parts: string[] = [];
  if (failing) parts.push(`${failing} failing`);
  if (neverRun) parts.push(`${neverRun} never run`);
  if (off) parts.push(`${off} off`);
  if (parts.length === 0) parts.push('all healthy');
  return parts.join(' · ');
}

/**
 * The glow a family carries, as `R,G,B` for `--tile-glow`.
 *
 * System and My Automations are fixed — they are the same two families on
 * every install, and a colour that moved between installs would be noise.
 * User groups hash their NAME into the palette, so a group keeps its colour
 * across reloads and across machines without anything being stored.
 */
const FAMILY_PALETTE = [
  '56,189,248',
  '168,85,247',
  '34,197,94',
  '245,158,11',
  '244,114,182',
  '20,184,166',
];

export function sectionGlow(kind: 'system' | 'ungrouped' | 'group', name = ''): string {
  if (kind === 'system') return '148,163,184';
  // Indirection is legal in a custom property: --tile-glow: var(--accent-rgb)
  // resolves before rgba() reads it, so the user's own automations always
  // carry the accent they picked.
  if (kind === 'ungrouped') return 'var(--accent-rgb)';
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return FAMILY_PALETTE[hash % FAMILY_PALETTE.length];
}
