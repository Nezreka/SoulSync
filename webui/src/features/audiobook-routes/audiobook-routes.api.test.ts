import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  fetchFolderConfigured,
  fetchRoutedNames,
  moveExistingAlbums,
  refreshAudiobookMetadata,
  setAudiobookRoute,
  summariseMove,
} from './audiobook-routes.api';

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

type Seen = { method: string; path: string; body: unknown };

function stubFetch(reply: (seen: Seen) => Response) {
  const calls: Seen[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      const text = await request.text();
      const seen = {
        method: request.method,
        path: new URL(request.url).pathname,
        body: text ? JSON.parse(text) : null,
      };
      calls.push(seen);
      return reply(seen);
    }),
  );
  return calls;
}

describe('audiobook route requests', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('asks the server which names are routed, in one call', async () => {
    const calls = stubFetch(() => jsonResponse({ success: true, routed: ['Bobby & Max'] }));
    expect(await fetchRoutedNames(['Queen', 'Bobby & Max'])).toEqual(['Bobby & Max']);
    expect(calls).toEqual([
      {
        method: 'POST',
        path: '/api/audiobook-routes/check',
        body: { names: ['Queen', 'Bobby & Max'] },
      },
    ]);
  });

  it('reads whether an audiobook folder is configured', async () => {
    const calls = stubFetch(() =>
      jsonResponse({ success: true, artists: [], audiobook_folder_configured: false }),
    );
    expect(await fetchFolderConfigured()).toBe(false);
    expect(calls).toEqual([{ method: 'GET', path: '/api/audiobook-routes', body: null }]);
  });

  it('does not call the server for an empty list', async () => {
    const calls = stubFetch(() => jsonResponse({ success: true, routed: [] }));
    expect(await fetchRoutedNames([])).toEqual([]);
    expect(calls).toHaveLength(0);
  });

  it('routes with POST and unroutes with DELETE, encoding the name', async () => {
    const calls = stubFetch(() => jsonResponse({ success: true }));
    await setAudiobookRoute('Bobby & Max', true);
    await setAudiobookRoute('Bobby & Max', false);
    expect(calls[0]).toEqual({
      method: 'POST',
      path: '/api/audiobook-routes',
      body: { name: 'Bobby & Max' },
    });
    expect(calls[1]?.method).toBe('DELETE');
    expect(calls[1]?.path).toBe('/api/audiobook-routes/Bobby%20%26%20Max');
  });

  it('surfaces the server error when a change is refused', async () => {
    stubFetch(() => jsonResponse({ success: false, error: 'name is required' }));
    await expect(setAudiobookRoute('', true)).rejects.toThrow('name is required');
  });

  it('previews a move by default and performs it only when asked', async () => {
    const calls = stubFetch(() =>
      jsonResponse({ success: true, applied: false, items: [], moved: 0, skipped: 0, failed: 0 }),
    );
    await moveExistingAlbums('Bobby & Max', false);
    await moveExistingAlbums('Bobby & Max', true);
    expect(calls.map((call) => call.body)).toEqual([{ apply: false }, { apply: true }]);
    expect(calls[0]?.path).toBe('/api/audiobook-routes/Bobby%20%26%20Max/move');
  });

  it('asks the server to write the metadata for the artist', async () => {
    const calls = stubFetch(() => jsonResponse({ success: true, written: 2, unchanged: 1 }));
    expect(await refreshAudiobookMetadata('Bobby & Max')).toEqual({
      success: true,
      written: 2,
      unchanged: 1,
    });
    expect(calls[0]).toEqual({
      method: 'POST',
      path: '/api/audiobook-routes/Bobby%20%26%20Max/refresh',
      body: null,
    });
  });

  it('counts what a move would relocate and what it would leave alone', () => {
    expect(
      summariseMove({
        applied: false,
        moved: 0,
        skipped: 1,
        failed: 0,
        items: [
          { src: 'a', dst: 'b', action: 'move' },
          { src: 'c', dst: 'd', action: 'move' },
          { src: 'e', dst: 'f', action: 'exists' },
        ],
      }),
    ).toEqual({ toMove: 2, clashes: 1 });
  });
});
