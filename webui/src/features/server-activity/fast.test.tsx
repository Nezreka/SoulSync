/**
 * The drawer loads fast and every poster finds a picture: posters ask for the
 * size drawn with the row's fallback query and try once more before the glyph
 * stays; History and Stats paint from what was read before and ask again
 * behind it; the drawer reads both ahead when it opens; a shared live
 * snapshot starts the playheads from when it was taken.
 */

import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { HistoryPayload, StatsPayload } from '@/shell/server-activity-types';

import { HttpResponse, http, server } from '@/test/msw';

import activity from './__fixtures__/activity.json';
import history from './__fixtures__/history.json';
import stats from './__fixtures__/stats.json';
import { imageSrc } from './api';
import { clearCache, load, peek } from './cache';
import { History } from './history';
import { ServerActivityHost, installDrawerApi } from './host';
import { useLiveActivity } from './live';
import { Stats } from './stats';
import { Poster } from './ui';

beforeEach(() => clearCache());

const HISTORY = history as unknown as HistoryPayload;
const STATS = stats as unknown as StatsPayload;

describe('posters', () => {
  it('ask for the width drawn, with the fallback query the server built', () => {
    expect(imageSrc('path=%2Flibrary%2Fmetadata%2F9%2Fthumb&k=episode&gk=9', null, 96)).toMatch(
      /\/api\/server-activity\/image\?path=%2Flibrary%2Fmetadata%2F9%2Fthumb&k=episode&gk=9&w=96$/,
    );
    // a bare thumb still works; nothing gives nothing
    expect(imageSrc(null, '/library/metadata/9/thumb/1', 160)).toMatch(
      /image\?path=%2Flibrary%2Fmetadata%2F9%2Fthumb%2F1&w=160$/,
    );
    expect(imageSrc(null, null, 96)).toBe('');
  });

  it('a row poster uses the row query at the row width', () => {
    const { container } = render(
      <Poster thumb="/library/metadata/1/thumb" image="path=x&gk=9" kind="episode" size="row" />,
    );
    expect(container.querySelector('img')!.getAttribute('src')).toMatch(/image\?path=x&gk=9&w=96$/);
  });

  it('a failed picture is asked once more, then the glyph stays', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<Poster thumb="/library/metadata/1/thumb" kind="movie" />);
      const first = container.querySelector('img')!;
      expect(first.getAttribute('loading')).toBe('lazy');
      fireEvent.error(first);
      expect(container.querySelector('img')).toBeNull(); // the glyph while it waits
      act(() => {
        vi.advanceTimersByTime(1600);
      });
      const second = container.querySelector('img')!;
      expect(second.getAttribute('src')).toMatch(/&retry=1$/);
      fireEvent.error(second);
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(container.querySelector('img')).toBeNull();
      expect(container.querySelector('svg')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('a picture that loads on the second try stays', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<Poster thumb="/library/metadata/1/thumb" kind="movie" />);
      fireEvent.error(container.querySelector('img')!);
      act(() => {
        vi.advanceTimersByTime(1600);
      });
      fireEvent.load(container.querySelector('img')!);
      expect(container.querySelector('img')).toHaveAttribute('data-loaded');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('the cache', () => {
  it('shares one request between callers and keeps a good answer', async () => {
    let calls = 0;
    const loader = async () => {
      calls += 1;
      return { ok: true as const, data: { ok: true, n: calls } };
    };
    const [a, b] = await Promise.all([load('k', loader), load('k', loader)]);
    expect(calls).toBe(1);
    expect(a).toBe(b);
    expect(peek<{ n: number }>('k')?.data.n).toBe(1);
  });

  it('does not keep a failure', async () => {
    await load('bad', async () => ({ ok: false as const, error: 'x', status: 500 }));
    await load('bad2', async () => ({ ok: true as const, data: { ok: false } }));
    expect(peek('bad')).toBeUndefined();
    expect(peek('bad2')).toBeUndefined();
  });
});

describe('history and stats paint from what was read before', () => {
  it('History comes back at once, with no skeleton, and a fresh answer is not asked again', async () => {
    let asked = 0;
    server.use(
      http.get('*/api/server-activity/history', () => {
        asked += 1;
        return HttpResponse.json(HISTORY);
      }),
    );
    const first = render(<History onNavigate={() => {}} />);
    await screen.findByText('Everything In Its Right Place');
    expect(asked).toBe(1);
    first.unmount();
    // back inside the fresh window: painted from memory, not asked again
    render(<History onNavigate={() => {}} />);
    expect(screen.getByText('Everything In Its Right Place')).toBeInTheDocument();
    expect(document.querySelector('[data-stale]')).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(asked).toBe(1);
  });

  it('History keeps its filters across a tab switch', async () => {
    const days: string[] = [];
    server.use(
      http.get('*/api/server-activity/history', ({ request }) => {
        days.push(new URL(request.url).searchParams.get('days') ?? '');
        return HttpResponse.json(HISTORY);
      }),
    );
    const first = render(<History onNavigate={() => {}} />);
    await screen.findByText('Everything In Its Right Place');
    fireEvent.click(screen.getByRole('button', { name: /Dates/ }));
    fireEvent.click(await screen.findByRole('option', { name: 'Last 30 days' }));
    await waitFor(() => expect(days).toContain('30'));
    first.unmount();
    render(<History onNavigate={() => {}} />);
    expect(screen.getByRole('button', { name: /Dates/ })).toHaveTextContent('Last 30 days');
  });

  it('a stats range seen before paints at once', async () => {
    const asked: string[] = [];
    server.use(
      http.get('*/api/server-activity/stats', ({ request }) => {
        asked.push(new URL(request.url).searchParams.get('days')!);
        return HttpResponse.json({
          ...STATS,
          days: Number(new URL(request.url).searchParams.get('days')),
        });
      }),
    );
    const view = render(<Stats days={7} onNavigate={() => {}} />);
    await screen.findByText(/In the last 7 days/);
    view.rerender(<Stats days={30} onNavigate={() => {}} />);
    await screen.findByText(/In the last 30 days/);
    view.rerender(<Stats days={7} onNavigate={() => {}} />);
    expect(screen.getByText(/In the last 7 days/)).toBeInTheDocument();
    // and, fresh, it is not asked for again
    await new Promise((r) => setTimeout(r, 30));
    expect(asked).toEqual(['7', '30']);
  });
});

describe('the drawer reads ahead', () => {
  it('opening on Now playing loads History and Stats in the background', async () => {
    const asked: string[] = [];
    server.use(
      http.get('*/api/server-activity', () => HttpResponse.json(activity)),
      http.get('*/api/server-activity/history', () => {
        asked.push('history');
        return HttpResponse.json(HISTORY);
      }),
      http.get('*/api/server-activity/stats', () => {
        asked.push('stats');
        return HttpResponse.json(STATS);
      }),
      http.get('*/api/server-activity/settings', () =>
        HttpResponse.json({ ok: true, settings: { stop_presets: [] } }),
      ),
    );
    installDrawerApi();
    render(<ServerActivityHost />);
    act(() => window.SoulSyncActivityDrawer!.open('now'));
    await waitFor(() => expect(asked.sort()).toEqual(['history', 'stats']));
    // the tab then paints without asking again
    fireEvent.click(screen.getByRole('tab', { name: 'History' }));
    expect(await screen.findByText('Everything In Its Right Place')).toBeInTheDocument();
    expect(asked.filter((x) => x === 'history')).toHaveLength(1);
  });
});

describe('the live snapshot', () => {
  it('starts the playheads from when the snapshot was taken', async () => {
    server.use(
      http.get('*/api/server-activity', () => HttpResponse.json({ ...activity, age_ms: 6000 })),
    );
    const { result } = renderHook(() => useLiveActivity(true, true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    const behind = Date.now() - result.current.receivedAt;
    expect(behind).toBeGreaterThanOrEqual(5900);
    expect(behind).toBeLessThan(9000);
  });
});
