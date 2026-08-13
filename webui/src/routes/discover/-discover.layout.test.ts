import { describe, expect, it } from 'vitest';

import type { DiscoverSectionId } from './-discover.layout';

import {
  DISCOVER_LAYOUT,
  SECTION_EMPTY_POLICY,
  buildLayoutRows,
  isSectionVisible,
} from './-discover.layout';

/** Treat every listed id as having content. */
const only =
  (...ids: DiscoverSectionId[]) =>
  (id: DiscoverSectionId) =>
    ids.includes(id);
const all = () => true;
const none = () => false;

describe('the section order', () => {
  it('matches the vanilla LAYOUT, top to bottom', () => {
    // Pinned verbatim. The vanilla moved DOM nodes to achieve this; React
    // renders it directly — but the ORDER is the observable behaviour and it
    // must not drift.
    // The streaming-home order: most personal first, browse + tools last
    // (was the vanilla's order until the Aug 2026 redesign).
    const flat = DISCOVER_LAYOUT.flatMap((e) => (e.kind === 'single' ? [e.id] : e.ids));
    expect(flat).toEqual([
      'your-mixes-section',
      'adv-wave',
      'listening-recs-section',
      'recommended-artists-section',
      'discover-bylt-sections',
      'recent-releases',
      'cache-genre-releases',
      'seasonal-albums-section',
      'cache-undiscovered',
      'cache-label-explorer',
      'your-albums-section',
      'your-artists-section',
      'year-mixes-section',
      'cache-deep-cuts',
      'cache-genre-explorer',
      'lastfm-radio',
      'listenbrainz',
      'build-a-playlist',
    ]);
  });

  it('keeps the dial directly above the two sections it drives', () => {
    // Deliberate adjacency: dragging the dial should visibly change both of
    // its targets without scrolling.
    const i = DISCOVER_LAYOUT.findIndex((e) => e.kind === 'single' && e.id === 'adv-wave');
    const next = DISCOVER_LAYOUT[i + 1];
    expect(next).toEqual({
      kind: 'pair',
      ids: ['listening-recs-section', 'recommended-artists-section'],
    });
  });

  it('has exactly the four pairs the vanilla had', () => {
    expect(DISCOVER_LAYOUT.filter((e) => e.kind === 'pair')).toHaveLength(4);
  });
});

describe('buildLayoutRows', () => {
  it('renders nothing when no section has content', () => {
    expect(buildLayoutRows(none)).toEqual([]);
  });

  it('renders every row when everything has content', () => {
    const rows = buildLayoutRows(all);
    expect(rows.filter((r) => r.kind === 'two-col')).toHaveLength(4);
    // pin-ok: the RELATIONSHIP is the assertion — one row per layout entry —
    // so it should track the layout rather than a frozen number.
    expect(rows).toHaveLength(DISCOVER_LAYOUT.length);
  });

  it('skips a single section with no content', () => {
    const rows = buildLayoutRows(only('your-artists-section'));
    expect(rows).toEqual([{ kind: 'full', id: 'your-artists-section' }]);
  });

  it('pairs two populated siblings into one two-column row', () => {
    const rows = buildLayoutRows(only('listening-recs-section', 'recommended-artists-section'));
    expect(rows).toEqual([
      { kind: 'two-col', ids: ['listening-recs-section', 'recommended-artists-section'] },
    ]);
  });

  it('promotes a LONE pair member to full width', () => {
    // The rule that matters: a single card stretched across a 2-col grid looks
    // broken, so the vanilla special-cased it. Both sides of the pair.
    expect(buildLayoutRows(only('listening-recs-section'))).toEqual([
      { kind: 'full', id: 'listening-recs-section' },
    ]);
    expect(buildLayoutRows(only('recommended-artists-section'))).toEqual([
      { kind: 'full', id: 'recommended-artists-section' },
    ]);
  });

  it('keeps a promoted member in its pair position, not at the end', () => {
    const rows = buildLayoutRows(
      only('cache-genre-explorer', 'cache-undiscovered', 'cache-deep-cuts'),
    );
    expect(rows).toEqual([
      { kind: 'full', id: 'cache-undiscovered' }, //  promoted from its pair, in place
      { kind: 'full', id: 'cache-deep-cuts' },
      { kind: 'full', id: 'cache-genre-explorer' },
    ]);
  });

  it('drops a pair entirely when neither member has content', () => {
    const rows = buildLayoutRows(only('cache-genre-explorer', 'cache-deep-cuts'));
    expect(rows.some((r) => r.kind === 'two-col')).toBe(false);
    expect(rows).toHaveLength(2);
  });

  it('preserves left/right order within a pair', () => {
    const rows = buildLayoutRows(only('cache-label-explorer', 'your-albums-section'));
    expect(rows[0]).toEqual({
      kind: 'two-col',
      ids: ['cache-label-explorer', 'your-albums-section'],
    });
  });

  it('is stable across repeat calls', () => {
    // The vanilla had to unwrap its own previous 2-col rows before re-running,
    // or repeat navigations nested them. A pure function cannot have that bug,
    // and this pins it.
    const first = buildLayoutRows(all);
    const second = buildLayoutRows(all);
    expect(second).toEqual(first);
  });
});

describe('the empty policy is NOT uniform', () => {
  it('only the four sections that opted in actually vanish', () => {
    // createDiscoverSectionController defaults to hideWhenEmpty:false with
    // renderEmptyState:true. Treating every empty shelf as "hide" silently
    // deletes messages that tell the user what to do next.
    const hiders = Object.entries(SECTION_EMPTY_POLICY)
      .filter(([, p]) => p?.kind === 'hide')
      .map(([id]) => id)
      .sort();
    expect(hiders).toEqual([
      'cache-deep-cuts',
      'cache-genre-explorer',
      'cache-genre-releases',
      'cache-label-explorer',
      'cache-undiscovered',
      'listening-recs-section',
      'recommended-artists-section',
      'your-albums-section',
      'your-artists-section',
    ]);
  });

  it('keeps the vanilla empty messages verbatim', () => {
    expect(SECTION_EMPTY_POLICY['recent-releases']).toEqual({
      kind: 'empty-state',
      message: 'No recent releases found',
    });
    expect(SECTION_EMPTY_POLICY['seasonal-albums-section']).toEqual({
      kind: 'empty-state',
      message: 'No seasonal albums found',
    });
  });
});

describe('isSectionVisible', () => {
  it('shows anything that has rows', () => {
    expect(isSectionVisible('your-albums-section', true, true)).toBe(true);
    expect(isSectionVisible('recent-releases', true, true)).toBe(true);
  });

  it('hides an opted-in section when empty, even after loading', () => {
    expect(isSectionVisible('your-albums-section', false, true)).toBe(false);
    expect(isSectionVisible('listening-recs-section', false, true)).toBe(false);
  });

  it('KEEPS an empty-state section visible when empty, so its message shows', () => {
    // The bug this exists to prevent: hiding these makes "No recent releases
    // found" disappear instead of explaining itself.
    expect(isSectionVisible('recent-releases', false, true)).toBe(true);
    expect(isSectionVisible('seasonal-albums-section', false, true)).toBe(true);
  });

  it('does not show an empty-state section before its data has loaded', () => {
    // The vanilla bails before showing the section when the fetch fails or
    // there is nothing at all (no current season) — no empty frame on load.
    expect(isSectionVisible('recent-releases', false, false)).toBe(false);
    expect(isSectionVisible('seasonal-albums-section', false, false)).toBe(false);
  });

  it('always shows the dial regardless of data or load state', () => {
    expect(isSectionVisible('adv-wave', false, false)).toBe(true);
  });

  it('keeps unported sections out of the layout entirely', () => {
    for (const id of ['lastfm-radio', 'listenbrainz', 'build-a-playlist'] as const) {
      expect(isSectionVisible(id, false, true)).toBe(false);
    }
  });
});
