import { z } from 'zod';

/**
 * Coerce a raw search value to a string.
 *
 * TanStack JSON-parses search values, so an all-digits filter arrives as a
 * NUMBER and a bare `z.string()` would throw SearchParamError and take the
 * route down. Only primitives are stringified — a hand-edited `?q[]=x` parses
 * to an object, which must read as absent rather than "[object Object]".
 */
function searchString(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return undefined;
}

// Which top-level view the page shows. The library is the browsable
// collection grid; the overview is the glanceable status dashboard.
export const automationsViews = ['overview', 'library'] as const;
export type AutomationsViewName = (typeof automationsViews)[number];

/**
 * Navigation collections for the library sidebar.
 *
 * Smart collections are fixed keys; user groups ride as `group:<name>`.
 * `guides` is the reference hub, not a set of automations.
 */
export const AUTOMATION_NAV_KEYS = [
  'all',
  'attention',
  'scheduled',
  'events',
  'off',
  'system',
  'ungrouped',
  'guides',
] as const;

/**
 * A legacy pre-overhaul `?health=` value and the smart collection that
 * replaced it. 'failing' and 'never' both lived under the old verdict strip's
 * "needs attention" idea; 'off' is the Switched off collection.
 */
const legacyHealthToNav: Record<string, string> = {
  failing: 'attention',
  never: 'attention',
  off: 'off',
};

function searchView(value: unknown): AutomationsViewName {
  const s = searchString(value);
  return (automationsViews as readonly string[]).includes(s ?? '')
    ? (s as AutomationsViewName)
    : 'overview';
}

// All three filter-bar controls, not just the text box. They were transient DOM
// state in the vanilla page — a reload dropped them — so putting them in the URL
// only adds state that used to be thrown away.
export const automationsSearchSchema = z
  .object({
    /** 'overview' = status dashboard, 'library' = the browsable collection grid. */
    view: z
      .preprocess((v) => searchView(v), z.enum(automationsViews))
      .default('overview')
      .catch('overview'),
    /**
     * The sidebar collection being browsed: a smart key ('all', 'attention',
     * 'scheduled', 'events', 'off', 'system', 'ungrouped', 'guides') or
     * `group:<name>` for a user group. Only meaningful when view=library.
     */
    nav: z
      .preprocess((v) => searchString(v) ?? 'all', z.string())
      .default('all')
      .catch('all'),
    q: z
      .preprocess((v) => searchString(v) ?? '', z.string())
      .default('')
      .catch(''),
    /** Raw trigger_type from the dropdown; '' means All Triggers. */
    trigger: z
      .preprocess((v) => searchString(v) ?? '', z.string())
      .default('')
      .catch(''),
    /** Raw action_type from the dropdown; '' means All Actions. */
    action: z
      .preprocess((v) => searchString(v) ?? '', z.string())
      .default('')
      .catch(''),
    /**
     * Legacy param from the pre-overhaul page, where the verdict strip linked
     * `?health=failing|never|off`. The overhaul replaced that strip with the
     * Needs attention / Switched off smart collections, so a bookmarked
     * health link rewrites to the equivalent collection below instead of
     * being silently dropped. Never read back out or written to the URL.
     */
    health: z
      .preprocess((v) => searchString(v) ?? '', z.string())
      .default('')
      .catch(''),
  })
  .transform(({ health, ...rest }) => {
    const legacy = health ? legacyHealthToNav[health] : undefined;
    // Only the untouched defaults rewrite: an explicit new-style view/nav
    // always wins over a stale health param sharing the URL.
    if (legacy && rest.view === 'overview' && rest.nav === 'all') {
      return { ...rest, view: 'library' as const, nav: legacy };
    }
    return rest;
  });

export type AutomationsSearch = z.infer<typeof automationsSearchSchema>;

/**
 * The vanilla filter bar hides itself below this many automations — the exact
 * test was `automations.length < 7` in the vanilla _initAutoFilterBar (since
 * deleted with the rest of the legacy list). Named so this cannot quietly
 * drift to "6+", which is what that code's own comment claimed it did.
 */
export const AUTO_FILTER_BAR_MIN = 7;

/**
 * One row from GET /api/automations.
 *
 * These are `automations` table rows passed through _hydrate_automation, which
 * JSON-parses the config columns and backfills `then_actions` from the legacy
 * notify_* pair. Fields are optional because the table grew by migration —
 * is_system, group_name, owned_by and then_actions were all added later, so an
 * older row can genuinely lack them.
 */
export interface Automation {
  id: number;
  name: string;
  enabled?: boolean | number;
  trigger_type?: string;
  trigger_config?: Record<string, unknown> | null;
  action_type?: string;
  action_config?: Record<string, unknown> | null;
  then_actions?: { type?: string; config?: Record<string, unknown> }[];
  last_run?: string | null;
  next_run?: string | null;
  run_count?: number;
  last_error?: string | null;
  /**
   * JSON-parsed by _hydrate_automation, so normally a dict of run facts. It is
   * NOT in _JSON_DEFAULT_DICT, so a malformed value hydrates to null rather
   * than {} — hence unknown rather than a record type.
   */
  last_result?: unknown;
  /** Seeded, undeletable automations. Rendered in the protected System section. */
  is_system?: boolean | number;
  /** User-defined grouping; drives the 📁 sections. */
  group_name?: string | null;
  /**
   * Which side owns the row. The engine is app-wide and both sides read the
   * SAME endpoint, so 'video' rows must be filtered out of the music page —
   * and vice versa on the video page.
   */
  owned_by?: string | null;
  profile_id?: number;
  created_at?: string | null;
  updated_at?: string | null;
}

/** GET /api/automations returns the bare array, or {error} on failure. */
export type AutomationsListResponse = Automation[] | { error?: string };

/** GET /api/automations/master — the per-side global pause. */
export interface AutomationsMasterState {
  music?: boolean;
  video?: boolean;
  error?: string;
}

/** GET /api/automations/progress — in-flight runs, keyed by automation id. */
export interface AutomationsProgressResponse {
  [key: string]: unknown;
  error?: string;
}

/** One entry in the builder palette. */
export interface AutomationBlockDef {
  type: string;
  label?: string;
}

/**
 * GET /api/automations/blocks. `_findBlockDef` searches these three categories
 * in order, so the label lookup below must too.
 */
export interface AutomationBlocks {
  triggers?: AutomationBlockDef[];
  actions?: AutomationBlockDef[];
  notifications?: AutomationBlockDef[];
}

export const BLOCK_CATEGORIES = ['triggers', 'actions', 'notifications'] as const;
