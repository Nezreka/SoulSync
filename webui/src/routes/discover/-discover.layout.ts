/**
 * The discover page's section order, as data.
 *
 * ── Why this file exists instead of a port of _reorderDiscoverSections ──────
 *
 * The vanilla authored its sections in code-order, and several injected
 * themselves into a mid-page sub-container, so the page rendered as a jumble.
 * It fixed that AFTER the fact: `_reorderDiscoverSections()` walked a LAYOUT
 * array and physically moved ~20 already-rendered DOM nodes into place with
 * appendChild, on every load and every navigation, unwrapping its own previous
 * two-column rows first so repeat runs did not nest or duplicate them.
 *
 * React renders in declaration order, so none of that machinery is needed. What
 * MUST survive is the outcome — the order, and the pairing rule — so the order
 * is preserved here verbatim and the pairing rule becomes a pure function that
 * can actually be tested. That is the whole of `_reorderDiscoverSections`'s
 * observable behaviour, minus the DOM churn.
 */

/** Stable id for each section, matching the vanilla's DOM ids where it had them. */
export type DiscoverSectionId =
  | 'cache-genre-explorer'
  | 'your-mixes-section'
  | 'year-mixes-section'
  | 'adv-wave'
  | 'listening-recs-section'
  | 'recommended-artists-section'
  | 'recent-releases'
  | 'cache-genre-releases'
  | 'seasonal-albums-section'
  | 'cache-undiscovered'
  | 'cache-label-explorer'
  | 'your-albums-section'
  | 'your-artists-section'
  | 'discover-bylt-sections'
  | 'cache-deep-cuts'
  | 'lastfm-radio'
  | 'listenbrainz'
  | 'deezer-editorial'
  | 'build-a-playlist';

/** One entry: a full-width section, or a pair that renders two-up when both have content. */
export type DiscoverLayoutEntry =
  | { kind: 'single'; id: DiscoverSectionId }
  | { kind: 'pair'; ids: [DiscoverSectionId, DiscoverSectionId] };

const single = (id: DiscoverSectionId): DiscoverLayoutEntry => ({ kind: 'single', id });
const pair = (a: DiscoverSectionId, b: DiscoverSectionId): DiscoverLayoutEntry => ({
  kind: 'pair',
  ids: [a, b],
});

/**
 * Top → bottom, copied from the vanilla's LAYOUT array including its grouping
 * comments. The dial sits directly above the two sections it affects so the
 * user sees both react to a drag — that adjacency is deliberate, not incidental.
 */
/**
 * The streaming-home order (Boulder: 'the data displayed the way spotify /
 * itunes / deezer would'): the most PERSONAL rows lead — made-for-you mixes,
 * the dial and its two recommendation targets, because-you-listen — then
 * what's NEW, then collection-completion rows (the thing no streaming
 * service can offer), then eras/browse, with stations and power tools last.
 * The old order led with genre browse tiles and buried
 * because-you-listen eleventh.
 */
export const DISCOVER_LAYOUT: DiscoverLayoutEntry[] = [
  single('your-mixes-section'), //                                    made for you
  single('adv-wave'), //                                              the dial
  pair('listening-recs-section', 'recommended-artists-section'), //   its two targets
  single('discover-bylt-sections'), //                                because you listen
  pair('recent-releases', 'cache-genre-releases'), //                 new
  pair('seasonal-albums-section', 'cache-undiscovered'), //           own the gaps
  pair('cache-label-explorer', 'your-albums-section'),
  single('your-artists-section'), //                                  your library
  single('year-mixes-section'), //                                    eras
  single('cache-deep-cuts'),
  single('cache-genre-explorer'), //                                  browse
  single('lastfm-radio'), //                                          stations & tools
  single('listenbrainz'),
  single('deezer-editorial'), //                          deezer's own editors
  single('build-a-playlist'),
];

/**
 * What a section does when it loads successfully but has no items.
 *
 * This is NOT uniform, and collapsing it loses real behaviour. The vanilla's
 * `createDiscoverSectionController` defaults to `hideWhenEmpty: false` with
 * `renderEmptyState: true` — so unless a section opts in, an empty result
 * renders an explanatory message rather than making the section vanish.
 *
 *   'hide'        the section disappears entirely (hideWhenEmpty: true)
 *   'empty-state' the section stays and shows `emptyMessage`
 *
 * Messages are verbatim from the vanilla configs — they are user-visible and
 * several of them tell the user what to DO ("run a watchlist scan"), which is
 * lost if the section just disappears.
 */
export type EmptyPolicy = { kind: 'hide' } | { kind: 'empty-state'; message: string };

const HIDE: EmptyPolicy = { kind: 'hide' };
const emptyState = (message: string): EmptyPolicy => ({ kind: 'empty-state', message });

/** The controller's own default, for sections that set no message. */
export const DEFAULT_EMPTY_MESSAGE = 'Nothing to show';

export const SECTION_EMPTY_POLICY: Partial<Record<DiscoverSectionId, EmptyPolicy>> = {
  // Made For You holds 'Build a mix', so it stays with nothing in it yet.
  'your-mixes-section': emptyState(
    'Your mixes show up here after a watchlist scan. You can build your own now.',
  ),
  // hideWhenEmpty: true — these vanish.
  'recommended-artists-section': HIDE,
  'listening-recs-section': HIDE,
  'your-albums-section': HIDE,
  'your-artists-section': HIDE,

  // hideWhenEmpty absent → the controller default (false): stay and explain.
  'recent-releases': emptyState('No recent releases found'),
  'seasonal-albums-section': emptyState('No seasonal albums found'),
  'discover-bylt-sections': emptyState(DEFAULT_EMPTY_MESSAGE),

  // The cache-* shelves do not use the controller at all: their loaders early-
  // return without ever creating the section, so an empty result means the
  // section never exists. Effectively 'hide'.
  'cache-genre-explorer': HIDE,
  'cache-genre-releases': HIDE,
  'cache-undiscovered': HIDE,
  'cache-label-explorer': HIDE,
  'cache-deep-cuts': HIDE,
};

/** Sections that render regardless of data — controls, not shelves. */
export const ALWAYS_VISIBLE: DiscoverSectionId[] = [
  'adv-wave',
  // Renders its own loading and empty states. Without this the section is
  // null for as long as the fetch takes - so the loading row could never
  // appear, and a slow or unreachable Deezer left no trace of the shelf at
  // all rather than a row that says what happened.
  'deezer-editorial',
];

/**
 * Should this section be in the layout at all?
 *
 * `hasItems` answers "did the shelf return rows"; this answers the different
 * question of whether the section renders — which for an 'empty-state' section
 * is true even with zero rows, as long as its data actually loaded.
 */
export function isSectionVisible(
  id: DiscoverSectionId,
  hasItems: boolean,
  loaded: boolean,
): boolean {
  if (ALWAYS_VISIBLE.includes(id)) return true;
  if (hasItems) return true;
  const policy = SECTION_EMPTY_POLICY[id];
  // Unknown sections (phases not yet ported) stay out of the layout.
  if (!policy) return false;
  // An empty-state section still needs its load to have COMPLETED — the
  // vanilla's loader bails before showing the section when the fetch fails or
  // there is nothing to show at all (no current season, say).
  return policy.kind === 'empty-state' && loaded;
}

/** A row ready to render: either one full-width section or a genuine two-up row. */
export type DiscoverLayoutRow =
  | { kind: 'full'; id: DiscoverSectionId }
  | { kind: 'two-col'; ids: [DiscoverSectionId, DiscoverSectionId] };
/**
 * Resolve the layout against which sections actually have content.
 *
 * Three rules, all of them the vanilla's:
 *
 *   • a section with no content is skipped entirely (the vanilla checked
 *     `style.display !== 'none'` and left hidden sections out of the order)
 *   • a pair with BOTH members present renders as a two-column row
 *   • a pair with only ONE member present renders that member FULL WIDTH, with
 *     no wrapper row — a lone card stretched across a 2-col grid looks broken,
 *     which is why the vanilla special-cased it
 */
export function buildLayoutRows(
  hasContent: (id: DiscoverSectionId) => boolean,
  layout: DiscoverLayoutEntry[] = DISCOVER_LAYOUT,
): DiscoverLayoutRow[] {
  const rows: DiscoverLayoutRow[] = [];
  for (const entry of layout) {
    if (entry.kind === 'single') {
      if (hasContent(entry.id)) rows.push({ kind: 'full', id: entry.id });
      continue;
    }
    const present = entry.ids.filter(hasContent);
    if (present.length === 2) rows.push({ kind: 'two-col', ids: entry.ids });
    else if (present.length === 1) rows.push({ kind: 'full', id: present[0] });
  }
  return rows;
}

/**
 * Customizable zones. The page's four zone containers, top to bottom.
 *
 * The section ids, zones and default order above are the render-side mirror;
 * `core/discovery/layout.py` is the single source of truth the API validates
 * and persists per-profile layouts against (GET/PUT /api/discover/layout).
 */

/** The four zone containers on the page. */
export type DiscoverZoneId = 'for-you' | 'new-missing' | 'library' | 'tools';

export const DISCOVER_ZONES: { id: DiscoverZoneId; label: string }[] = [
  { id: 'for-you', label: 'For You' },
  { id: 'new-missing', label: 'New & Missing' },
  { id: 'library', label: 'From Your Library' },
  { id: 'tools', label: 'Explore & Build' },
];

/** Where each section lives unless the profile's saved layout says otherwise. */
export const DEFAULT_SECTION_ZONE: Record<DiscoverSectionId, DiscoverZoneId> = {
  'your-mixes-section': 'for-you',
  'adv-wave': 'for-you',
  'listening-recs-section': 'for-you',
  'recommended-artists-section': 'for-you',
  'discover-bylt-sections': 'for-you',
  'recent-releases': 'new-missing',
  'cache-genre-releases': 'new-missing',
  'seasonal-albums-section': 'new-missing',
  'cache-undiscovered': 'new-missing',
  'cache-label-explorer': 'new-missing',
  'your-albums-section': 'new-missing',
  'your-artists-section': 'library',
  'year-mixes-section': 'library',
  'cache-deep-cuts': 'library',
  'cache-genre-explorer': 'tools',
  'lastfm-radio': 'tools',
  listenbrainz: 'tools',
  'deezer-editorial': 'tools',
  'build-a-playlist': 'tools',
};

/** One customizable layout entry, as GET /api/discover/layout answers. */
export interface DiscoverLayoutSection {
  id: DiscoverSectionId;
  zone: DiscoverZoneId;
  enabled: boolean;
  position: number;
}

/**
 * The default layout: no saved preferences, exactly the current page order,
 * every section enabled. The hook falls back to this while the fetch is
 * pending or has failed, so the page never renders without sections.
 */
export function defaultDiscoverLayout(): DiscoverLayoutSection[] {
  const entries: DiscoverLayoutSection[] = [];
  for (const zone of DISCOVER_ZONES) {
    let position = 0;
    for (const entry of DISCOVER_LAYOUT) {
      const ids = entry.kind === 'single' ? [entry.id] : entry.ids;
      for (const id of ids) {
        if (DEFAULT_SECTION_ZONE[id] === zone.id) {
          entries.push({ id, zone: zone.id, enabled: true, position });
          position += 1;
        }
      }
    }
  }
  return entries;
}

/**
 * Enabled section ids per zone, in position order. Unknown ids are dropped —
 * the server never sends them, and a stale cached payload must not take the
 * page down.
 */
export function layoutSectionsByZone(
  entries: DiscoverLayoutSection[],
): Record<DiscoverZoneId, DiscoverSectionId[]> {
  const byZone: Record<DiscoverZoneId, DiscoverLayoutSection[]> = {
    'for-you': [],
    'new-missing': [],
    library: [],
    tools: [],
  };
  for (const entry of entries) {
    if (!entry || !byZone[entry.zone] || !(entry.id in DEFAULT_SECTION_ZONE)) continue;
    if (entry.enabled) byZone[entry.zone].push(entry);
  }
  const out = {} as Record<DiscoverZoneId, DiscoverSectionId[]>;
  for (const zone of DISCOVER_ZONES) {
    out[zone.id] = byZone[zone.id].sort((a, b) => a.position - b.position).map((e) => e.id);
  }
  return out;
}
