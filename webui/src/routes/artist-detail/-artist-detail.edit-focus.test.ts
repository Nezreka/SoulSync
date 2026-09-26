import { afterEach, describe, expect, it, vi } from 'vitest';

import { takeFindingsFocus, requestFindingsFocus } from '../tools/-tools.findings-focus';
import {
  albumMatchesEdit,
  clearArtistEdit,
  peekArtistEdit,
  requestArtistEdit,
} from './-artist-detail.edit-focus';

describe('issue hand-offs', () => {
  afterEach(() => {
    clearArtistEdit();
    vi.useRealTimers();
  });

  it('only the asked artist sees the edit ask, and it goes stale', () => {
    vi.useFakeTimers();
    requestArtistEdit({ artistId: 7, albumId: 12 } as never);
    expect(peekArtistEdit('8')).toBeNull();
    expect(peekArtistEdit(7)).toMatchObject({ artistId: '7', albumId: '12' });
    vi.advanceTimersByTime(61_000);
    expect(peekArtistEdit(7)).toBeNull();
  });

  it('an album answers by its id or by holding the track', () => {
    const byAlbum = { artistId: '1', albumId: '12' };
    const byTrack = { artistId: '1', trackId: '99' };
    expect(albumMatchesEdit(byAlbum, { id: 12, tracks: [] })).toBe(true);
    expect(albumMatchesEdit(byAlbum, { id: 13, tracks: [] })).toBe(false);
    expect(albumMatchesEdit(byTrack, { id: 13, tracks: [{ id: 99 }] })).toBe(true);
    expect(albumMatchesEdit(null, { id: 12 })).toBe(false);
  });

  it('the findings ask is used up once', () => {
    requestFindingsFocus({ jobId: 'duplicate_detector', query: ' Song ' });
    expect(takeFindingsFocus()).toEqual({ jobId: 'duplicate_detector', query: 'Song' });
    expect(takeFindingsFocus()).toBeNull();
  });
});
