import { describe, expect, it } from 'vitest';

import type { ExplorerArtist, MirroredPlaylist } from './-explorer.types';

import {
  artistHasSelection,
  clampExplorerZoom,
  EXPLORER_MAX_ZOOM,
  EXPLORER_MIN_ZOOM,
  explorerAlbumNodeId,
  explorerAlbumTypeLabel,
  explorerArtistKey,
  explorerBuildProgress,
  explorerCardView,
  explorerCurvePath,
  explorerCurveStroke,
  explorerFitScrollLeft,
  explorerFitZoom,
  explorerFormatDuration,
  explorerNodePosition,
  explorerRowCapacity,
  explorerSelectableAlbumIds,
  explorerSelectionLabel,
  explorerSelectionTotals,
  explorerSourceKey,
  explorerSourceLabel,
  explorerSvgSize,
  explorerWheelStep,
  groupPlaylistsBySource,
  groupSelectionByArtist,
  isRealAlbumId,
  planArtistRows,
} from './-explorer.core';

/**
 * Differential tests against the vanilla explorer (pages-extra.js:1-1141).
 * Expected values are written as LITERALS, never re-derived from the module
 * under test — a test that interpolates the source's own formula passes even
 * when the formula is wrong.
 */

function playlist(overrides: Partial<MirroredPlaylist> = {}): MirroredPlaylist {
  return { id: 1, name: 'Mix', source: 'spotify', ...overrides };
}

describe('explorerCardView — the readiness gate', () => {
  it('rounds the discovered percentage and gates clicks at 50%', () => {
    expect(explorerCardView(playlist({ total_count: 100, discovered_count: 49 })).pct).toBe(49);
    expect(explorerCardView(playlist({ total_count: 100, discovered_count: 49 })).isReady).toBe(
      false,
    );
    expect(explorerCardView(playlist({ total_count: 100, discovered_count: 50 })).isReady).toBe(
      true,
    );
    // 49.5% rounds to 50 and therefore passes the gate — the vanilla compared
    // the ROUNDED value, not the raw ratio.
    expect(explorerCardView(playlist({ total_count: 200, discovered_count: 99 })).pct).toBe(50);
    expect(explorerCardView(playlist({ total_count: 200, discovered_count: 99 })).isReady).toBe(
      true,
    );
  });

  it('falls back to track_count, and calls an empty playlist 0%', () => {
    expect(explorerCardView(playlist({ track_count: 40, discovered_count: 10 })).pct).toBe(25);
    expect(explorerCardView(playlist({ track_count: 40, discovered_count: 10 })).total).toBe(40);
    const empty = explorerCardView(playlist({ total_count: 0, discovered_count: 0 }));
    expect(empty.pct).toBe(0);
    expect(empty.isReady).toBe(false);
  });

  it('prefers total_count when both counters are present', () => {
    const view = explorerCardView(playlist({ total_count: 10, track_count: 999 }));
    expect(view.total).toBe(10);
  });

  it('shows the Discover button exactly when the card is not ready', () => {
    expect(
      explorerCardView(playlist({ total_count: 10, discovered_count: 4 })).showDiscoverButton,
    ).toBe(true);
    expect(
      explorerCardView(playlist({ total_count: 10, discovered_count: 5 })).showDiscoverButton,
    ).toBe(false);
  });
});

describe('explorerCardView — badge precedence', () => {
  it('puts a mostly-owned playlist at the top of the chain', () => {
    const view = explorerCardView(
      playlist({
        total_count: 10,
        discovered_count: 10,
        in_library_count: 8,
        wishlisted_count: 5,
        explored: true,
      }),
    );
    expect(view.badge).toEqual({
      kind: 'downloaded',
      title: 'Most tracks in library',
      text: '✓',
    });
  });

  it('needs 80% in library, not merely some', () => {
    const view = explorerCardView(
      playlist({ total_count: 10, discovered_count: 10, in_library_count: 7 }),
    );
    expect(view.badge?.kind).toBe('ready');
    expect(view.inLibrary).toBe(7);
  });

  it('treats explored_at and explored as the same signal, and both outrank the heart', () => {
    const byTimestamp = explorerCardView(
      playlist({
        total_count: 10,
        discovered_count: 10,
        explored_at: '2026-08-01',
        wishlisted_count: 3,
      }),
    );
    const byFlag = explorerCardView(
      playlist({ total_count: 10, discovered_count: 10, explored: true, wishlisted_count: 3 }),
    );
    expect(byTimestamp.badge).toEqual({
      kind: 'explored',
      title: 'Already explored',
      text: '✓',
    });
    expect(byFlag.badge).toEqual(byTimestamp.badge);
    expect(byFlag.wasExplored).toBe(true);
  });

  it('shows the heart only when nothing above it applies', () => {
    const view = explorerCardView(
      playlist({ total_count: 10, discovered_count: 10, wishlisted_count: 2 }),
    );
    expect(view.badge).toEqual({ kind: 'wishlisted', title: 'Tracks wishlisted', text: '♥' });
  });

  it('stars a fully discovered playlist and shows the percentage below the gate', () => {
    expect(explorerCardView(playlist({ total_count: 10, discovered_count: 10 })).badge).toEqual({
      kind: 'ready',
      title: 'Ready to explore',
      text: '★',
    });
    expect(explorerCardView(playlist({ total_count: 10, discovered_count: 3 })).badge).toEqual({
      kind: 'needs-discovery',
      title: 'Needs discovery (30%)',
      text: '30%',
    });
  });

  it('leaves a ready-but-partial playlist with no badge at all', () => {
    // 50-99% discovered, nothing owned, nothing wishlisted, never explored:
    // every branch of the vanilla ladder misses.
    expect(explorerCardView(playlist({ total_count: 10, discovered_count: 7 })).badge).toBeNull();
  });
});

describe('explorerCardView — the meta lines', () => {
  it('labels the three discovery states', () => {
    const full = explorerCardView(playlist({ total_count: 10, discovered_count: 10 }));
    expect(full.metaText).toBe('Fully discovered');
    expect(full.metaClass).toBe('explorer-picker-discovered');

    const ready = explorerCardView(playlist({ total_count: 10, discovered_count: 6 }));
    expect(ready.metaText).toBe('60% discovered');
    expect(ready.metaClass).toBeNull();

    const notReady = explorerCardView(playlist({ total_count: 10, discovered_count: 2 }));
    expect(notReady.metaText).toBe('20% discovered');
    expect(notReady.metaClass).toBe('explorer-picker-not-ready');
  });

  it('adds the library/wishlist counters in that order, and omits zeroes', () => {
    const both = explorerCardView(
      playlist({ total_count: 10, discovered_count: 10, in_library_count: 1, wishlisted_count: 2 }),
    );
    expect(both.statusParts).toEqual([
      { className: 'explorer-picker-in-library', text: '1 in library' },
      { className: 'explorer-picker-wishlisted', text: '2 wishlisted' },
    ]);
    expect(explorerCardView(playlist({ total_count: 10 })).statusParts).toEqual([]);
  });
});

describe('groupPlaylistsBySource', () => {
  const rows: MirroredPlaylist[] = [
    playlist({ id: 1, source: 'Spotify' }),
    playlist({ id: 2, source: 'tidal' }),
    playlist({ id: 3, source: 'SPOTIFY' }),
    playlist({ id: 4, source: null }),
  ];

  it('lowercases the key, keeps first-seen order, and buckets a missing source as other', () => {
    const { groups } = groupPlaylistsBySource(rows);
    expect(groups.map((g) => g.source)).toEqual(['spotify', 'tidal', 'other']);
    expect(groups.map((g) => g.count)).toEqual([2, 1, 1]);
    expect(groups[0]?.playlists.map((p) => p.id)).toEqual([1, 3]);
  });

  it('hides the tab strip at one source', () => {
    expect(groupPlaylistsBySource([playlist({ id: 1 })]).showTabs).toBe(false);
    expect(groupPlaylistsBySource(rows).showTabs).toBe(true);
    expect(groupPlaylistsBySource([]).showTabs).toBe(false);
  });

  it('keeps a valid active source and falls back to the first group otherwise', () => {
    expect(groupPlaylistsBySource(rows, 'tidal').activeSource).toBe('tidal');
    expect(groupPlaylistsBySource(rows, 'deezer').activeSource).toBe('spotify');
    expect(groupPlaylistsBySource(rows).activeSource).toBe('spotify');
    expect(groupPlaylistsBySource([], 'tidal').activeSource).toBeNull();
  });

  it('lowercases the source key and buckets a blank one as other', () => {
    expect(explorerSourceKey({ id: 1, source: 'TIDAL' })).toBe('tidal');
    expect(explorerSourceKey({ id: 1, source: '' })).toBe('other');
    expect(explorerSourceKey({ id: 1 })).toBe('other');
  });

  it('title-cases an unknown source for its tab label', () => {
    expect(explorerSourceLabel('spotify')).toBe('Spotify');
    expect(explorerSourceLabel('youtube')).toBe('YouTube');
    expect(explorerSourceLabel('soundcloud')).toBe('Soundcloud');
    expect(explorerSourceLabel('')).toBe('');
  });
});

describe('the tree shape', () => {
  it('collapses every non-alphanumeric character into an underscore', () => {
    expect(explorerArtistKey('Sigur Rós')).toBe('Sigur_R_s');
    expect(explorerArtistKey('AC/DC')).toBe('AC_DC');
    // The documented collision: two distinct artists share one key.
    expect(explorerArtistKey('AC-DC')).toBe('AC_DC');
    expect(explorerArtistKey(null)).toBe('');
    expect(explorerArtistKey(undefined)).toBe('');
  });

  it('grows rows 2, 3, 4, 5…', () => {
    expect(explorerRowCapacity(0)).toBe(2);
    expect(explorerRowCapacity(3)).toBe(5);
    expect(planArtistRows(0)).toEqual([]);
    expect(planArtistRows(1)).toEqual([1]);
    expect(planArtistRows(2)).toEqual([2]);
    expect(planArtistRows(3)).toEqual([2, 1]);
    expect(planArtistRows(5)).toEqual([2, 3]);
    expect(planArtistRows(6)).toEqual([2, 3, 1]);
    expect(planArtistRows(14)).toEqual([2, 3, 4, 5]);
    expect(planArtistRows(15)).toEqual([2, 3, 4, 5, 1]);
  });

  it('conserves the artist count across the plan', () => {
    for (const n of [7, 20, 33, 100]) {
      expect(planArtistRows(n).reduce((a, b) => a + b, 0)).toBe(n);
    }
  });

  it('falls back to a positional album id, which the tracklist gate then rejects', () => {
    expect(explorerAlbumNodeId({ spotify_id: '4aawyAB9' }, 'Radiohead', 2)).toBe('4aawyAB9');
    expect(explorerAlbumNodeId({}, 'Radiohead', 2)).toBe('Radiohead_2');
    expect(isRealAlbumId('4aawyAB9')).toBe(true);
    expect(isRealAlbumId('Radiohead_2')).toBe(false);
  });

  it('labels album types', () => {
    expect(explorerAlbumTypeLabel('single')).toBe('Single');
    expect(explorerAlbumTypeLabel('ep')).toBe('EP');
    expect(explorerAlbumTypeLabel('album')).toBe('Album');
    expect(explorerAlbumTypeLabel(null)).toBe('Album');
  });

  it('formats durations, and blanks a missing one', () => {
    expect(explorerFormatDuration(215000)).toBe('3:35');
    expect(explorerFormatDuration(61000)).toBe('1:01');
    expect(explorerFormatDuration(3599000)).toBe('59:59');
    expect(explorerFormatDuration(0)).toBe('');
    expect(explorerFormatDuration(null)).toBe('');
  });

  it('reports build progress', () => {
    expect(explorerBuildProgress(3, 12)).toEqual({
      pct: 25,
      text: 'Discovering artists... 3 of 12',
    });
    // The vanilla divided by a streamed total; guard the zero case rather than
    // painting NaN% into the bar.
    expect(explorerBuildProgress(0, 0).pct).toBe(0);
  });
});

describe('selection', () => {
  const artists: ExplorerArtist[] = [
    {
      name: 'Boards of Canada',
      artist_id: 'a1',
      image_url: 'a1.jpg',
      albums: [
        { spotify_id: 'al1', title: 'Music Has the Right', track_count: 17 },
        { spotify_id: 'al2', title: 'Geogaddi', track_count: 23, owned: true },
        { title: 'No id', track_count: 4 },
      ],
    },
    {
      name: 'Aphex Twin',
      spotify_id: 'a2',
      albums: [{ spotify_id: 'al3', title: 'SAW II', track_count: 24 }],
    },
    { name: 'Errored', error: 'not found', albums: null },
  ];

  it('pluralises the count label', () => {
    expect(explorerSelectionLabel(0)).toBe('0 albums selected');
    expect(explorerSelectionLabel(1)).toBe('1 album selected');
    expect(explorerSelectionLabel(2)).toBe('2 albums selected');
  });

  it('select-all skips owned albums and albums with no real id', () => {
    expect(explorerSelectableAlbumIds(artists)).toEqual(['al1', 'al3']);
  });

  it('lights the artist ring only when one of its own albums is selected', () => {
    const selected = new Set(['al3']);
    expect(artistHasSelection(artists[0]!, selected)).toBe(false);
    expect(artistHasSelection(artists[1]!, selected)).toBe(true);
    expect(artistHasSelection(artists[2]!, selected)).toBe(false);
  });

  it('groups the selection by artist and drops artists with nothing selected', () => {
    const sections = groupSelectionByArtist(artists, new Set(['al1', 'al2']));
    expect(sections).toHaveLength(1);
    expect(sections[0]?.artistId).toBe('a1');
    expect(sections[0]?.name).toBe('Boards of Canada');
    expect(sections[0]?.image).toBe('a1.jpg');
    expect(sections[0]?.albums.map((a) => a.spotify_id)).toEqual(['al1', 'al2']);
  });

  it('falls back to spotify_id for the artist id', () => {
    const sections = groupSelectionByArtist(artists, new Set(['al3']));
    expect(sections[0]?.artistId).toBe('a2');
    expect(sections[0]?.image).toBeNull();
  });

  it('totals the modal hero counters', () => {
    const sections = groupSelectionByArtist(artists, new Set(['al1', 'al2', 'al3']));
    expect(explorerSelectionTotals(sections)).toEqual({ artists: 2, albums: 3, tracks: 64 });
    expect(explorerSelectionTotals([])).toEqual({ artists: 0, albums: 0, tracks: 0 });
  });
});

describe('SVG geometry', () => {
  it('bends 45% of the way down between the two nodes', () => {
    expect(explorerCurvePath(0, 0, 100, 200)).toBe('M 0 0 C 0 90, 100 90, 100 200');
    expect(explorerCurvePath(50, 10, 50, 110)).toBe('M 50 10 C 50 55, 50 55, 50 110');
  });

  it('gives each tier its own stroke', () => {
    expect(explorerCurveStroke('root')).toEqual({
      stroke: 'url(#explorer-grad-root)',
      strokeWidth: '1.5',
    });
    expect(explorerCurveStroke('album')).toEqual({
      stroke: 'url(#explorer-grad-album)',
      strokeWidth: '1',
    });
    expect(explorerCurveStroke('track')).toEqual({
      stroke: 'rgba(255,255,255,0.05)',
      strokeWidth: '0.8',
    });
  });

  it('sizes the canvas to the larger of scroll/offset, plus slack', () => {
    expect(explorerSvgSize(1200, 800, 300, 900)).toEqual({ width: 1240, height: 940 });
  });

  it('divides positions by the zoom so lines stay glued to scaled nodes', () => {
    const node = { left: 200, top: 400, bottom: 500, width: 100 };
    const tree = { left: 100, top: 100 };
    expect(explorerNodePosition(node, tree, 1)).toEqual({ cx: 150, top: 300, bottom: 400 });
    expect(explorerNodePosition(node, tree, 2)).toEqual({ cx: 75, top: 150, bottom: 200 });
    // A zoom of 0 would divide by zero; the vanilla's `|| 1` fallback stands.
    expect(explorerNodePosition(node, tree, 0)).toEqual({ cx: 150, top: 300, bottom: 400 });
  });
});

describe('zoom', () => {
  it('clamps to 0.2–3', () => {
    expect(EXPLORER_MIN_ZOOM).toBe(0.2);
    expect(EXPLORER_MAX_ZOOM).toBe(3);
    expect(clampExplorerZoom(1)).toBe(1);
    expect(clampExplorerZoom(0.05)).toBe(0.2);
    expect(clampExplorerZoom(7)).toBe(3);
  });

  it('inverts the wheel so scrolling up zooms in', () => {
    expect(explorerWheelStep(120)).toBe(-0.08);
    expect(explorerWheelStep(-120)).toBe(0.08);
    expect(explorerWheelStep(0)).toBe(0.08);
  });

  it('fits to the smaller axis, never past 1.5, never below 0.2', () => {
    // 2000 wide in a 1040 viewport → 1000/2000 = 0.5; height is roomier.
    expect(explorerFitZoom(2000, 500, 1040, 1040)).toBe(0.5);
    // A tiny tree would scale to 10x; capped at 1.5.
    expect(explorerFitZoom(100, 100, 1040, 1040)).toBe(1.5);
    // A vast tree floors at the minimum zoom.
    expect(explorerFitZoom(100000, 100000, 1040, 1040)).toBe(0.2);
    // Nothing measured yet — leave the zoom alone rather than divide by zero.
    expect(explorerFitZoom(0, 0, 1040, 1040)).toBe(1);
  });

  it('centres the scaled tree, and never scrolls negative', () => {
    expect(explorerFitScrollLeft(2000, 1, 1040)).toBe(500);
    expect(explorerFitScrollLeft(2000, 0.5, 1040)).toBe(0);
    expect(explorerFitScrollLeft(500, 1, 1040)).toBe(0);
  });
});
