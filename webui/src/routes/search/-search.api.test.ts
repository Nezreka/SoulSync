import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  fetchArtistImage,
  fetchConfigStatus,
  fetchEnhancedSearch,
  fetchLabels,
  fetchLibraryCheck,
  lookupById,
  streamVideoSearch,
} from './-search.api';

/** NDJSON body from a list of chunk payloads, optionally split mid-line. */
function ndjson(lines: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
        else controller.close();
      },
    }),
    { status: 200 },
  );
}

describe('fetchEnhancedSearch', () => {
  it('sends the query and source', async () => {
    let body: unknown;
    server.use(
      http.post('/api/enhanced-search', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ spotify_albums: [] });
      }),
    );
    await fetchEnhancedSearch('aphex', 'deezer');
    expect(body).toEqual({ query: 'aphex', source: 'deezer' });
  });

  it('is abortable', async () => {
    server.use(http.post('/api/enhanced-search', () => new Promise(() => {})));
    const controller = new AbortController();
    const promise = fetchEnhancedSearch('aphex', 'spotify', controller.signal);
    controller.abort();
    await expect(promise).rejects.toThrow();
  });
});

describe('streamVideoSearch', () => {
  it('reports each chunk cumulatively as it arrives', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson([
          '{"type":"videos","data":[{"video_id":"a"}]}\n',
          '{"type":"videos","data":[{"video_id":"b"}]}\n',
        ]),
      ),
    );

    const chunks: number[] = [];
    const all = await streamVideoSearch('aphex', (videos) => chunks.push(videos.length));
    // Progressive: the grid fills in rather than appearing all at once.
    expect(chunks).toEqual([1, 2]);
    expect(all.map((v) => v.video_id)).toEqual(['a', 'b']);
  });

  it('holds back a line split across two chunks', async () => {
    // The obvious way to break NDJSON is to parse a fragment.
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson(['{"type":"videos","data":[{"video_id"', ':"split"}]}\n']),
      ),
    );
    const all = await streamVideoSearch('aphex', () => {});
    expect(all.map((v) => v.video_id)).toEqual(['split']);
  });

  it('skips a malformed line without killing the stream', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson(['not json\n', '{"type":"videos","data":[{"video_id":"ok"}]}\n']),
      ),
    );
    const all = await streamVideoSearch('aphex', () => {});
    expect(all.map((v) => v.video_id)).toEqual(['ok']);
  });

  it('ignores chunks that are not videos', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson(['{"type":"progress","data":[{"video_id":"nope"}]}\n']),
      ),
    );
    expect(await streamVideoSearch('aphex', () => {})).toEqual([]);
  });

  it('returns nothing on a failed response rather than throwing', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        HttpResponse.json({}, { status: 500 }),
      ),
    );
    await expect(streamVideoSearch('aphex', () => {})).resolves.toEqual([]);
  });
});

describe('fetchLibraryCheck', () => {
  it('sends name/artist per row and keeps request order', async () => {
    let body: { albums: { name?: string }[] } | undefined;
    server.use(
      http.post('/api/enhanced-search/library-check', async ({ request }) => {
        body = (await request.json()) as { albums: { name?: string }[] };
        return HttpResponse.json({ albums: [true, false] });
      }),
    );

    const result = await fetchLibraryCheck(
      [
        { name: 'One', artist: 'A' },
        { name: 'Two', artist: 'B' },
      ],
      [],
    );
    expect(body?.albums.map((a) => a.name)).toEqual(['One', 'Two']);
    expect(result.albums).toEqual([true, false]);
  });

  it('does not call out for an empty batch', async () => {
    let called = false;
    server.use(
      http.post('/api/enhanced-search/library-check', () => {
        called = true;
        return HttpResponse.json({});
      }),
    );
    await fetchLibraryCheck([], []);
    expect(called).toBe(false);
  });

  it('leaves everything unbadged when it fails', async () => {
    // A failure must not be read as "nothing is owned".
    server.use(http.post('/api/enhanced-search/library-check', () => HttpResponse.error()));
    await expect(fetchLibraryCheck([{ name: 'x' }], [])).resolves.toEqual({});
  });
});

describe('fetchLabels', () => {
  it('unwraps the labels array', async () => {
    server.use(
      http.post('/api/labels/search', () =>
        HttpResponse.json({ labels: [{ id: 'l1', name: 'Warp' }] }),
      ),
    );
    expect(await fetchLabels('warp')).toHaveLength(1);
  });

  it('is empty on failure — the section just does not appear', async () => {
    server.use(http.post('/api/labels/search', () => HttpResponse.error()));
    expect(await fetchLabels('warp')).toEqual([]);
  });
});

describe('fetchArtistImage', () => {
  it('passes source and name through and returns the url', async () => {
    let search = '';
    server.use(
      http.get('/api/artist/:id/image', ({ request }) => {
        search = new URL(request.url).search;
        return HttpResponse.json({ success: true, image_url: 'https://cdn/a.jpg' });
      }),
    );
    expect(await fetchArtistImage(42, 'deezer', 'Aphex Twin')).toBe('https://cdn/a.jpg');
    expect(search).toContain('source=deezer');
    expect(search).toContain('name=Aphex+Twin');
  });

  it('is empty rather than throwing when there is no image', async () => {
    server.use(http.get('/api/artist/:id/image', () => HttpResponse.json({ success: false })));
    expect(await fetchArtistImage(42, 'spotify', 'X')).toBe('');
  });
});

describe('fetchConfigStatus', () => {
  it('applies the vanilla per-source rule, not one flattened expression', async () => {
    // The rule differs BY SOURCE, and my first version flattened it:
    //   - credential-free sources are always usable
    //   - spotify is configured OR metadata_available (Spotify Free)
    //   - everything else is `configured` alone
    server.use(
      http.get('/api/settings/config-status', () =>
        HttpResponse.json({
          spotify: { configured: false, metadata_available: true },
          deezer: { configured: false, metadata_available: true },
          itunes: { configured: true },
        }),
      ),
    );
    const { configured } = await fetchConfigStatus();

    // Spotify Free: no credentials, still usable.
    expect(configured.spotify).toBe(true);
    // Deezer does NOT get that latitude — metadata_available must not light it up.
    expect(configured.deezer).toBe(false);
    expect(configured.itunes).toBe(true);
    // Credential-free sources are usable without appearing in the payload.
    expect(configured.musicbrainz).toBe(true);
    expect(configured.amazon).toBe(true);
  });

  it('reads the experimental flags off the same payload', async () => {
    // They ride config-status, so fetching them separately would be a second
    // round-trip for data already in hand.
    server.use(
      http.get('/api/settings/config-status', () =>
        HttpResponse.json({ _experimental: { bandcamp_enabled: true, jiosaavn_enabled: false } }),
      ),
    );
    const { enabledExperimental, configured } = await fetchConfigStatus();
    expect(enabledExperimental.has('bandcamp')).toBe(true);
    expect(enabledExperimental.has('jiosaavn')).toBe(false);
    // And an enabled experimental source is then present in the picker map.
    expect(configured.bandcamp).toBe(true);
    expect('jiosaavn' in configured).toBe(false);
  });

  it('stays optimistic on failure, so the picker does not look broken', async () => {
    server.use(http.get('/api/settings/config-status', () => HttpResponse.error()));
    expect(await fetchConfigStatus()).toEqual({
      configured: {},
      enabledExperimental: new Set(),
    });
  });
});

describe('lookupById', () => {
  it('posts the raw string and returns the availability verdict', async () => {
    let body: unknown;
    server.use(
      http.post('/api/enhanced-search/by-id', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ available: false, message: 'Nothing there' });
      }),
    );
    const data = await lookupById('770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce');
    expect(body).toEqual({ query: '770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce' });
    expect(data.available).toBe(false);
    expect(data.message).toBe('Nothing there');
  });
});

afterEach(() => vi.restoreAllMocks());
