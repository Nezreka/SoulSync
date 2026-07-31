import { describe, expect, it } from 'vitest';

import type { DiscoverSectionId } from './-discover.layout';

import { DISCOVER_LAYOUT, buildLayoutRows } from './-discover.layout';

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
    const flat = DISCOVER_LAYOUT.flatMap((e) => (e.kind === 'single' ? [e.id] : e.ids));
    expect(flat).toEqual([
      'cache-genre-explorer',
      'your-mixes-section',
      'year-mixes-section',
      'adv-wave',
      'listening-recs-section',
      'recommended-artists-section',
      'recent-releases',
      'cache-genre-releases',
      'seasonal-albums-section',
      'cache-undiscovered',
      'cache-label-explorer',
      'your-albums-section',
      'your-artists-section',
      'discover-bylt-sections',
      'cache-deep-cuts',
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
      { kind: 'full', id: 'cache-genre-explorer' },
      { kind: 'full', id: 'cache-undiscovered' }, //  promoted from its pair, in place
      { kind: 'full', id: 'cache-deep-cuts' },
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
