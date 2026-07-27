import { describe, expect, it, vi } from 'vitest';

import {
  artistDetailQueryOptions,
  isSourceOnlyArtist,
  needsCompletionStream,
  readArtistDetail,
  settleOwnershipForSourceArtist,
} from './-artist-detail.api';
import { artistDetailSearchSchema, normalizeSource } from './-artist-detail.types';

describe('normalizeSource', () => {
  it("treats 'library' as no source, since that means the local DB", () => {
    expect(normalizeSource('library')).toBeNull();
    expect(normalizeSource('LIBRARY')).toBeNull();
    expect(normalizeSource('')).toBeNull();
  });

  it('lowercases a real metadata source', () => {
    expect(normalizeSource('Spotify')).toBe('spotify');
  });
});

describe('artistDetailSearchSchema', () => {
  it('coerces an all-digits artist name back to a string', () => {
    // TanStack JSON-parses search values, so "311" arrives as a NUMBER and a
    // bare z.string() throws SearchParamError — the route dies and clicking
    // the artist appears to do nothing. This has regressed once before.
    expect(artistDetailSearchSchema.parse({ name: 311 })).toEqual({ name: '311' });
  });

  it('defaults to an empty name when absent', () => {
    expect(artistDetailSearchSchema.parse({})).toEqual({ name: '' });
  });
});

describe('artistDetailQueryOptions', () => {
  it('sends no query params at all for a library artist', () => {
    // The vanilla loader only set() source/name when non-empty, so a library
    // request carried no query string. Sending empties would also fragment
    // the cache key.
    const opts = artistDetailQueryOptions('library', '42', '');
    expect(opts.queryKey).toEqual(['artist-detail', 'library', '42', '']);
  });

  it('omits empty source and name from the REQUEST, not just the key', async () => {
    // The query key is derived separately, so it cannot detect this — the URL
    // has to be observed. The vanilla loader built its params with conditional
    // set() calls and sent no query string for a plain library artist.
    const seen: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        seen.push(input instanceof Request ? input.url : String(input));
        return new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    try {
      await artistDetailQueryOptions('library', '42', '').queryFn!({} as never);
      expect(new URL(seen[0]).search).toBe('');

      await artistDetailQueryOptions('spotify', 'abc', '311').queryFn!({} as never);
      const params = new URL(seen[1]).searchParams;
      expect(params.get('source')).toBe('spotify');
      expect(params.get('name')).toBe('311');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keys separately per source, id and name', () => {
    const a = artistDetailQueryOptions('spotify', 'abc', 'Aphex Twin');
    const b = artistDetailQueryOptions('deezer', 'abc', 'Aphex Twin');
    expect(a.queryKey).not.toEqual(b.queryKey);
  });
});

describe('readArtistDetail', () => {
  it('surfaces the server reason on success:false', () => {
    expect(() => readArtistDetail({ success: false, error: 'Artist not found' })).toThrow(
      'Artist not found',
    );
  });

  it('falls back to a generic message when no reason is given', () => {
    expect(() => readArtistDetail({ success: false })).toThrow('Failed to load artist data');
    expect(() => readArtistDetail(undefined)).toThrow('Failed to load artist data');
  });

  it('passes a successful payload straight through', () => {
    const payload = { success: true, artist: { name: 'Aphex Twin' } };
    expect(readArtistDetail(payload)).toBe(payload);
  });
});

describe('settleOwnershipForSourceArtist', () => {
  it('turns unknown ownership into false across every bucket', () => {
    // A source-only artist has no library to check against, so anything left
    // as null would render as "checking" forever.
    const settled = settleOwnershipForSourceArtist({
      albums: [{ owned: null }, { owned: true }],
      eps: [{}],
      singles: [{ owned: false }],
    });
    expect(settled.albums?.map((r) => r.owned)).toEqual([false, true]);
    expect(settled.eps?.map((r) => r.owned)).toEqual([false]);
    expect(settled.singles?.map((r) => r.owned)).toEqual([false]);
  });

  it('does not mutate the cached response', () => {
    const original = { albums: [{ owned: null }] };
    settleOwnershipForSourceArtist(original);
    expect(original.albums[0].owned).toBeNull();
  });

  it('leaves a missing bucket missing rather than inventing an empty one', () => {
    expect(settleOwnershipForSourceArtist({ albums: [] }).eps).toBeUndefined();
  });
});

describe('isSourceOnlyArtist', () => {
  it('keys off server_source, which is what the backend omits for source artists', () => {
    expect(isSourceOnlyArtist({ artist: { name: 'x' } })).toBe(true);
    expect(isSourceOnlyArtist({ artist: { name: 'x', server_source: 'plex' } })).toBe(false);
    expect(isSourceOnlyArtist({})).toBe(true);
  });
});

describe('needsCompletionStream', () => {
  const library = { artist: { server_source: 'plex' } };

  it('runs when a library discography still has unknown ownership', () => {
    expect(
      needsCompletionStream({ ...library, discography: { albums: [{ owned: null }] } }),
    ).toBe(true);
  });

  it('does not run once everything is resolved', () => {
    expect(
      needsCompletionStream({ ...library, discography: { albums: [{ owned: true }] } }),
    ).toBe(false);
  });

  it('finds unknowns in eps and singles too, not just albums', () => {
    expect(
      needsCompletionStream({
        ...library,
        discography: { albums: [{ owned: true }], singles: [{ owned: null }] },
      }),
    ).toBe(true);
  });

  it('never runs for a source-only artist', () => {
    // There is no library to check against — the stream could never report.
    expect(needsCompletionStream({ discography: { albums: [{ owned: null }] } })).toBe(false);
  });

  it('does not run when the discography has no albums key at all', () => {
    expect(needsCompletionStream({ ...library, discography: { singles: [{ owned: null }] } })).toBe(
      false,
    );
  });
});
