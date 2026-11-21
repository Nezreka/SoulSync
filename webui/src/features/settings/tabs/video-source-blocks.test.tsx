/**
 * The torrent seeding goals' per-indexer rules (SETTINGS_PORT.md, video pass
 * V2a): Prowlarr's torrent indexers only, blank inherits the goal, 0 exempts
 * the tracker, and a rule saves the downloads config when it is left.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { videoDownloads } from './video-downloads';
import { VideoSeedingGoals, VideoUsenetBlocks } from './video-source-blocks';

let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoDownloads.reset();
  calls = [];
  answers = {
    'GET /api/video/downloads/config': { seed_overrides: { '7': { ratio: 2, hours: 24 } } },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined;
      calls.push({ url, method, body });
      const data = answers[`${method} ${url}`] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const rows = () => document.getElementById('video-seed-idx-rows')!;

describe('per-indexer seed rules', () => {
  it('says when Prowlarr is not set up', async () => {
    answers['GET /api/prowlarr/indexers'] = { success: false };
    render(<VideoSeedingGoals />);
    expect(rows().textContent).toBe('Load indexers to list the torrent indexers from Prowlarr.');
    fireEvent.click(document.getElementById('video-seed-idx-refresh')!);
    await waitFor(() =>
      expect(rows().textContent).toBe("Prowlarr isn't set up yet (Downloads, Sources, Indexers)."),
    );
  });

  it('lists the torrent indexers with their rules; blank inherits, 0 exempts', async () => {
    answers['GET /api/prowlarr/indexers'] = {
      success: true,
      indexers: [
        { id: 3, name: 'NZBgeek', protocol: 'usenet' },
        { id: 7, name: 'TorrentLeech', protocol: 'torrent', privacy: 'private' },
      ],
    };
    render(<VideoSeedingGoals />);
    await act(() => videoDownloads.load());
    fireEvent.click(document.getElementById('video-seed-idx-refresh')!);
    await waitFor(() => expect(rows().querySelectorAll('[data-indexer]')).toHaveLength(1));
    expect(rows().querySelector('[data-indexer]')!.textContent).toContain('TorrentLeech');
    // each box is named for a reader: what it is, and for which tracker
    expect(rows().querySelector('[data-vseed-field="hours"]')!.getAttribute('aria-label')).toBe(
      'Hours for TorrentLeech',
    );
    const field = (f: string) =>
      rows().querySelector<HTMLInputElement>(`[data-vseed-field="${f}"]`)!;
    expect(field('ratio').value).toBe('2');
    expect(field('pack_hours').placeholder).toBe('same');
    fireEvent.input(field('hours'), { target: { value: '' } });
    fireEvent.input(field('ratio'), { target: { value: '0' } });
    field('ratio').dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')!.body!.seed_overrides).toEqual({
      '7': { ratio: 0 },
    });
  });
});

describe('the blocks load the config they edit', () => {
  it('on their own, so the Sources tab works before the Library tab was opened', async () => {
    answers['GET /api/video/downloads/config'] = {
      seed_ratio_goal: 2,
      usenet_remove_completed: 'files',
    };
    render(
      <>
        <VideoSeedingGoals />
        <VideoUsenetBlocks />
      </>,
    );
    await waitFor(() =>
      expect(
        (document.getElementById('video-usenet-remove-completed') as HTMLSelectElement).value,
      ).toBe('files'),
    );
    expect((document.getElementById('video-seed-ratio') as HTMLInputElement).value).toBe('2');
    // once, for both
    expect(calls.filter((c) => c.url === '/api/video/downloads/config')).toHaveLength(1);
    fireEvent.change(document.getElementById('video-usenet-remove-completed')!, {
      target: { value: 'off' },
    });
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.method === 'POST')!.body!.usenet_remove_completed).toBe('off');
  });
});
