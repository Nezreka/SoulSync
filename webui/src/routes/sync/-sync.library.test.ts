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
  LIBRARY_SORTS,
  libraryCardState,
  libraryCoveragePct,
  libraryDiscovered,
  libraryFilterCounts,
  libraryGap,
  libraryIsComplete,
  libraryIsRunning,
  libraryMatchesFilter,
  libraryMatchesSearch,
  libraryMissingCount,
  libraryNeedsAttention,
  libraryOwned,
  libraryOwnedKnown,
  librarySearch,
  librarySortedRows,
  librarySources,
  librarySummary,
  libraryTotal,
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
    // The STATE counts only — schedule has its own test, and asserting the
    // whole record here would rewrite this literal every time a filter is
    // added.
    expect(libraryFilterCounts(rows, NONE)).toMatchObject({
      all: 3,
      attention: 1,
      running: 1,
      synced: 1,
    });
    expect(libraryFilterCounts(rows, new Set(['spotify']))).toMatchObject({
      all: 2,
      attention: 0,
      running: 1,
      synced: 1,
    });
  });

  it('lists the sources present, de-duplicated and stable', () => {
    expect(librarySources(rows)).toEqual(['spotify', 'tidal']);
    expect(librarySources([])).toEqual([]);
  });
});

describe('which tabs render', () => {
  /** Counts with everything defaulted, so adding a filter never rewrites these. */
  const counts = (over: Partial<Record<LibraryFilter, number>>): Record<LibraryFilter, number> => ({
    all: 0,
    attention: 0,
    running: 0,
    synced: 0,
    scheduled: 0,
    unscheduled: 0,
    ...over,
  });

  it('hides a state tab with nothing in it — a tab with no rows has no tab', () => {
    expect(libraryVisibleFilters(counts({ all: 3, synced: 3 }), 'all').map((f) => f.id)).toEqual(['all', 'synced']);
  });

  it('always keeps All, so the strip never empties', () => {
    expect(libraryVisibleFilters(counts({}), 'all').map((f) => f.id)).toEqual(['all']);
  });

  it('keeps the ACTIVE tab even at zero', () => {
    // Removing the tab you are standing on would move the page out from
    // under you the moment its last row finished.
    expect(libraryVisibleFilters(counts({ all: 3, synced: 3 }), 'attention').map((f) => f.id)).toEqual([
      'all',
      'attention',
      'synced',
    ]);
  });

  it('renders them in the declared order, never the order they filled up', () => {
    expect(libraryVisibleFilters(counts({ all: 9, attention: 1, running: 1, synced: 1, scheduled: 1, unscheduled: 1 }), 'all').map((f) => f.id)).toEqual(
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

describe('filtering by schedule', () => {
  const rows = [
    row({ id: 1, name: 'Road Trip' }),
    row({ id: 2, name: 'Deep Focus' }),
    row({ id: 3, name: 'Hot Hits' }),
  ];
  const scheduled = new Set([1, 3]);

  it('keeps only the playlists with a cadence', () => {
    expect(libraryVisibleRows(rows, 'scheduled', new Set(), scheduled).map((r) => r.id)).toEqual([
      1, 3,
    ]);
  });

  it('and only those without, for the other half', () => {
    expect(libraryVisibleRows(rows, 'unscheduled', new Set(), scheduled).map((r) => r.id)).toEqual([
      2,
    ]);
  });

  it('the two halves partition the library — every row lands in exactly one', () => {
    const a = libraryVisibleRows(rows, 'scheduled', new Set(), scheduled).length;
    const b = libraryVisibleRows(rows, 'unscheduled', new Set(), scheduled).length;
    expect(a + b).toBe(rows.length);
  });

  it('counts both halves, and they agree with what the filters return', () => {
    // A tab must never advertise a number its own filter would not produce.
    const counts = libraryFilterCounts(rows, new Set(), scheduled);
    expect(counts.scheduled).toBe(2);
    expect(counts.unscheduled).toBe(1);
  });

  it('shows NOTHING rather than everything when the schedule map is missing', () => {
    // An empty tab is a visible mistake; silently listing all 38 under
    // "Scheduled" would look like an answer.
    expect(libraryVisibleRows(rows, 'scheduled', new Set())).toEqual([]);
  });

  it('a row with no id cannot be either — it can never be scheduled', () => {
    const anon = [row({ id: undefined, name: 'Orphan' })];
    expect(libraryVisibleRows(anon, 'scheduled', new Set(), scheduled)).toEqual([]);
    expect(libraryVisibleRows(anon, 'unscheduled', new Set(), scheduled)).toEqual([]);
  });

  it('still honours the source chips alongside it', () => {
    const mixed = [
      row({ id: 1, name: 'A', source: 'spotify' }),
      row({ id: 3, name: 'B', source: 'tidal' }),
    ];
    expect(
      libraryVisibleRows(mixed, 'scheduled', new Set(['tidal']), scheduled).map((r) => r.id),
    ).toEqual([3]);
  });
});

describe('searching by name', () => {
  const rows = [
    row({ id: 1, name: 'Time Machine — 2000s' }),
    row({ id: 2, name: 'Discover Weekly' }),
    row({ id: 3, name: 'Deep Focus', custom_name: 'Monday' }),
  ];

  it('matches on a fragment, ignoring case', () => {
    expect(librarySearch(rows, 'machine').map((r) => r.id)).toEqual([1]);
    expect(librarySearch(rows, 'MACHINE').map((r) => r.id)).toEqual([1]);
  });

  it('matches a renamed playlist on BOTH names', () => {
    // The card shows the custom name, but someone who remembers importing
    // "Deep Focus" should still find it after calling it "Monday".
    expect(librarySearch(rows, 'Monday').map((r) => r.id)).toEqual([3]);
    expect(librarySearch(rows, 'Deep Focus').map((r) => r.id)).toEqual([3]);
  });

  it('an empty or whitespace query returns everything, not nothing', () => {
    expect(librarySearch(rows, '')).toHaveLength(3);
    expect(librarySearch(rows, '   ')).toHaveLength(3);
  });

  it('trims, so a trailing space from a paste still matches', () => {
    expect(librarySearch(rows, '  weekly  ').map((r) => r.id)).toEqual([2]);
  });

  it('returns nothing when nothing matches, rather than falling back to all', () => {
    expect(librarySearch(rows, 'zzzz')).toEqual([]);
  });

  it('survives a row with no name at all', () => {
    expect(librarySearch([row({ id: 9, name: undefined })], 'x')).toEqual([]);
    expect(libraryMatchesSearch(row({ id: 9, name: undefined }), '')).toBe(true);
  });
});

describe('what the library actually OWNS', () => {
  it('reads the in-library count, which is not the discovered one', () => {
    // Discovery matched a track to a source; owning the file is a separate
    // question the backend answers by joining the real tracks table.
    expect(libraryOwned(row({ discovered_count: 140, in_library_count: 96 }))).toBe(96);
  });

  it('is 0 when the field is absent, never the discovered count', () => {
    // Falling back to discovered would reintroduce the exact overclaim this
    // number exists to correct.
    expect(libraryOwned(row({ discovered_count: 140 }))).toBe(0);
  });
});

describe('ordering the library', () => {
  const rows = [
    row({ id: 1, name: 'Zebra', total_count: 10, discovered_count: 10, updated_at: '2026-01-01' }),
    row({ id: 2, name: 'apple', total_count: 90, discovered_count: 90, updated_at: '2026-03-01' }),
    row({ id: 3, name: 'Mango', total_count: 50, discovered_count: 50, updated_at: '2026-02-01' }),
  ];

  it('defaults to state, which is the only order about the LIBRARY', () => {
    const broken = row({ id: 9, name: 'aaa', pipeline_state: { status: 'error' } });
    expect(librarySortedRows([...rows, broken])[0].id).toBe(9);
  });

  it('sorts by the name a user SEES, case-insensitively', () => {
    // Not the raw name: a renamed playlist shows its alias, so sorting on the
    // original would order the list by something invisible.
    expect(librarySortedRows(rows, 'name').map((r) => r.name)).toEqual(['apple', 'Mango', 'Zebra']);
    const renamed = [row({ id: 1, name: 'Zebra', display_name: 'aardvark' }), row({ id: 2, name: 'apple' })];
    expect(librarySortedRows(renamed, 'name')[0].id).toBe(1);
  });

  it('sorts by track count, biggest first', () => {
    expect(librarySortedRows(rows, 'tracks').map((r) => r.total_count)).toEqual([90, 50, 10]);
  });

  it('sorts by most recently synced', () => {
    expect(librarySortedRows(rows, 'recent').map((r) => r.id)).toEqual([2, 3, 1]);
  });

  it('a row with no timestamp sorts last rather than first', () => {
    // An unparseable date must not become 1970-beats-everything or, worse, NaN.
    const withNone = [...rows, row({ id: 4, name: 'No date', updated_at: undefined })];
    expect(librarySortedRows(withNone, 'recent').at(-1)?.id).toBe(4);
  });

  it('breaks every tie on state, so a broken row still surfaces', () => {
    const tied = [
      row({ id: 1, name: 'A', total_count: 10, discovered_count: 10 }),
      row({ id: 2, name: 'B', total_count: 10, pipeline_state: { status: 'error' } }),
    ];
    expect(librarySortedRows(tied, 'tracks')[0].id).toBe(2);
  });

  it('never sorts the caller’s array in place', () => {
    const original = [...rows];
    librarySortedRows(rows, 'name');
    expect(rows).toEqual(original);
  });

  it('every declared sort id is handled, none falls through to state silently', () => {
    const byId = Object.fromEntries(
      LIBRARY_SORTS.map((s) => [s.id, librarySortedRows(rows, s.id).map((r) => r.id).join()]),
    );
    // name/tracks/recent must each differ from the default ordering.
    for (const id of ['name', 'tracks', 'recent']) {
      expect(byId[id], `${id} produced the default order`).not.toBe(byId.state);
    }
  });
});

describe('which gap a playlist has', () => {
  const full = { total_count: 140, discovered_count: 140 };

  it('none when discovery is complete and everything is owned', () => {
    expect(libraryGap(row({ ...full, in_library_count: 140 }))).toBe('none');
  });

  it('discovery when tracks were never found', () => {
    expect(libraryGap(row({ total_count: 140, discovered_count: 62, in_library_count: 0 }))).toBe(
      'discovery',
    );
  });

  it('discovery WINS over ownership — you cannot own what was never found', () => {
    // Sequential, not alternative. Offering "download the rest" before
    // discovery has finished would act on an incomplete list.
    expect(libraryGap(row({ total_count: 140, discovered_count: 62, in_library_count: 10 }))).toBe(
      'discovery',
    );
  });

  it('ownership once discovery is complete but the files are not here', () => {
    expect(libraryGap(row({ ...full, in_library_count: 96 }))).toBe('ownership');
  });

  it('owning NONE of a fully discovered playlist is a gap, not a blank', () => {
    // The case the whole change exists for.
    expect(libraryGap(row({ ...full, in_library_count: 0 }))).toBe('ownership');
  });

  it('distinguishes "reported zero" from "did not report"', () => {
    // The whole reason the gap can trust a 0. Collapsing these would either
    // hide a playlist you own none of, or flag every row on a payload that
    // predates the field.
    expect(libraryOwnedKnown(row({ in_library_count: 0 }))).toBe(true);
    expect(libraryOwnedKnown(row({}))).toBe(false);
  });

  it('is none when the backend reported no ownership figure at all', () => {
    // An absent field is an older payload, not a claim that you own nothing —
    // treating it as zero would flag every row on such a payload.
    expect(libraryGap(row(full))).toBe('none');
  });

  it('is none for a playlist nobody has run yet', () => {
    // A fresh import is not broken.
    expect(libraryGap(row({ total_count: 140, discovered_count: 0, in_library_count: 0 }))).toBe(
      'none',
    );
  });
});

describe('an ownership gap reads as one everywhere', () => {
  const short = row({ total_count: 140, discovered_count: 140, in_library_count: 96 });

  it('lands in Needs attention, which used to miss it entirely', () => {
    expect(libraryNeedsAttention(short)).toBe(true);
    expect(libraryCardState(short)).toBe('short');
  });

  it('leaves the Discovered tab, because it is no longer resting', () => {
    // libraryIsComplete refuses anything needing attention, so the two tabs
    // still partition rather than overlap.
    expect(libraryIsComplete(short)).toBe(false);
  });

  it('the ring measures OWNERSHIP, not discovery', () => {
    // On the discovery ratio this would draw a full ring on a playlist missing
    // 44 files.
    expect(libraryCoveragePct(short)).toBe(69);
  });

  it('the missing count is the files, not the matches', () => {
    // total - discovered is zero here; the button would read "Find 0 missing".
    expect(libraryMissingCount(short)).toBe(44);
  });

  it('a discovery gap still counts discovery, unchanged', () => {
    const disc = row({ total_count: 86, discovered_count: 62, in_library_count: 40 });
    expect(libraryCoveragePct(disc)).toBe(72);
    expect(libraryMissingCount(disc)).toBe(24);
  });

  it('a running playlist is still never flagged, whichever gap it has', () => {
    const running = row({
      total_count: 140,
      discovered_count: 140,
      in_library_count: 0,
      pipeline_state: { status: 'running' },
    });
    expect(libraryNeedsAttention(running)).toBe(false);
  });
});
