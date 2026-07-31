import { renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { SearchAlbum, SearchTrack } from './-search.types';

import { albumIdentity, trackIdentity } from './-search.helpers';
import { ownershipFromResponse, useLibraryCheck } from './-search.use-library-check';
import { EMPTY_OWNERSHIP } from './-ui/search-results';

const album = (over: Partial<SearchAlbum> = {}): SearchAlbum => ({
  id: 'a1',
  name: 'Drukqs',
  artist: 'Aphex Twin',
  ...over,
});

const track = (over: Partial<SearchTrack> = {}): SearchTrack => ({
  id: 't1',
  name: 'Xtal',
  artist: 'Aphex Twin',
  ...over,
});

describe('ownershipFromResponse', () => {
  it('keys album answers by identity, not by document position', () => {
    // The response is in REQUEST order and interleaves albums with singles; the
    // rendered list is grouped. Indexing the DOM badges an album you do not own.
    const rows = [
      album({ id: 'A1', album_type: 'album' }),
      album({ id: 'S1', album_type: 'single' }),
      album({ id: 'A2', album_type: 'album' }),
    ];
    const state = ownershipFromResponse(rows, [], { albums: [false, true, false] });
    expect([...state.ownedAlbums]).toEqual([albumIdentity(rows[1])]);
  });

  it('splits tracks into owned and wished, never both', () => {
    const rows = [track({ id: 't1' }), track({ id: 't2' }), track({ id: 't3' })];
    const state = ownershipFromResponse([], rows, {
      tracks: [
        { in_library: true, file_path: '/m/a.flac' },
        { in_wishlist: true },
        // in_library wins outright — the vanilla's else-if.
        { in_library: true, in_wishlist: true, file_path: '/m/c.flac' },
      ],
    });

    expect(state.ownedTracks.has(trackIdentity(rows[0]))).toBe(true);
    expect(state.wishlistTracks.has(trackIdentity(rows[1]))).toBe(true);
    expect(state.ownedTracks.has(trackIdentity(rows[2]))).toBe(true);
    expect(state.wishlistTracks.has(trackIdentity(rows[2]))).toBe(false);
  });

  it('keeps the whole library row, for what playLibraryTrack needs', () => {
    const rows = [track()];
    const row = {
      in_library: true,
      track_id: 42,
      title: 'Xtal',
      file_path: '/m/xtal.flac',
      album_title: 'SAW 85-92',
      artist_name: 'Aphex Twin',
      album_thumb_url: 'https://cdn/t.jpg',
    };
    const state = ownershipFromResponse([], rows, { tracks: [row] });
    expect(state.libraryTracks.get(trackIdentity(rows[0]))).toEqual(row);
  });

  it('does not offer to play an owned track with no local file', () => {
    // Owned on a Plex-only entry: badge it, but there is nothing to play.
    const rows = [track()];
    const state = ownershipFromResponse([], rows, { tracks: [{ in_library: true }] });
    expect(state.ownedTracks.size).toBe(1);
    expect(state.libraryTracks.size).toBe(0);
  });

  it('ignores answers past the end of the list it asked about', () => {
    const state = ownershipFromResponse([album()], [], { albums: [true, true, true] });
    expect(state.ownedAlbums.size).toBe(1);
  });

  it('owns nothing when the check answered nothing', () => {
    const state = ownershipFromResponse([album()], [track()], null);
    expect(state.ownedAlbums.size).toBe(0);
    expect(state.ownedTracks.size).toBe(0);
  });
});

describe('useLibraryCheck', () => {
  it('asks with names and artists only, and badges what came back', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('/api/enhanced-search/library-check', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ albums: [true], tracks: [{ in_wishlist: true }] });
      }),
    );

    const albums = [album()];
    const tracks = [track()];
    const { result } = renderHook(() => useLibraryCheck(albums, tracks));

    await waitFor(() => expect(result.current.ownedAlbums.size).toBe(1));
    expect(result.current.wishlistTracks.size).toBe(1);
    // Ids are useless here: the check matches the local database by name.
    expect(body).toEqual({
      albums: [{ name: 'Drukqs', artist: 'Aphex Twin' }],
      tracks: [{ name: 'Xtal', artist: 'Aphex Twin' }],
    });
  });

  it('asks nothing, and re-renders nothing, when there is nothing on screen', async () => {
    let asked = false;
    server.use(
      http.post('/api/enhanced-search/library-check', () => {
        asked = true;
        return HttpResponse.json({});
      }),
    );

    const { result } = renderHook(() => useLibraryCheck([], []));
    await new Promise((r) => setTimeout(r, 20));
    expect(asked).toBe(false);
    // The SAME object, not an equal one. Handing back a fresh empty state on
    // every empty query re-renders the whole result surface for nothing —
    // React bails out only when the identity is unchanged.
    expect(result.current).toBe(EMPTY_OWNERSHIP);
  });

  it('leaves every card unbadged when the check fails', async () => {
    // Best effort by design: an unbadged owned album is a small loss, an error
    // state over the whole result list is not.
    server.use(
      http.post(
        '/api/enhanced-search/library-check',
        () => new HttpResponse(null, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useLibraryCheck([album()], [track()]));
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.ownedAlbums.size).toBe(0);
  });

  it('does not apply an answer to a result set that has been replaced', async () => {
    // The classic stale-response bug: badges from the previous query landing on
    // the new one's cards.
    const pending: (() => void)[] = [];
    server.use(
      http.post('/api/enhanced-search/library-check', () => {
        return new Promise<Response>((resolve) => {
          pending.push(() => resolve(HttpResponse.json({ albums: [true] })));
        });
      }),
    );

    const first = [album({ id: 'first' })];
    const { result, rerender, unmount } = renderHook(
      ({ albums }: { albums: SearchAlbum[] }) => useLibraryCheck(albums, []),
      { initialProps: { albums: first } },
    );
    await waitFor(() => expect(pending).toHaveLength(1));

    rerender({ albums: [album({ id: 'second' })] });
    pending[0]();
    await new Promise((r) => setTimeout(r, 20));

    expect(result.current.ownedAlbums.has(albumIdentity(first[0]))).toBe(false);
    unmount();
  });

  it('does not re-ask when a render passes the same rows in a new array', async () => {
    // The page rebuilds its result arrays freely — emptySourceResults() mints new
    // ones on every read. Keying the effect on array identity turns each render
    // into another round trip.
    let asks = 0;
    server.use(
      http.post('/api/enhanced-search/library-check', () => {
        asks += 1;
        return HttpResponse.json({ albums: [false] });
      }),
    );

    const { rerender } = renderHook(
      ({ albums }: { albums: SearchAlbum[] }) => useLibraryCheck(albums, []),
      { initialProps: { albums: [album()] } },
    );
    await waitFor(() => expect(asks).toBe(1));

    // Same album, brand new array and object.
    rerender({ albums: [album()] });
    rerender({ albums: [album()] });
    await new Promise((r) => setTimeout(r, 20));
    expect(asks).toBe(1);
  });
});
