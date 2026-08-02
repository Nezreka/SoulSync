import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { ArtMapEdge, ArtMapNode, ArtMapRawNode } from './-discover.artist-map';

import {
  ARTMAP_EMPTY_WATCHLIST,
  ARTMAP_EXPLORE_URL,
  ARTMAP_GENRE_LIST_URL,
  ARTMAP_GENRE_OVERLAP,
  ARTMAP_GENRES_URL,
  ARTMAP_PROMPT_DEBOUNCE_MS,
  ARTMAP_RELATED_GENRES,
  ARTMAP_SEARCH_DEBOUNCE_MS,
  ARTMAP_SEARCH_LIMIT,
  ARTMAP_SEARCH_MIN_CHARS,
  ARTMAP_TITLES,
  ARTMAP_URL,
  type ArtMapGenre,
  artMapExploreEmptyMessage,
  artMapExploreNodes,
  artMapExploreStats,
  artMapGenreGroups,
  artMapGenreStats,
  artMapInfoBest,
  artMapInfoSourceOrder,
  artMapPanelTitle,
  artMapPayloadIsEmpty,
  artMapPoolEntry,
  artMapRelatedGenres,
  artMapRelatedNodes,
  artMapSearchResults,
  artMapSearchShouldRun,
  artMapSearchUrl,
  artMapUsesOneIsland,
  artMapWatchlistNodes,
  artMapWatchlistStats,
} from './-discover.artist-map.entry';

/**
 * The three entry points.
 *
 * These are async orchestration around fetches, so rather than a side-by-side
 * run this pins the DECISIONS each one makes — which nodes are focal, which
 * genres qualify as related, what the toolbar reads — and cross-checks each
 * constant against the text still in discover.js, so a drift on either side
 * fails here.
 */
const SOURCE = readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8');

describe('the endpoints and copy still match discover.js', () => {
  it('keeps the four map URLs', () => {
    for (const url of [ARTMAP_URL, ARTMAP_GENRES_URL, ARTMAP_GENRE_LIST_URL, ARTMAP_EXPLORE_URL]) {
      expect(SOURCE).toContain(url);
    }
  });

  it('keeps the search tuning', () => {
    expect(ARTMAP_SEARCH_MIN_CHARS).toBe(2);
    expect(ARTMAP_SEARCH_LIMIT).toBe(8);
    expect(ARTMAP_SEARCH_DEBOUNCE_MS).toBe(300);
    expect(ARTMAP_PROMPT_DEBOUNCE_MS).toBe(350);
    expect(SOURCE).toContain('artists.slice(0, 8)');
    expect(SOURCE).toContain('if (q.length < 2)');
  });

  it('keeps the empty-watchlist copy', () => {
    expect(SOURCE).toContain(ARTMAP_EMPTY_WATCHLIST);
  });

  it('keeps the genre-overlap tuning', () => {
    expect(ARTMAP_RELATED_GENRES).toBe(4);
    expect(ARTMAP_GENRE_OVERLAP).toBe(0.1);
    expect(SOURCE).toContain('primarySet.size * 0.1');
    expect(SOURCE).toContain('.slice(0, 4)');
  });

  it('keeps the three toolbar titles', () => {
    expect(SOURCE).toContain(`'${ARTMAP_TITLES.genre}'`);
    expect(SOURCE).toContain(`'${ARTMAP_TITLES.explorer}'`);
  });
});

describe('the watchlist map', () => {
  const payload = {
    success: true,
    watchlist_count: 12,
    similar_count: 340,
    nodes: [
      { id: 'a', name: 'Watched', type: 'watchlist' },
      { id: 'b', name: 'Similar', type: 'similar' },
      { id: 'c', name: 'Untyped' },
    ] as ArtMapRawNode[],
  };

  it('marks only watchlist artists focal', () => {
    expect(artMapWatchlistNodes(payload).map((n) => n._focal)).toEqual([true, false, false]);
  });

  it('copies rather than mutating the payload', () => {
    artMapWatchlistNodes(payload);
    expect(payload.nodes[0]).not.toHaveProperty('_focal');
  });

  it('reads the counts from the payload, not from the node list', () => {
    // 12 + 340 is 352, but only three nodes came back — the header reports what
    // the server counted, which is the honest number for a capped payload.
    expect(artMapWatchlistStats(payload)).toBe('12 watchlist · 340 similar');
  });

  it('treats an empty node list as an empty watchlist', () => {
    expect(artMapPayloadIsEmpty({ success: true, nodes: [] })).toBe(true);
    expect(artMapPayloadIsEmpty({ success: false, nodes: [{}] })).toBe(true);
    expect(artMapPayloadIsEmpty({ success: true, nodes: [{}] })).toBe(false);
    expect(artMapPayloadIsEmpty({})).toBe(true);
  });
});

describe('the genre map', () => {
  const genres: ArtMapGenre[] = [
    { name: 'Rock', count: 100, artist_ids: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] },
    { name: 'Indie', count: 40, artist_ids: [1, 2, 3, 4, 99] }, //   4 of 10 → 40%
    { name: 'Metal', count: 30, artist_ids: [5, 6, 98] }, //         2 of 10 → 20%
    { name: 'Jazz', count: 20, artist_ids: [7, 97] }, //             1 of 10 → 10%, NOT > 10%
    { name: 'Folk', count: 10, artist_ids: [96] }, //                0
    { name: 'Punk', count: 50, artist_ids: [1, 2, 3, 4, 5, 6, 7, 8] }, // 8 of 10 → 80%
    { name: 'Pop', count: 60, artist_ids: [1, 2, 3, 4, 5] }, //      5 of 10 → 50%
  ];

  it('picks the top four by artist OVERLAP, not by size', () => {
    const out = artMapRelatedGenres(genres, 'Rock');
    // Punk (8) > Pop (5) > Indie (4) > Metal (2). Jazz is exactly 10% and is
    // excluded — the test is `>`, not `>=`.
    expect(out?.map((g) => g.name)).toEqual(['Rock', 'Punk', 'Pop', 'Indie', 'Metal']);
  });

  it('excludes a genre sitting exactly on the threshold', () => {
    const two: ArtMapGenre[] = [
      { name: 'Rock', count: 10, artist_ids: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] },
      { name: 'Jazz', count: 1, artist_ids: [1] },
    ];
    expect(artMapRelatedGenres(two, 'Rock')?.map((g) => g.name)).toEqual(['Rock']);
  });

  it('caps the companions at four however many qualify', () => {
    const out = artMapRelatedGenres(genres, 'Rock');
    expect(out).toHaveLength(1 + ARTMAP_RELATED_GENRES);
  });

  it('returns null for a genre that is not in the payload', () => {
    expect(artMapRelatedGenres(genres, 'Nope')).toBeNull();
  });

  it('always leads with the picked genre', () => {
    expect(artMapRelatedGenres(genres, 'Metal')?.[0].name).toBe('Metal');
  });

  it('drops artist ids the payload has no node for', () => {
    const nodes: Record<string, ArtMapRawNode> = { 1: { id: 1, name: 'One' } };
    const groups = artMapGenreGroups([{ name: 'Rock', count: 900, artist_ids: [1, 2, 3] }], nodes);
    expect(groups[0].nodes).toHaveLength(1);
    // …but the label still reports the genre's true size.
    expect(groups[0].count).toBe(900);
  });

  it('sums the artist ids for the header, including duplicates across genres', () => {
    expect(artMapGenreStats(genres.slice(0, 3))).toEqual({ count: 3, artists: 18 });
  });
});

describe('the explorer', () => {
  const payload = {
    success: true,
    center: 'Aphex Twin',
    nodes: [
      { id: 'c', name: 'Aphex Twin', ring: 0 },
      { id: 'x', name: 'Centre by type', type: 'center' },
      { id: 'a', name: 'Similar', ring: 1 },
      { id: 'b', name: 'Similar 2', ring: 1 },
      { id: 'd', name: 'Extended', ring: 2 },
    ],
  };

  it('treats ring 0 AND an explicit centre type as focal', () => {
    expect(artMapExploreNodes(payload).map((n) => n._focal)).toEqual([
      true,
      true,
      false,
      false,
      false,
    ]);
  });

  it('counts the rings for the header', () => {
    expect(artMapExploreStats(payload)).toBe('Aphex Twin · 2 similar · 1 extended');
  });

  it('says something different for a name that is not an artist at all', () => {
    expect(artMapExploreEmptyMessage(404, 'asdf')).toContain("doesn't appear to be a real artist");
    expect(artMapExploreEmptyMessage(200, 'Obscure Band')).toContain('No data found');
    // Both name the input back, so the user can see what was actually searched.
    expect(artMapExploreEmptyMessage(404, 'asdf')).toContain('asdf');
    expect(artMapExploreEmptyMessage(500, 'asdf')).toContain('asdf');
  });

  it('stays multi-island, unlike the other two entry points', () => {
    expect(artMapUsesOneIsland('watchlist')).toBe(true);
    expect(artMapUsesOneIsland('genre')).toBe(true);
    expect(artMapUsesOneIsland('explorer')).toBe(false);
  });

  it('titles the panel differently from the toolbar', () => {
    expect(artMapPanelTitle('watchlist')).toBe('Watchlist Map');
    expect(ARTMAP_TITLES.watchlist).toBe('Artist Map');
    expect(artMapPanelTitle('explorer', 'Aphex Twin')).toBe('Explore: Aphex Twin');
    expect(artMapPanelTitle('explorer')).toBe('Explore: ');
  });
});

describe('the toolbar search', () => {
  it('needs two characters, after trimming', () => {
    expect(artMapSearchShouldRun('a')).toBe(false);
    expect(artMapSearchShouldRun(' a ')).toBe(false);
    expect(artMapSearchShouldRun('ab')).toBe(true);
    expect(artMapSearchShouldRun('')).toBe(false);
    expect(artMapSearchShouldRun(null)).toBe(false);
  });

  it('encodes the query', () => {
    expect(artMapSearchUrl('Sigur Rós & co')).toBe(
      '/api/discover/build-playlist/search-artists?query=Sigur%20R%C3%B3s%20%26%20co',
    );
  });

  it('caps the dropdown at eight', () => {
    const artists = Array.from({ length: 20 }, (_, i) => ({ name: `A${i}` }));
    expect(artMapSearchResults({ success: true, artists })).toHaveLength(8);
  });

  it('treats a failed or malformed response as no results', () => {
    expect(artMapSearchResults({ success: false, artists: [{ name: 'x' }] })).toEqual([]);
    expect(artMapSearchResults({ success: true })).toEqual([]);
    expect(
      artMapSearchResults({ success: true, artists: 'nope' as unknown as { name: string }[] }),
    ).toEqual([]);
  });
});

describe('the artist info hand-off', () => {
  const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
    ({
      id: 3,
      name: 'Aphex Twin',
      x: 0,
      y: 0,
      radius: 40,
      opacity: 1,
      type: 'similar',
      image_url: '/a.jpg',
      spotify_id: 'sp',
      itunes_id: 'it',
      deezer_id: 'dz',
      discogs_id: 'dc',
      musicbrainz_id: 'mb',
      ...over,
    }) as ArtMapNode;

  it('reorders the WHOLE chain behind the active source', () => {
    // Distinct from the context menu, which keeps a fixed spotify/itunes/deezer
    // tail whatever the active source is.
    expect(artMapInfoSourceOrder('deezer')).toEqual([
      'deezer_id',
      'spotify_id',
      'itunes_id',
      'discogs_id',
      'musicbrainz_id',
    ]);
    expect(artMapInfoSourceOrder('musicbrainz')[0]).toBe('musicbrainz_id');
    expect(artMapInfoSourceOrder('lastfm')).toEqual(artMapInfoSourceOrder('spotify'));
  });

  it('picks the first populated id in that order', () => {
    expect(artMapInfoBest(node(), 'itunes')).toEqual({ id: 'it', source: 'itunes' });
    expect(artMapInfoBest(node({ itunes_id: '' }), 'itunes')).toEqual({
      id: 'sp',
      source: 'spotify',
    });
    expect(
      artMapInfoBest(node({ musicbrainz_id: 'mb' } as Partial<ArtMapNode>), 'musicbrainz'),
    ).toEqual({ id: 'mb', source: 'musicbrainz' });
  });

  it('reaches discogs and musicbrainz, which the context menu never does', () => {
    const bare = node({
      spotify_id: '',
      itunes_id: '',
      deezer_id: '',
      discogs_id: 'dc',
      musicbrainz_id: '',
    });
    expect(artMapInfoBest(bare, 'spotify')).toEqual({ id: 'dc', source: 'discogs' });
  });

  it('gives an empty id and source when the node has nothing', () => {
    const bare = node({
      spotify_id: '',
      itunes_id: '',
      deezer_id: '',
      discogs_id: '',
      musicbrainz_id: '',
    });
    expect(artMapInfoBest(bare, 'spotify')).toEqual({ id: '', source: '' });
  });

  describe('related artists', () => {
    const a = node({ id: 1, name: 'A' });
    const b = node({ id: 2, name: 'B' });
    const c = node({ id: 3, name: 'C' });
    const byId: Record<string, ArtMapNode> = { 1: a, 2: b, 3: c };

    it('follows edges in BOTH directions', () => {
      const edges: ArtMapEdge[] = [
        { source: 1, target: 2 },
        { source: 3, target: 1 },
      ];
      expect(artMapRelatedNodes(a, edges, byId).map((n) => n.name)).toEqual(['B', 'C']);
    });

    it('de-duplicates a pair wired both ways', () => {
      const edges: ArtMapEdge[] = [
        { source: 1, target: 2 },
        { source: 2, target: 1 },
      ];
      expect(artMapRelatedNodes(a, edges, byId).map((n) => n.name)).toEqual(['B']);
    });

    it('de-duplicates a repeated edge in the SAME direction', () => {
      // Each branch has its own `relatedIds` guard, and only this shape
      // exercises the outgoing one — a both-ways pair is caught by the incoming
      // guard, so dropping the outgoing one survives that case.
      const edges: ArtMapEdge[] = [
        { source: 1, target: 2 },
        { source: 1, target: 2 },
      ];
      expect(artMapRelatedNodes(a, edges, byId).map((n) => n.name)).toEqual(['B']);
    });

    it('drops an edge whose other end was never placed', () => {
      const edges: ArtMapEdge[] = [{ source: 1, target: 99 }];
      expect(artMapRelatedNodes(a, edges, byId)).toEqual([]);
    });

    it('keeps edge order rather than sorting', () => {
      const edges: ArtMapEdge[] = [
        { source: 1, target: 3 },
        { source: 1, target: 2 },
      ];
      expect(artMapRelatedNodes(a, edges, byId).map((n) => n.name)).toEqual(['C', 'B']);
    });

    it('includes a self-edge as its own relation', () => {
      // `source === node.id` and `target === node.id` are separate `if`s, so a
      // self-edge adds the node once via the first branch.
      const edges: ArtMapEdge[] = [{ source: 1, target: 1 }];
      expect(artMapRelatedNodes(a, edges, byId).map((n) => n.name)).toEqual(['A']);
    });
  });

  it('builds the pool entry the Your Artists modal expects', () => {
    const related = [node({ id: 9, name: 'Related' })];
    expect(artMapPoolEntry(node({ type: 'watchlist' }), 'spotify', related)).toEqual({
      id: 3,
      artist_name: 'Aphex Twin',
      active_source_id: 'sp',
      active_source: 'spotify',
      image_url: '/a.jpg',
      spotify_artist_id: 'sp',
      itunes_artist_id: 'it',
      deezer_artist_id: 'dz',
      discogs_artist_id: 'dc',
      source_services: [],
      on_watchlist: 1,
      _related: related,
    });
  });

  it('resolves active_source_id through the ACTIVE source, not always spotify', () => {
    const entry = artMapPoolEntry(node(), 'deezer', []);
    expect(entry.active_source_id).toBe('dz');
    expect(entry.active_source).toBe('deezer');
    // …while the per-provider columns stay as they are.
    expect(entry.spotify_artist_id).toBe('sp');
  });

  it('flags on_watchlist as 0/1, not a boolean', () => {
    expect(artMapPoolEntry(node({ type: 'similar' }), 'spotify', []).on_watchlist).toBe(0);
    expect(artMapPoolEntry(node({ type: 'center' }), 'spotify', []).on_watchlist).toBe(0);
  });

  it('carries NO musicbrainz id — the pool shape has only four', () => {
    // The modal's row shape predates musicbrainz, so the id is resolved for
    // active_source_id but has nowhere of its own to live.
    expect(artMapPoolEntry(node(), 'spotify', [])).not.toHaveProperty('musicbrainz_artist_id');
  });
});
