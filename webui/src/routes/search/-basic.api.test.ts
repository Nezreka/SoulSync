import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  fetchBasicSources,
  isSingleSourceMode,
  performBasicSearch,
  postDownload,
  sourceLabel,
} from './-basic.api';

describe('fetchBasicSources', () => {
  it('returns the mode and the source list', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'hybrid',
          sources: [
            { name: 'soulseek', display_name: 'Soulseek' },
            { name: 'tidal', display_name: 'Tidal' },
          ],
        }),
      ),
    );
    const data = await fetchBasicSources();
    expect(data.mode).toBe('hybrid');
    expect(data.sources.map((s) => s.name)).toEqual(['soulseek', 'tidal']);
  });

  it('leaves the row empty rather than throwing when the endpoint is down', async () => {
    // The picker is a convenience; search works without it, so a failure here
    // must not take the page with it (downloads.js:4280).
    server.use(http.get('/api/search/sources', () => HttpResponse.error()));
    await expect(fetchBasicSources()).resolves.toEqual({ mode: '', sources: [] });
  });

  it('tolerates a response with neither key', async () => {
    server.use(http.get('/api/search/sources', () => HttpResponse.json({})));
    await expect(fetchBasicSources()).resolves.toEqual({ mode: '', sources: [] });
  });
});

describe('isSingleSourceMode', () => {
  it('is a picker only in hybrid mode with something to pick between', () => {
    const two = [
      { name: 'soulseek', display_name: 'Soulseek' },
      { name: 'tidal', display_name: 'Tidal' },
    ];
    expect(isSingleSourceMode({ mode: 'hybrid', sources: two })).toBe(false);
    expect(isSingleSourceMode({ mode: 'hybrid', sources: two.slice(0, 1) })).toBe(true);
    expect(isSingleSourceMode({ mode: 'soulseek', sources: two })).toBe(true);
    expect(isSingleSourceMode({ mode: '', sources: [] })).toBe(true);
  });
});

describe('performBasicSearch', () => {
  it('sends the query and the chosen source', async () => {
    let body: unknown;
    server.use(
      http.post('/api/search', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ results: [] });
      }),
    );
    await performBasicSearch('aphex', 'tidal');
    expect(body).toEqual({ query: 'aphex', source: 'tidal' });
  });

  it('omits the source when there is none, so the server picks', async () => {
    // Sending `source: null` or '' is not the same as omitting it — the server
    // reads a present-but-empty source as a request it cannot route.
    const bodies: unknown[] = [];
    server.use(
      http.post('/api/search', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ results: [] });
      }),
    );
    await performBasicSearch('aphex', null);
    await performBasicSearch('aphex', '');
    expect(bodies).toEqual([{ query: 'aphex' }, { query: 'aphex' }]);
  });

  it('returns the results', async () => {
    server.use(
      http.post('/api/search', () =>
        HttpResponse.json({ results: [{ result_type: 'track', title: 'Xtal' }] }),
      ),
    );
    const results = await performBasicSearch('aphex', null);
    expect(results).toHaveLength(1);
  });

  it('returns an empty list when the server sends no results key', async () => {
    server.use(http.post('/api/search', () => HttpResponse.json({})));
    await expect(performBasicSearch('aphex', null)).resolves.toEqual([]);
  });

  it('throws the server error message so the status bar can show it', async () => {
    // e.g. a SoundCloud link pasted with the source disabled — the user needs
    // that sentence, not a generic failure.
    server.use(
      http.post('/api/search', () =>
        HttpResponse.json({ error: "SoundCloud isn't connected — enable it in Settings." }),
      ),
    );
    await expect(performBasicSearch('https://soundcloud.com/x', null)).rejects.toThrow(
      "SoundCloud isn't connected",
    );
  });

  it('propagates an abort rather than resolving empty', async () => {
    // The controller tells a cancelled search apart from a failed one by the
    // rejection; swallowing it here would report "no results" on every cancel.
    server.use(http.post('/api/search', () => HttpResponse.json({ results: [] })));
    const controller = new AbortController();
    controller.abort();
    await expect(performBasicSearch('aphex', null, controller.signal)).rejects.toThrow();
  });
});

describe('postDownload', () => {
  it('posts the whole result object untouched', async () => {
    // The server picks fields off the body with .get(); it never reconstructs
    // a dataclass, so passing the result through is the contract.
    let body: Record<string, unknown> | undefined;
    server.use(
      http.post('/api/download', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true });
      }),
    );
    const payload = {
      result_type: 'track' as const,
      username: 'peer',
      filename: 'a.flac',
      size: 100,
      title: 'Xtal',
    };
    await postDownload(payload);
    expect(body).toMatchObject(payload);
  });

  it('surfaces the failure body rather than throwing', async () => {
    server.use(
      http.post('/api/download', () => HttpResponse.json({ success: false, error: 'nope' })),
    );
    await expect(postDownload({ result_type: 'track' })).resolves.toEqual({
      success: false,
      error: 'nope',
    });
  });
});

describe('sourceLabel', () => {
  it('prefers the display name and falls back to the raw name', () => {
    expect(sourceLabel({ name: 'soulseek', display_name: 'Soulseek' })).toBe('Soulseek');
    expect(sourceLabel({ name: 'soulseek', display_name: '' })).toBe('soulseek');
  });
});
