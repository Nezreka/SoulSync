import { afterEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum, EnhancedTrack } from './-artist-detail.enhanced';

import {
  albumSourceId,
  buildExpectedTrack,
  buildWishlistPayload,
  importExistingTrackRequest,
  importStageText,
  totalDiscs,
  wishlistEnhancedMissingTrack,
} from './-artist-detail.missing-track';

/**
 * The missing-track layer: the wishlist payload shape, the expected-track
 * context the importer needs, and the request bodies.
 */

const ARTIST = { id: 42, name: 'Aphex Twin', imageUrl: 'a.jpg' };

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.openAddToWishlistModal;
  delete window.handleWishlistDownloadNow;
});

describe('buildWishlistPayload', () => {
  it('prefers metadata-source ids and the canonical track count', () => {
    const { albumData, wishlistTrack } = buildWishlistPayload(
      {
        id: 'row-1',
        spotify_track_id: 'sp-t',
        title: 'Xtal',
        duration: 293_000,
        track_number: 1,
      } as EnhancedTrack,
      {
        id: 7,
        title: 'SAW 85-92',
        year: 1992,
        record_type: 'album',
        api_track_count: 13,
        tracks: [{ id: 1 }],
      } as EnhancedAlbum,
      ARTIST,
    );
    expect(wishlistTrack.id).toBe('sp-t');
    expect(wishlistTrack.artists).toEqual([{ name: 'Aphex Twin' }]);
    expect(albumData.total_tracks).toBe(13);
    expect(albumData.release_date).toBe('1992-01-01');
  });

  it('falls back to the row id and the owned count', () => {
    const { albumData, wishlistTrack } = buildWishlistPayload(
      { id: 'row-1', track_number: 4 } as EnhancedTrack,
      { id: 7, tracks: [{ id: 1 }, { id: 2 }] } as EnhancedAlbum,
      ARTIST,
    );
    expect(wishlistTrack.id).toBe('row-1');
    expect(wishlistTrack.name).toBe('Track 4');
    expect(albumData.total_tracks).toBe(2);
    expect(albumData.release_date).toBe('');
  });
});

describe('wishlistEnhancedMissingTrack', () => {
  const ACTIONABLE = {
    id: 'r1',
    title: 'Xtal',
    _hasActionableContext: true,
  } as unknown as EnhancedTrack;
  const ALBUM = { id: 7, title: 'SAW 85-92', tracks: [] } as unknown as EnhancedAlbum;

  it('refuses a context-less row with the vanilla toast', async () => {
    window.showToast = vi.fn() as never;
    await wishlistEnhancedMissingTrack({ id: 'r1' } as EnhancedTrack, ALBUM, ARTIST);
    expect(window.showToast).toHaveBeenCalledWith(
      'This missing track needs metadata context before it can be wishlisted or downloaded.',
      'error',
    );
  });

  it('opens the shared modal with the track pre-unticked as unowned', async () => {
    const open = vi.fn(async (..._args: unknown[]) => {});
    window.openAddToWishlistModal = open as never;
    await wishlistEnhancedMissingTrack(ACTIONABLE, ALBUM, ARTIST);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0]?.[4]).toEqual({ Xtal: false });
  });

  it('download-now fires the follow-up shortly after the modal opens', async () => {
    vi.useFakeTimers();
    window.openAddToWishlistModal = vi.fn(async () => {}) as never;
    const downloadNow = vi.fn();
    window.handleWishlistDownloadNow = downloadNow as never;
    await wishlistEnhancedMissingTrack(ACTIONABLE, ALBUM, ARTIST, true);
    expect(downloadNow).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(150);
    expect(downloadNow).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

describe('the importer context', () => {
  it("the row's own fields win; its canonical _sourceTrack fills the gaps", () => {
    const expected = buildExpectedTrack(
      {
        title: 'Xtal',
        track_number: 1,
        _sourceTrack: {
          name: 'Xtal (canonical)',
          disc_number: 2,
          duration_ms: 293_000,
          spotify_track_id: 'sp-t',
          id: 'canon-1',
        },
      } as unknown as EnhancedTrack,
      'Aphex Twin',
    );
    expect(expected.title).toBe('Xtal');
    expect(expected.disc_number).toBe(2);
    expect(expected.duration_ms).toBe(293_000);
    expect(expected.spotify_track_id).toBe('sp-t');
    expect(expected.track_id).toBe('canon-1');
    expect(expected.artists).toEqual(['Aphex Twin']);
  });

  it('resolves the album source id in priority order and the disc count', () => {
    expect(albumSourceId({ deezer_id: 'dz', qobuz_id: 'q' } as EnhancedAlbum)).toBe('dz');
    expect(albumSourceId({} as EnhancedAlbum)).toBe('');
    expect(
      totalDiscs({
        tracks: [{ disc_number: 1 }, { disc_number: 3 }],
      } as unknown as EnhancedAlbum),
    ).toBe(3);
    expect(
      totalDiscs({
        canonical_tracks: [{ disc_number: 2 }],
        tracks: [{ disc_number: 1 }],
      } as unknown as EnhancedAlbum),
    ).toBe(2);
    expect(totalDiscs({ tracks: [] } as unknown as EnhancedAlbum)).toBe(1);
  });

  it('posts the full import body and gates updated_data on ITS success flag', async () => {
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, updated_data: { success: false } })),
    );
    vi.stubGlobal('fetch', spy);
    const result = await importExistingTrackRequest(
      { id: 7, spotify_album_id: 'sp7', tracks: [{ disc_number: 1 }] } as unknown as EnhancedAlbum,
      { title: 'Xtal', track_number: 1 } as EnhancedTrack,
      'Aphex Twin',
      '99',
    );
    expect(String(spy.mock.calls[0]?.[0])).toBe('/api/library/album/7/import-existing-track');
    const body = JSON.parse(String(spy.mock.calls[0]?.[1]?.body));
    expect(body.source_track_id).toBe('99');
    expect(body.album_source_id).toBe('sp7');
    expect(body.total_discs).toBe(1);
    expect(body.expected_track.title).toBe('Xtal');
    // updated_data.success false → treated as absent, caller re-fetches.
    expect(result.updatedData).toBeNull();
  });
});

describe('importStageText', () => {
  it('walks the stages by elapsed seconds', () => {
    expect(importStageText(0)).toBe('Copying selected file into staging.');
    expect(importStageText(5)).toBe('Verifying audio and writing the missing track tags.');
    expect(importStageText(25)).toContain('Still working.');
  });
});
