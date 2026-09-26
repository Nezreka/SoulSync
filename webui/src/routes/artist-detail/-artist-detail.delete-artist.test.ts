import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  deleteArtistMessage,
  deleteArtistRequest,
  deleteArtistScope,
  deleteArtistToast,
} from './-artist-detail.delete-artist';

afterEach(() => vi.unstubAllGlobals());

describe('the scope phrase', () => {
  it('pluralises each half independently', () => {
    expect(deleteArtistScope(45, 234)).toBe('45 albums and 234 tracks');
    expect(deleteArtistScope(1, 1)).toBe('1 album and 1 track');
    expect(deleteArtistScope(1, 12)).toBe('1 album and 12 tracks');
    expect(deleteArtistScope(0, 0)).toBe('0 albums and 0 tracks');
  });
});

describe('the confirm copy', () => {
  it('names the artist and promises no files are touched', () => {
    const message = deleteArtistMessage('Don Felder');
    expect(message).toContain('Don Felder');
    // the whole point of the box: "delete" beside a music library reads as
    // "erase my files"
    expect(message).toContain('Not one file on disk is deleted');
  });

  it('quotes no counts it cannot stand behind', () => {
    // the payload has no album count; "0 albums" would be a lie
    expect(deleteArtistMessage('Don Felder')).not.toMatch(/\d+ albums?/);
  });
});

describe('the toast', () => {
  it('reports the real counts the server returned', () => {
    const toast = deleteArtistToast('Don Felder', {
      albums_deleted: 45,
      tracks_deleted: 234,
    });
    expect(toast).toContain('45 albums and 234 tracks');
    expect(toast).toContain('No files were deleted');
  });

  it('warns when the artist will come back on the next scan', () => {
    const toast = deleteArtistToast('Don Felder', {
      albums_deleted: 1,
      tracks_deleted: 2,
      returns_on_rescan: true,
    });
    expect(toast).toContain('scan will add it back');
  });

  it('stays quiet about rescans for a soulsync-owned artist', () => {
    const toast = deleteArtistToast('Don Felder', {
      albums_deleted: 1,
      tracks_deleted: 2,
      returns_on_rescan: false,
    });
    expect(toast).not.toContain('scan will add it back');
  });
});

describe('the request', () => {
  it('DELETEs the artist endpoint and returns the payload', async () => {
    const fetchSpy = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ success: true, albums_deleted: 3 })),
    );
    vi.stubGlobal('fetch', fetchSpy);

    const result = await deleteArtistRequest(42);

    expect(fetchSpy.mock.calls[0][0]).toBe('/api/library/artist/42');
    expect(fetchSpy.mock.calls[0][1]).toMatchObject({ method: 'DELETE' });
    expect(result.albums_deleted).toBe(3);
  });

  it('encodes an id that would otherwise break the path', async () => {
    const fetchSpy = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ success: true })),
    );
    vi.stubGlobal('fetch', fetchSpy);

    await deleteArtistRequest('a/b');

    expect(fetchSpy.mock.calls[0][0]).toBe('/api/library/artist/a%2Fb');
  });

  it('raises the server reason rather than reporting a silent success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: false, error: 'Artist not found' }), {
            status: 404,
          }),
      ),
    );

    await expect(deleteArtistRequest(9)).rejects.toThrow('Artist not found');
  });

  it('raises on a non-JSON failure instead of throwing a parse error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>502</html>', { status: 502 })),
    );

    await expect(deleteArtistRequest(9)).rejects.toThrow('502');
  });
});
