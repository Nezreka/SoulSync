/**
 * The playlist library's filters.
 *
 * The classification rules are the substance here: which playlists count as
 * needing a human, which count as done, and — the one that decides whether the
 * filter is usable at all — which count as neither.
 */

import { describe, expect, it } from 'vitest';

import type { MirroredPlaylistRow } from './-sync.mirrored';

import type { LibraryFilter } from './-sync.library';

import {
  LIBRARY_FILTERS,
  libraryCardState,
  libraryCoveragePct,
  libraryDiscovered,
  libraryMissingCount,
  librarySortedRows,
  libraryMatchesFilter,
  libraryTotal,
  libraryFilterCounts,
  libraryIsComplete,
  libraryIsRunning,
  libraryNeedsAttention,
  librarySources,
  librarySummary,
  libraryVisibleFilters,
  libraryVisibleRows,
} from './-sync.library';

function row(over: Partial<MirroredPlaylistRow> = {}): MirroredPlaylistRow {
  return { id: 1, name: 'A', source: 'spotify', track_count: 10, ...over };
}

const NONE = new Set<string>();

describe('classification', () => {
  it('a short discovery needs attention', () => {
    expect(libraryNeedsAttention(row({ total_count: 10, discovered_count: 8 }))).toBe(true);
  });

  it('a pipeline error needs attention, whatever the counts say', () => {
    expect(
      libraryNeedsAttention(
        row({ total_count: 10, discovered_count: 10, pipeline_state: { status: 'error' } }),
      ),
    ).toBe(true);
    expect(
      libraryNeedsAttention(
        row({ total_count: 10, discovered_count: 10, pipeline_state: { error: 'boom' } }),
      ),
    ).toBe(true);
  });

  it('a playlist NOTHING has been attempted on is not "attention"', () => {
    // The rule that decides whether this filter is usable. Every freshly
    // mirrored playlist has discovered_count 0; calling them all problems
    // would make the tab useless on day one.
    expect(libraryNeedsAttention(row({ total_count: 10, discovered_count: 0 }))).toBe(false);
  });

  it('fully discovered is complete', () => {
    expect(libraryIsComplete(row({ total_count: 10, discovered_count: 10 }))).toBe(true);
  });

  it('over-discovered still counts as complete', () => {
    expect(libraryIsComplete(row({ total_count: 10, discovered_count: 12 }))).toBe(true);
  });

  it('an empty playlist is not complete — there is nothing to be complete about', () => {
    expect(libraryIsComplete(row({ track_count: 0, total_count: 0, discovered_count: 0 }))).toBe(
      false,
    );
  });

  it('running and complete are mutually exclusive', () => {
    const busy = row({
      total_count: 10,
      discovered_count: 10,
      pipeline_state: { status: 'running' },
    });
    expect(libraryIsRunning(busy)).toBe(true);
    expect(libraryIsComplete(busy)).toBe(false);
  });

  it('falls back to track_count when total_count is absent', () => {
    expect(libraryIsComplete(row({ track_count: 4, discovered_count: 4 }))).toBe(true);
  });

  it('a row in every state lands in exactly one of the three, never two', () => {
    const rows = [
      row({ id: 1, total_count: 10, discovered_count: 8 }),
      row({ id: 2, total_count: 10, discovered_count: 10 }),
      row({ id: 3, total_count: 10, discovered_count: 5, pipeline_state: { status: 'running' } }),
      row({ id: 4, total_count: 10, discovered_count: 0 }),
    ];
    for (const r of rows) {
      const hits = [libraryNeedsAttention(r), libraryIsRunning(r), libraryIsComplete(r)].filter(
        Boolean,
      );
      expect(hits.length).toBeLessThanOrEqual(1);
    }
  });
});

describe('filtering', () => {
  const rows = [
    row({ id: 1, source: 'spotify', total_count: 10, discovered_count: 10 }),
    row({ id: 2, source: 'tidal', total_count: 10, discovered_count: 6 }),
    row({ id: 3, source: 'spotify', pipeline_state: { status: 'running' } }),
  ];

  it('all returns everything', () => {
    expect(libraryVisibleRows(rows, 'all', NONE)).toHaveLength(3);
  });

  it('each state filter returns only its own rows', () => {
    expect(libraryVisibleRows(rows, 'synced', NONE).map((r) => r.id)).toEqual([1]);
    expect(libraryVisibleRows(rows, 'attention', NONE).map((r) => r.id)).toEqual([2]);
    expect(libraryVisibleRows(rows, 'running', NONE).map((r) => r.id)).toEqual([3]);
  });

  it('an empty source selection means NO source filter, not no rows', () => {
    // An empty chip row showing an empty library would read as data loss.
    expect(libraryVisibleRows(rows, 'all', new Set())).toHaveLength(3);
  });

  it('source and state filters compose', () => {
    expect(libraryVisibleRows(rows, 'all', new Set(['spotify'])).map((r) => r.id)).toEqual([1, 3]);
    expect(libraryVisibleRows(rows, 'synced', new Set(['tidal']))).toHaveLength(0);
  });

  it('counts are scoped by SOURCE but not by the active state tab', () => {
    // Each tab must say how many you would get by switching to it.
    const all = libraryFilterCounts(rows, NONE);
    expect(all).toEqual({ all: 3, attention: 1, running: 1, synced: 1 });

    const spotifyOnly = libraryFilterCounts(rows, new Set(['spotify']));
    expect(spotifyOnly).toEqual({ all: 2, attention: 0, running: 1, synced: 1 });
  });

  it('lists the sources present, de-duplicated and stable', () => {
    expect(librarySources(rows)).toEqual(['spotify', 'tidal']);
    expect(librarySources([])).toEqual([]);
  });
});

describe('which tabs render', () => {
  it('hides a state tab with nothing in it — a tab with no rows has no tab', () => {
    const counts = { all: 3, attention: 0, running: 0, synced: 3 };
    expect(libraryVisibleFilters(counts, 'all').map((f) => f.id)).toEqual(['all', 'synced']);
  });

  it('always keeps All, so the strip never empties', () => {
    const counts = { all: 0, attention: 0, running: 0, synced: 0 };
    expect(libraryVisibleFilters(counts, 'all').map((f) => f.id)).toEqual(['all']);
  });

  it('keeps the ACTIVE tab even at zero', () => {
    // Removing the tab you are standing on would move the page out from
    // under you the moment its last row finished.
    const counts = { all: 3, attention: 0, running: 0, synced: 3 };
    expect(libraryVisibleFilters(counts, 'attention').map((f) => f.id)).toEqual([
      'all',
      'attention',
      'synced',
    ]);
  });

  it('renders them in the declared order, never the order they filled up', () => {
    const counts = { all: 9, attention: 1, running: 1, synced: 1 };
    expect(libraryVisibleFilters(counts, 'all').map((f) => f.id)).toEqual(
      LIBRARY_FILTERS.map((f) => f.id),
    );
  });
});

describe('the header summary', () => {
  it('says something true and omits what it has nothing to say about', () => {
    expect(librarySummary([row({ total_count: 10, discovered_count: 10 })])).toBe('1 playlist');
  });

  it('reports work in flight and missing tracks when there are any', () => {
    const rows = [
      row({ id: 1, total_count: 10, discovered_count: 8 }),
      row({ id: 2, pipeline_state: { status: 'running' } }),
    ];
    expect(librarySummary(rows)).toBe('2 playlists · 1 working · 2 tracks missing');
  });

  it('a fresh install gets a sentence, not a row of zeroes', () => {
    expect(librarySummary([])).toBe('No playlists yet');
  });

  it('singularises', () => {
    expect(librarySummary([row({ total_count: 2, discovered_count: 1 })])).toBe(
      '1 playlist · 1 track missing',
    );
  });
});

describe('the count helpers', () => {
  it('libraryTotal prefers total_count, then track_count, then zero', () => {
    // total_count is the discovery-side total; track_count is what the source
    // claimed at mirror time. They disagree on playlists that changed
    // upstream, and the discovery-side number is the one the bar is about.
    expect(libraryTotal(row({ total_count: 12, track_count: 10 }))).toBe(12);
    expect(libraryTotal(row({ total_count: undefined, track_count: 10 }))).toBe(10);
    expect(libraryTotal(row({ total_count: 0, track_count: 0 }))).toBe(0);
  });

  it('libraryDiscovered defaults to zero rather than NaN', () => {
    expect(libraryDiscovered(row({ discovered_count: 7 }))).toBe(7);
    expect(libraryDiscovered(row())).toBe(0);
  });

  it('libraryMatchesFilter is the single predicate the filtering uses', () => {
    const short = row({ total_count: 10, discovered_count: 4 });
    expect(libraryMatchesFilter(short, 'all')).toBe(true);
    expect(libraryMatchesFilter(short, 'attention')).toBe(true);
    expect(libraryMatchesFilter(short, 'synced')).toBe(false);
    expect(libraryMatchesFilter(short, 'running')).toBe(false);
  });

  it('an unknown filter falls through to showing the row, never hiding it', () => {
    // A filter id that no longer exists must not silently empty the library.
    expect(libraryMatchesFilter(row(), 'nonsense' as LibraryFilter)).toBe(true);
  });
});

describe('the card state', () => {
  it('names each state, and ok renders nothing extra', () => {
    expect(libraryCardState(row({ pipeline_state: { status: 'error' } }))).toBe('error');
    expect(libraryCardState(row({ pipeline_state: { status: 'running' } }))).toBe('working');
    expect(libraryCardState(row({ total_count: 10, discovered_count: 6 }))).toBe('short');
    expect(libraryCardState(row({ total_count: 10, discovered_count: 10 }))).toBe('ok');
  });

  it('an error outranks a run still in flight', () => {
    // A failed run needs a human whether or not the poller has caught up.
    expect(
      libraryCardState(row({ pipeline_state: { status: 'running', error: 'boom' } })),
    ).toBe('error');
  });

  it('a never-touched playlist is ok, not short', () => {
    // Every freshly mirrored playlist has discovered_count 0; ringing them all
    // would put a warning on the entire library on day one.
    expect(libraryCardState(row({ total_count: 10, discovered_count: 0 }))).toBe('ok');
  });

  it('coverage is a clamped percentage, and 0 when there is nothing to measure', () => {
    expect(libraryCoveragePct(row({ total_count: 10, discovered_count: 6 }))).toBe(60);
    expect(libraryCoveragePct(row({ total_count: 10, discovered_count: 99 }))).toBe(100);
    expect(libraryCoveragePct(row({ total_count: 0, discovered_count: 0 }))).toBe(0);
  });

  it('counts what is missing, never negative', () => {
    expect(libraryMissingCount(row({ total_count: 86, discovered_count: 62 }))).toBe(24);
    expect(libraryMissingCount(row({ total_count: 10, discovered_count: 12 }))).toBe(0);
  });
});

describe('sorting', () => {
  const rows = [
    row({ id: 1, total_count: 10, discovered_count: 10 }),
    row({ id: 2, pipeline_state: { status: 'running' } }),
    row({ id: 3, total_count: 10, discovered_count: 4 }),
    row({ id: 4, pipeline_state: { status: 'error' } }),
    row({ id: 5, total_count: 10, discovered_count: 10 }),
  ];

  it('puts problems first: error, short, working, then healthy', () => {
    // This ordering is what lets the cards stay calm — with the broken one at
    // the front, the design needs no warning colours to make it findable.
    expect(librarySortedRows(rows).map((r) => r.id)).toEqual([4, 3, 2, 1, 5]);
  });

  it('keeps the incoming order within a state', () => {
    // The backend sends newest-updated first; that is a sensible second key.
    expect(librarySortedRows(rows).slice(3).map((r) => r.id)).toEqual([1, 5]);
  });

  it('does not reorder the caller’s array', () => {
    const original = [...rows];
    librarySortedRows(rows);
    expect(rows).toEqual(original);
  });
});
