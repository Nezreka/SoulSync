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
export const DISCOVER_LAYOUT: DiscoverLayoutEntry[] = [
  single('cache-genre-explorer'), //                                  quick browse
  single('your-mixes-section'), //                                    made for you
  single('year-mixes-section'),
  single('adv-wave'), //                                              the dial
  pair('listening-recs-section', 'recommended-artists-section'), //   its two targets
  pair('recent-releases', 'cache-genre-releases'), //                 new
  pair('seasonal-albums-section', 'cache-undiscovered'),
  pair('cache-label-explorer', 'your-albums-section'),
  single('your-artists-section'), //                                  your library
  single('discover-bylt-sections'), //                                because you listen
  single('cache-deep-cuts'),
  single('lastfm-radio'), //                                          stations & tools
  single('listenbrainz'),
  single('build-a-playlist'),
];

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
