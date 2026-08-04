/**
 * SystemStatsCard — artefact differential against the live vanilla region +
 * the payload/poll behaviours.
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { compareTrees, extractDashArticle, parseVanilla } from './dash-artefact';
import { SystemStatsCard } from './system-stats';

const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window._socketConnected;
});

async function mountCard() {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<SystemStatsCard />);
  });
  return view!;
}

function fireStats(payload: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:dashboard-stats', { detail: payload }));
  });
}

describe('the artefact differential', () => {
  it('renders the vanilla stats card 1:1 in its initial state', async () => {
    const vanilla = parseVanilla(
      extractDashArticle('<article class="dash-card" data-card="stats">'),
    );
    const view = await mountCard();
    compareTrees(vanilla, view.container.firstElementChild!, 'stats');
  });
});

describe('payloads', () => {
  it('drives all six tiles with the update-time subtitles', async () => {
    const view = await mountCard();
    fireStats({
      active_downloads: 3,
      finished_downloads: 12,
      download_speed: '1.2 MB/s',
      active_syncs: 1,
      uptime: '2h 5m',
      memory_usage: '41%',
      process_memory: '512 MB',
    });
    const card = (id: string) => view.container.querySelector(`#${id}`)!;
    expect(card('active-downloads-card').querySelector('.stat-card-value')!.textContent).toBe('3');
    // The markup said "Completed this session"; updates say "Completed downloads".
    expect(card('finished-downloads-card').querySelector('.stat-card-subtitle')!.textContent).toBe(
      'Completed downloads',
    );
    expect(card('download-speed-card').querySelector('.stat-card-value')!.textContent).toBe(
      '1.2 MB/s',
    );
    expect(card('memory-card').querySelector('.stat-card-value')!.textContent).toBe('41%');
    expect(card('memory-card').querySelector('.stat-card-subtitle')!.textContent).toBe(
      'SoulSync · 512 MB',
    );
  });

  it('memory subtitle falls back without process_memory; absent fields keep previous text', async () => {
    const view = await mountCard();
    fireStats({ memory_usage: '41%', uptime: '2h' });
    fireStats({ memory_usage: '42%' });
    const memory = view.container.querySelector('#memory-card')!;
    expect(memory.querySelector('.stat-card-value')!.textContent).toBe('42%');
    expect(memory.querySelector('.stat-card-subtitle')!.textContent).toBe('Current usage');
    // uptime was absent from the second payload — previous value kept.
    expect(view.container.querySelector('#uptime-card .stat-card-value')!.textContent).toBe('2h');
  });
});

describe('the fallback poll', () => {
  it('hydrates on mount and polls /api/system/stats at 10s while the socket is down', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(
      () =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ active_downloads: 7 }),
        }) as never,
    );
    const view = await mountCard();
    expect(
      view.container.querySelector('#active-downloads-card .stat-card-value')!.textContent,
    ).toBe('7');
    fetchMock.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual(['/api/system/stats']);
  });

  it('skips ticks while the socket is pushing (window._socketConnected)', async () => {
    vi.useFakeTimers();
    const view = await mountCard();
    window._socketConnected = true;
    fetchMock.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    view.unmount();
  });
});
