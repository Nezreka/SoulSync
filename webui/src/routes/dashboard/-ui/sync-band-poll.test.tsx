/**
 * The sync band reloads every 4s while a sync runs. On a busy server one load
 * took 6s, so loads stacked on top of each other and added to the load the
 * sync was already under. A load starts only once the last one is done.
 */

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SyncBand } from './sync-band';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('the sync band polls one load at a time', () => {
  it('a reload while the last is still out does not start another', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      calls.push(url);
      await gate;
      return new Response('[]', { headers: { 'Content-Type': 'application/json' } });
    });
    render(<SyncBand />);
    const mirrored = () => calls.filter((u) => u.includes('/api/mirrored-playlists')).length;
    const history = () => calls.filter((u) => u.includes('/api/sync/history')).length;
    await vi.waitFor(() => expect(mirrored()).toBe(1));
    const historyBefore = history();
    window.dispatchEvent(new Event('focus'));
    window.dispatchEvent(new Event('focus'));
    expect(mirrored()).toBe(1);
    expect(history()).toBe(historyBefore);

    // once it lands, the next reload goes out again
    release();
    await vi.waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 0));
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => expect(mirrored()).toBe(2));
  });
});
