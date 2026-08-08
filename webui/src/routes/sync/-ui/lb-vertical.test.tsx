/**
 * useListenBrainzVertical exists for ONE reason: the post-discovery mirror
 * (_mirrorListenBrainzAfterDiscovery, sync-services.js 10954, fired at
 * 11075/11170) went missing from the port entirely. This test is the guarantee
 * that building the ListenBrainz vertical this way still mirrors.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SYNC_DISCOVERY_EVENT } from '../-sync.use-vertical';
import { useListenBrainzVertical } from './lb-sync-tab';

let calls: { url: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
      // The series lookup answers "no match" so the mirror keeps the mbid.
      return new Response(JSON.stringify({ matched: false }));
    }),
  );
  (window as unknown as Record<string, unknown>).showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const MATCHED = [
  {
    spotify_data: {
      id: 't1',
      name: 'Alright',
      artists: [{ name: 'Kendrick Lamar' }],
      album: { name: 'TPAB' },
      duration_ms: 219000,
    },
  },
];

describe('useListenBrainzVertical', () => {
  it('mirrors the matched tracks when a discovery completes', async () => {
    const { result } = renderHook(() => useListenBrainzVertical());
    act(() =>
      result.current.hydrate('mbid-1', {
        playlist: { name: 'Weekly Jams', tracks: [], description: '5 tracks from Weekly Jams' },
        phase: 'discovering',
        results: MATCHED,
      }),
    );

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(SYNC_DISCOVERY_EVENT, {
          detail: { id: 'mbid-1', platform: 'listenbrainz', complete: true },
        }),
      );
      await Promise.resolve();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror, 'the mirror is the whole point of this hook').toBeDefined();
    const body = mirror?.body as Record<string, unknown>;
    expect(body.source).toBe('listenbrainz');
    expect(body.source_playlist_id).toBe('mbid-1');
    expect(body.name).toBe('Weekly Jams');
    expect(body.description).toBe('5 tracks from Weekly Jams');
    expect((body.tracks as unknown[]).length).toBe(1);
    // The series lookup runs first (11009) — it is what can rewrite the id.
    expect(calls.some((c) => c.url.startsWith('/api/listenbrainz/series-detect'))).toBe(true);
  });

  it('does not mirror a discovery that produced no matches (10964)', async () => {
    const { result } = renderHook(() => useListenBrainzVertical());
    act(() =>
      result.current.hydrate('mbid-2', {
        playlist: { name: 'Empty', tracks: [] },
        phase: 'discovering',
        results: [{ spotify_track: 'unmatched' }],
      }),
    );
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(SYNC_DISCOVERY_EVENT, {
          detail: { id: 'mbid-2', platform: 'listenbrainz', complete: true },
        }),
      );
      await Promise.resolve();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(calls.some((c) => c.url === '/api/mirror-playlist')).toBe(false);
  });
});
