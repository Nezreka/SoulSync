/**
 * The five render states every discover section actually has.
 *
 * ── Why this file exists ────────────────────────────────────────────────────
 *
 * My first data layer gave each section two states: it either had rows or it
 * did not. That was wrong. `createDiscoverSectionController` has FIVE, and the
 * differences are all user-visible:
 *
 *   loading   spinner + the section's own copy ("Reading your listening...")
 *   rendered  the items
 *   empty     the section's own empty copy; section hides or stays per its flag
 *   stale     spinner + "still fetching" copy, AND starts a poller
 *   error     the section's own error copy, plus a toast on most sections
 *
 * Collapsing error into empty means a 500 shows "No recent releases found"
 * instead of "Failed to load recent releases", with no toast. Dropping stale
 * means the your-albums grid sits blank while its cache rebuilds instead of
 * saying so and polling.
 *
 * All copy below is verbatim from the vanilla section configs.
 */

import type { DiscoverSectionId } from './-discover.layout';

/** What the api layer hands back — success and failure are distinguishable. */
export type SectionOutcome<T> =
  | { kind: 'ok'; data: T }
  /** The request threw: HTTP not-ok, network, bad JSON. NOT `success: false`. */
  | { kind: 'error'; error: unknown };

export type SectionPhase = 'loading' | 'rendered' | 'empty' | 'stale' | 'error';

export interface SectionConfig {
  /** Spinner copy. Some sections deliberately have none. */
  loadingMessage?: string;
  /** Shown in the `empty` phase when the section does not hide. */
  emptyMessage?: string;
  /** Shown in the `error` phase. */
  errorMessage?: string;
  /** Fire a global toast on error as well as the in-section block. */
  showErrorToast?: boolean;
  /** Hide the whole section when empty rather than showing emptyMessage. */
  hideWhenEmpty?: boolean;
  /**
   * Override "is this empty?". Default is `items.length === 0`.
   * your-albums needs one because an empty grid mid-rebuild is not "empty".
   */
  isEmpty?: (items: unknown[], data: unknown) => boolean;
  /**
   * "Empty NOW, but upstream is still working." Wins over `isEmpty`.
   * Returning true also means the section should start polling.
   */
  isStale?: (items: unknown[], data: unknown) => boolean;
  /** Copy for the stale phase. */
  staleMessage?: string;
}

/**
 * Per-section config, transcribed from the vanilla's
 * createDiscoverSectionController calls.
 *
 * Only LIVE sections appear. decade-browser and genre-browser are omitted
 * deliberately — Discover 2.0 orphaned both, and porting their config would
 * resurrect dead UI.
 */
export const SECTION_CONFIG: Partial<Record<DiscoverSectionId, SectionConfig>> = {
  'recommended-artists-section': {
    loadingMessage: 'Finding recommendations...',
    emptyMessage: 'No recommendations yet — let the Similar Artists worker run',
    errorMessage: 'Failed to load recommendations',
    hideWhenEmpty: true,
    // No toast: there is no action the user could take, and the vanilla's
    // comment is explicit that such sections should not shout.
    showErrorToast: false,
  },
  'listening-recs-section': {
    loadingMessage: 'Reading your listening...',
    emptyMessage: 'Play more music and run a watchlist scan to see picks based on your listening',
    errorMessage: 'Failed to load listening recommendations',
    hideWhenEmpty: true,
    showErrorToast: false,
  },
  'recent-releases': {
    loadingMessage: 'Loading recent releases...',
    emptyMessage: 'No recent releases found',
    errorMessage: 'Failed to load recent releases',
    hideWhenEmpty: false,
    showErrorToast: true,
  },
  'your-albums-section': {
    emptyMessage: 'Nothing to show',
    errorMessage: 'Failed to load your albums',
    hideWhenEmpty: true,
    showErrorToast: true,
    staleMessage: 'Fetching your albums from connected services...',
    /**
     * Traced verbatim:
     *   isEmpty: total === 0 && !data.stale
     *   isStale: Boolean(data.stale) && total === 0
     * where `total` is data.stats.total — the UNFILTERED library count, not
     * `data.total`, which is the count after the status filter.
     */
    isEmpty: (_items, data) => {
      const d = data as { stats?: { total?: number }; stale?: boolean } | null;
      const total = (d && d.stats && d.stats.total) || 0;
      return total === 0 && !d?.stale;
    },
    isStale: (_items, data) => {
      const d = data as { stats?: { total?: number }; stale?: boolean } | null;
      const total = (d && d.stats && d.stats.total) || 0;
      return Boolean(d && d.stale) && total === 0;
    },
  },
  'your-artists-section': {
    loadingMessage: 'Loading your artists...',
    emptyMessage: 'No followed artists found',
    errorMessage: 'Failed to load your artists',
    hideWhenEmpty: true,
    showErrorToast: true,
  },
  'seasonal-albums-section': {
    emptyMessage: 'No seasonal albums found',
    errorMessage: 'Failed to load seasonal albums',
    hideWhenEmpty: false,
    showErrorToast: true,
  },
  /**
   * The cache-* shelves and the Time Machine hide when empty, but by a
   * DIFFERENT mechanism than hideWhenEmpty: their loaders early-return without
   * ever creating (or, for decades, after explicitly hiding) the section. Same
   * observable outcome, so they carry the flag here.
   */
  'cache-genre-explorer': { hideWhenEmpty: true },
  'cache-genre-releases': { hideWhenEmpty: true },
  'cache-undiscovered': { hideWhenEmpty: true },
  'cache-label-explorer': { hideWhenEmpty: true },
  'cache-deep-cuts': { hideWhenEmpty: true },
  'year-mixes-section': { hideWhenEmpty: true },

  'discover-bylt-sections': {
    // renderEmptyState:false in the vanilla — it blanks the container rather
    // than printing copy, so there is deliberately no emptyMessage.
    hideWhenEmpty: false,
    showErrorToast: false,
  },
};

/** The controller's defaults, for any section that sets none of its own. */
export const SECTION_DEFAULTS: Required<
  Pick<SectionConfig, 'loadingMessage' | 'emptyMessage' | 'errorMessage' | 'staleMessage'>
> = {
  loadingMessage: 'Loading...',
  emptyMessage: 'Nothing to show',
  errorMessage: 'Failed to load',
  staleMessage: 'Updating...',
};

export interface ResolvedSection<T> {
  phase: SectionPhase;
  data?: T;
  items: unknown[];
  /** The copy to show for this phase, already defaulted. */
  message?: string;
  /** True when the section should be omitted from the layout entirely. */
  hidden: boolean;
  /** True when the caller should start/keep a poller running. */
  shouldPoll: boolean;
  /** True when an error toast is due. */
  shouldToast: boolean;
}

/**
 * Resolve one section's render state.
 *
 * Order is the controller's, and it matters:
 *
 *   1. still in flight        -> loading
 *   2. threw                  -> error   (+ toast if configured)
 *   3. `success: false`       -> EMPTY, not error. The controller calls
 *                                _showEmpty() on a failed success gate.
 *   4. isStale                -> stale   (+ poll). Wins over empty.
 *   5. isEmpty                -> empty   (hidden if hideWhenEmpty)
 *   6. otherwise              -> rendered
 */
export function resolveSection<T>(
  id: DiscoverSectionId,
  outcome: SectionOutcome<T> | undefined,
  extractItems: (data: T) => unknown[],
  isPending: boolean,
): ResolvedSection<T> {
  const cfg = SECTION_CONFIG[id] ?? {};
  const base = { items: [] as unknown[], hidden: false, shouldPoll: false, shouldToast: false };

  if (isPending || !outcome) {
    return {
      ...base,
      phase: 'loading',
      message: cfg.loadingMessage ?? SECTION_DEFAULTS.loadingMessage,
    };
  }

  if (outcome.kind === 'error') {
    return {
      ...base,
      phase: 'error',
      message: cfg.errorMessage ?? SECTION_DEFAULTS.errorMessage,
      shouldToast: Boolean(cfg.showErrorToast),
    };
  }

  const data = outcome.data;
  const envelope = data as { success?: boolean } | null;

  // A failed success gate renders EMPTY, not error — _showEmpty() at the
  // controller's line 370. This is the case most easily got wrong.
  if (envelope && Object.prototype.hasOwnProperty.call(envelope, 'success') && !envelope.success) {
    return {
      ...base,
      phase: 'empty',
      data,
      message: cfg.emptyMessage ?? SECTION_DEFAULTS.emptyMessage,
      hidden: Boolean(cfg.hideWhenEmpty),
    };
  }

  const items = extractItems(data);
  const list = Array.isArray(items) ? items : [];

  if (cfg.isStale?.(list, data)) {
    return {
      ...base,
      phase: 'stale',
      data,
      items: list,
      message: cfg.staleMessage ?? SECTION_DEFAULTS.staleMessage,
      shouldPoll: true,
    };
  }

  const empty = cfg.isEmpty ? cfg.isEmpty(list, data) : list.length === 0;
  if (empty) {
    return {
      ...base,
      phase: 'empty',
      data,
      items: list,
      message: cfg.emptyMessage ?? SECTION_DEFAULTS.emptyMessage,
      hidden: Boolean(cfg.hideWhenEmpty),
    };
  }

  return { ...base, phase: 'rendered', data, items: list };
}

/**
 * The your-albums stale poller's shape, traced from `_pollYourAlbums`.
 *
 * 5s interval, at most 12 attempts (so it gives up after a minute), and it
 * stops as soon as `stats.total > 0` — then reloads the section.
 */
export const YOUR_ALBUMS_POLL_MS = 5000;
export const YOUR_ALBUMS_POLL_MAX_ATTEMPTS = 12;
