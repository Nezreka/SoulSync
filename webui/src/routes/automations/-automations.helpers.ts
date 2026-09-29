import { isTimerTrigger, parseServerTime } from './-automations.format';
import { type Automation, type AutomationsListResponse } from './-automations.types';

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
 * Navigation collections for the library sidebar.
 *
 * The page used to be one long scroll of sections — System, every group, then
 * the ungrouped remainder — and finding anything meant scrolling. The sidebar
 * turns those same families plus a few smart lenses into navigation, so each
 * is one click away and the main pane only ever shows one collection.
 *
 * Smart keys are fixed; user groups ride as `group:<name>`; `guides` is the
 * reference hub rather than a set of automations.
 */
export type CollectionKind =
  | 'all'
  | 'attention'
  | 'scheduled'
  | 'events'
  | 'off'
  | 'system'
  | 'ungrouped'
  | 'guides'
  | 'group';

export interface CollectionDef {
  key: string;
  kind: CollectionKind;
  /** User group name, only when kind === 'group'. */
  groupName?: string;
  label: string;
  /** One line under the library header saying what lives here. */
  description: string;
}

const SMART_COLLECTIONS: CollectionDef[] = [
  {
    key: 'all',
    kind: 'all',
    label: 'All automations',
    description: 'Everything on this side, in one place',
  },
  {
    key: 'attention',
    kind: 'attention',
    label: 'Needs attention',
    description: 'Failing runs and automations that never fired',
  },
  {
    key: 'scheduled',
    kind: 'scheduled',
    label: 'Scheduled',
    description: 'Timer-driven automations and their cadences',
  },
  {
    key: 'events',
    kind: 'events',
    label: 'Event-driven',
    description: 'Automations that listen for things happening',
  },
  {
    key: 'off',
    kind: 'off',
    label: 'Switched off',
    description: 'Disabled automations, kept around but idle',
  },
  {
    key: 'system',
    kind: 'system',
    label: 'System',
    description: 'Built-in automations that keep SoulSync healthy',
  },
  {
    key: 'ungrouped',
    kind: 'ungrouped',
    label: 'My Automations',
    description: 'Your automations, not filed into a group yet',
  },
  {
    key: 'guides',
    kind: 'guides',
    label: 'Guides & reference',
    description: 'Pipelines, recipes and block reference',
  },
];

function groupKey(name: string): string {
  return `group:${name}`;
}

/** The user group behind a `group:<name>` key, or null for smart keys. */
export function collectionGroupName(key: string): string | null {
  return key.startsWith('group:') ? key.slice('group:'.length) : null;
}

/**
 * Every collection the sidebar can show, in sidebar order: smart lenses,
 * then System, then user groups by name, then the ungrouped remainder.
 */
export function buildCollections(automations: Automation[]): CollectionDef[] {
  const names = [
    ...new Set(automations.filter((a) => a.group_name).map((a) => a.group_name as string)),
  ];
  names.sort();
  return [
    ...SMART_COLLECTIONS.filter((c) => c.key !== 'system' && c.key !== 'ungrouped'),
    {
      key: 'system',
      kind: 'system',
      label: 'System',
      description: 'Built-in automations that keep SoulSync healthy',
    },
    ...names.map((name) => ({
      key: groupKey(name),
      kind: 'group' as const,
      groupName: name,
      label: name,
      description: `Automations filed under ${name}`,
    })),
    {
      key: 'ungrouped',
      kind: 'ungrouped',
      label: 'My Automations',
      description: 'Your automations, not filed into a group yet',
    },
  ];
}

/**
 * The automations a collection holds, BEFORE any text/trigger/action filter.
 * `guides` holds none — it renders reference content, not cards.
 */
export function collectionAutomations(automations: Automation[], key: string): Automation[] {
  const groupName = collectionGroupName(key);
  if (groupName !== null) {
    return automations.filter((a) => !truthy(a.is_system) && a.group_name === groupName);
  }
  switch (key) {
    case 'attention': {
      // Worst first, matching the overview's attention queue; a row that both
      // failed and never ran appears once, under its failure.
      const failing = new Set(filterByHealth(automations, 'failing'));
      return [...failing, ...filterByHealth(automations, 'never').filter((a) => !failing.has(a))];
    }
    case 'scheduled':
      return automations.filter((a) => isTimerTrigger(a.trigger_type));
    case 'events':
      return automations.filter((a) => !isTimerTrigger(a.trigger_type));
    case 'off':
      return filterByHealth(automations, 'off');
    case 'system':
      return automations.filter((a) => truthy(a.is_system));
    case 'ungrouped':
      return automations.filter((a) => !truthy(a.is_system) && !a.group_name);
    case 'guides':
      return [];
    case 'all':
    default:
      return automations;
  }
}

/**
 * A collection's health at a glance, for the sidebar dot.
 * 'bad' = something failing, 'warn' = something off or never run, else 'ok'.
 */
export function collectionHealth(
  automations: Automation[],
  key: string,
): 'bad' | 'warn' | 'ok' | 'none' {
  const members = collectionAutomations(automations, key);
  if (members.length === 0) return 'none';
  if (members.some((a) => Boolean(a.last_error))) return 'bad';
  if (members.some((a) => !truthy(a.enabled) || (truthy(a.enabled) && !a.last_run))) return 'warn';
  return 'ok';
}

/** Count badge for a sidebar row. Attention shows its own count; guides none. */
export function collectionCount(automations: Automation[], key: string): number | null {
  if (key === 'guides') return null;
  return collectionAutomations(automations, key).length;
}

/**
 * What happens next: enabled timer automations with an armed next_run, soonest
 * first. The overview's "Up next" timeline. Paused sides and disabled rows
 * schedule nothing, so they are out; event-driven rows have no next run.
 */
export function upcomingRuns(automations: Automation[], paused: boolean): Automation[] {
  if (paused) return [];
  return automations
    .filter((a) => truthy(a.enabled) && isTimerTrigger(a.trigger_type) && Boolean(a.next_run))
    .sort((a, b) => parseServerTime(a.next_run as string) - parseServerTime(b.next_run as string));
}

/** Most recently run automations, newest first — the overview's activity feed. */
export function recentRuns(automations: Automation[]): Automation[] {
  return automations
    .filter((a) => Boolean(a.last_run))
    .sort((a, b) => parseServerTime(b.last_run as string) - parseServerTime(a.last_run as string));
}

/**
 * The queue of things that want a human: failing runs first (they are broken),
 * then enabled automations that never ran (they may be misconfigured).
 */
export function attentionQueue(automations: Automation[]): Automation[] {
  const failing = automations.filter((a) => Boolean(a.last_error));
  const neverRun = automations.filter((a) => !a.last_error && truthy(a.enabled) && !a.last_run);
  return [...failing, ...neverRun];
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
  filters: { q?: string; trigger?: string; action?: string },
  labelFor: (a: Automation) => { trigger: string; action: string },
): Automation[] {
  const q = (filters.q ?? '').toLowerCase().trim();
  const trigger = filters.trigger ?? '';
  const action = filters.action ?? '';
  if (!q && !trigger && !action) return automations;

  return automations.filter((a) => {
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
