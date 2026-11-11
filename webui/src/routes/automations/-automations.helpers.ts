import { isTimerTrigger, parseServerTime } from './-automations.format';
import {
  type Automation,
  type AutomationActivityRow,
  type AutomationsListResponse,
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
  return automations.filter(
    (a) => a.owned_by !== 'video' && (!a.action_type || !a.action_type.startsWith('video_')),
  );
}

/**
 * Maintenance and repair actions that belong to the "Maintenance" collection
 * rather than core "Operations". Includes all Tools-page repair jobs, cleanup passes,
 * backups, deep scans, and metadata cache refreshes.
 */
export const MAINTENANCE_ACTIONS = new Set([
  'run_repair_job',
  'video_run_repair_job',
  'clean_search_history',
  'video_clean_search_history',
  'clean_completed_downloads',
  'video_clean_completed_downloads',
  'backup_database',
  'video_backup_database',
  'full_cleanup',
  'video_full_cleanup',
  'library_cleanup',
  'deep_scan_library',
  'video_deep_scan_movies',
  'video_deep_scan_tv',
  'refresh_beatport_cache',
  'seeding_sweep',
  'video_seeding_sweep',
  'import_lastfm_listening',
  'import_listenbrainz_listening',
  'audiobook_purge_recycle',
  'video_clean_youtube_episodes',
  'video_clean_plex_images',
  'video_purge_recycle_bin',
  'video_reenrich_stale',
]);

export function isMaintenanceAutomation(a: Automation): boolean {
  if (a.owned_by === 'system:repair_job') return true;
  return Boolean(a.action_type && MAINTENANCE_ACTIONS.has(a.action_type));
}

export function isOperationAutomation(a: Automation): boolean {
  return truthy(a.is_system) && !isMaintenanceAutomation(a);
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
  | 'operations'
  | 'maintenance'
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
    key: 'operations',
    kind: 'operations',
    label: 'Operations',
    description: 'Core pipeline automations (Wishlist, Watchlist, Scans, Syncs)',
  },
  {
    key: 'maintenance',
    kind: 'maintenance',
    label: 'Maintenance',
    description: 'Tools, cleanups, repair jobs, and backups',
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
 * then Operations & Maintenance under System, then user groups by name,
 * then the ungrouped remainder.
 */
export function buildCollections(automations: Automation[]): CollectionDef[] {
  // a system row can carry a group name too (the Tools jobs are filed under
  // "Maintenance"). it lives in its system section, so it names no user group
  const names = [
    ...new Set(
      automations
        .filter((a) => a.group_name && !truthy(a.is_system))
        .map((a) => a.group_name as string),
    ),
  ];
  names.sort();
  return [
    ...SMART_COLLECTIONS.filter(
      (c) => !['system', 'operations', 'maintenance', 'ungrouped'].includes(c.key),
    ),
    {
      key: 'operations',
      kind: 'operations',
      label: 'Operations',
      description: 'Core pipeline automations (Wishlist, Watchlist, Scans, Syncs)',
    },
    {
      key: 'maintenance',
      kind: 'maintenance',
      label: 'Maintenance',
      description: 'Tools, cleanups, repair jobs, and backups',
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
    case 'operations':
      return automations.filter((a) => isOperationAutomation(a));
    case 'maintenance':
      return automations.filter((a) => truthy(a.is_system) && isMaintenanceAutomation(a));
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

/**
 * The sections the library lists, top to bottom: the pipeline, the user's own
 * groups, their unfiled automations, then housekeeping. A section with nothing
 * in it is left out, except the unfiled one when the user has built nothing:
 * that empty section is where the invitation to build one sits.
 */
export function librarySections(automations: Automation[]): CollectionDef[] {
  const all = buildCollections(automations);
  const pick = (key: string) => all.find((c) => c.key === key) as CollectionDef;
  const groups = all.filter((c) => c.kind === 'group');
  const ordered = [pick('operations'), ...groups, pick('ungrouped'), pick('maintenance')];
  const hasOwn = automations.some((a) => !truthy(a.is_system));
  return ordered.filter(
    (c) =>
      collectionAutomations(automations, c.key).length > 0 || (c.key === 'ungrouped' && !hasOwn),
  );
}

/** Seconds between two runs of a timer automation, or null when it has no
 *  fixed beat (weekly and monthly times, events). */
export function cadenceSeconds(a: Automation): number | null {
  const config = (a.trigger_config ?? {}) as Record<string, unknown>;
  if (a.trigger_type === 'daily_time') return 86400;
  if (a.trigger_type !== 'schedule') return null;
  const n = Number(config.interval ?? 1);
  const per = { minutes: 60, hours: 3600, days: 86400 }[String(config.unit ?? 'hours')];
  return per && Number.isFinite(n) && n > 0 ? n * per : null;
}

export interface LaneDot {
  automation: Automation;
  /** minutes from now, 0 to the window's end */
  minute: number;
  /** vertical step so runs that land together do not hide each other */
  step: number;
}

export interface ScheduleLane {
  key: string;
  label: string;
  /** timer automations in this lane that are on */
  count: number;
  /** how many of them run more often than a dot can show */
  frequent: number;
  dots: LaneDot[];
}

/** Runs closer together than this are stepped apart. */
const LANE_CROWD_MINUTES = 16;
/** Anything running more often than this is a band, not a row of dots. */
export const LANE_FREQUENT_SECONDS = 3 * 3600;
const LANE_STEPS = [0, -1, 1, -2, 2];

/**
 * The next `hours` hours as lanes of runs: the pipeline, the user's own
 * automations, housekeeping. Each run of a slower automation is a dot at its
 * minute; the ones that run every hour or so are counted, not drawn, since
 * forty dots in a row say nothing a line does not.
 */
export function scheduleLanes(
  automations: Automation[],
  paused: boolean,
  now: number = Date.now(),
  hours = 24,
): ScheduleLane[] {
  const window = hours * 60;
  const armed = paused
    ? []
    : automations.filter(
        (a) => truthy(a.enabled) && isTimerTrigger(a.trigger_type) && Boolean(a.next_run),
      );
  const own = armed.filter((a) => !truthy(a.is_system));
  const ownGroups = [...new Set(own.map((a) => a.group_name || ''))];
  const lanes: { key: string; label: string; members: Automation[] }[] = [
    { key: 'operations', label: 'Operations', members: armed.filter(isOperationAutomation) },
    {
      key: 'own',
      label: ownGroups.length === 1 && ownGroups[0] ? ownGroups[0] : 'Yours',
      members: own,
    },
    {
      key: 'maintenance',
      label: 'Maintenance',
      members: armed.filter((a) => truthy(a.is_system) && isMaintenanceAutomation(a)),
    },
  ];
  return lanes
    .filter((lane) => lane.members.length > 0)
    .map((lane) => {
      const dots: LaneDot[] = [];
      let frequent = 0;
      for (const a of lane.members) {
        const every = cadenceSeconds(a);
        if (every !== null && every < LANE_FREQUENT_SECONDS) {
          frequent += 1;
          continue;
        }
        const first = Math.max(0, (parseServerTime(a.next_run as string) - now) / 60000);
        const stride = every === null ? Infinity : every / 60;
        for (let minute = first; minute <= window; minute += stride) {
          dots.push({ automation: a, minute, step: 0 });
        }
      }
      dots.sort((x, y) => x.minute - y.minute);
      let run = 0;
      dots.forEach((dot, i) => {
        run = i > 0 && dot.minute - dots[i - 1].minute < LANE_CROWD_MINUTES ? run + 1 : 0;
        dot.step = LANE_STEPS[run % LANE_STEPS.length];
      });
      return { key: lane.key, label: lane.label, count: lane.members.length, frequent, dots };
    });
}

export interface AttentionItem {
  automation: Automation;
  /** failed = its last run failed. flaky = it failed lately and has since
   *  run clean. idle = on, and never ran */
  kind: 'failed' | 'flaky' | 'idle';
  text: string;
}

/**
 * What wants a person, worst first.
 *
 * `last_error` only describes the LAST run, and one clean run clears it. An
 * automation that fails twenty three times and then works once read as
 * healthy, so the page said "All systems go" over a sync that had been broken
 * all day. The last 24 hours of history are counted as well.
 */
export function attentionItems(
  automations: Automation[],
  recent: AutomationActivityRow[] = [],
): AttentionItem[] {
  const lately = new Map(recent.map((r) => [r.automation_id, r]));
  const failed: AttentionItem[] = [];
  const flaky: (AttentionItem & { count: number })[] = [];
  const idle: AttentionItem[] = [];
  for (const a of automations) {
    const row = lately.get(a.id);
    const tally =
      row && row.failed > 0
        ? `Failed ${row.failed} of its last ${row.runs} ${row.runs === 1 ? 'run' : 'runs'}`
        : '';
    if (a.last_error) {
      failed.push({
        automation: a,
        kind: 'failed',
        text: tally ? `${tally}. ${a.last_error}` : `Failed: ${a.last_error}`,
      });
    } else if (row && row.failed > 0) {
      flaky.push({
        automation: a,
        kind: 'flaky',
        text: row.last_error ? `${tally}. ${row.last_error}` : tally,
        count: row.failed,
      });
    } else if (truthy(a.enabled) && !a.last_run) {
      idle.push({ automation: a, kind: 'idle', text: 'On, and never ran' });
    }
  }
  flaky.sort((x, y) => y.count - x.count);
  return [...failed, ...flaky.map(({ count: _count, ...item }) => item), ...idle];
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
