/**
 * The drawer: opened through window.SoulSyncActivityDrawer (the shell's
 * button), the tabs, the two widths, Escape, and the live feed (the socket
 * push when connected, a poll when not).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import activity from './__fixtures__/activity.json';
import history from './__fixtures__/history.json';
import stats from './__fixtures__/stats.json';
import { clearCache } from './cache';
import { ServerActivityHost, installDrawerApi } from './host';

// every test starts with nothing read ahead or remembered
beforeEach(() => clearCache());

function serve() {
  const hits: string[] = [];
  server.use(
    http.get('*/api/server-activity', () => {
      hits.push('activity');
      return HttpResponse.json(activity);
    }),
    http.get('*/api/server-activity/history', () => HttpResponse.json(history)),
    http.get('*/api/server-activity/stats', () => HttpResponse.json(stats)),
    http.get('*/api/server-activity/settings', () =>
      HttpResponse.json({
        ok: true,
        settings: { upload_mbps: 40, geo_lookup: true, retention_days: 0, stop_presets: [] },
      }),
    ),
  );
  return hits;
}

function mount() {
  installDrawerApi();
  render(<ServerActivityHost />);
  return window.SoulSyncActivityDrawer!;
}

afterEach(() => {
  delete window.SoulSyncActivitySocket;
  vi.useRealTimers();
});

describe('the drawer', () => {
  it('opens at the asked tab and widens for history and stats', async () => {
    serve();
    const api = mount();
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => api.open('history'));
    const dialog = await screen.findByRole('dialog', { name: 'Server Activity' });
    expect(dialog).toHaveAttribute('data-wide');
    expect(screen.getByRole('tab', { name: 'History' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Narrow' }));
    expect(dialog).not.toHaveAttribute('data-wide');
    fireEvent.click(screen.getByRole('button', { name: 'Widen' }));
    expect(dialog).toHaveAttribute('data-wide');
    fireEvent.click(screen.getByRole('tab', { name: /Now playing/ }));
    expect(dialog).not.toHaveAttribute('data-wide');
    expect(await screen.findByRole('tab', { name: /Now playing\s*7/ })).toBeInTheDocument();
  });

  it('the tabs move with the arrow keys', async () => {
    serve();
    const api = mount();
    act(() => api.open());
    const now = await screen.findByRole('tab', { name: /Now playing/ });
    fireEvent.keyDown(now, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'History' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'History' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('tab', { name: 'History' }), { key: 'End' });
    expect(screen.getByRole('tab', { name: 'Stats' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByRole('radiogroup', { name: 'Range' })).toBeInTheDocument();
  });

  it('Escape and the close button close it, and the api says so', async () => {
    serve();
    const api = mount();
    act(() => api.open());
    const dialog = await screen.findByRole('dialog');
    expect(api.isOpen()).toBe(true);
    expect(api.wantsLive()).toBe(true);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(api.isOpen()).toBe(false));
    act(() => api.open());
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }));
    await waitFor(() => expect(api.isOpen()).toBe(false));
  });

  it('takes the socket push when connected, and polls when not', async () => {
    const hits = serve();
    const socket = { isConnected: () => true, subscribe: vi.fn(), unsubscribe: vi.fn() };
    window.SoulSyncActivitySocket = socket;
    const api = mount();
    act(() => api.open());
    await screen.findByRole('tab', { name: /Now playing\s*7/ });
    expect(socket.subscribe).toHaveBeenCalledTimes(1);
    const pushed = { ...structuredClone(activity), sessions: activity.sessions.slice(0, 2) };
    pushed.summary.streams = 2;
    act(() => api.push(pushed));
    expect(await screen.findByRole('tab', { name: /Now playing\s*2/ })).toBeInTheDocument();
    act(() => api.close());
    await waitFor(() => expect(socket.unsubscribe).toHaveBeenCalledTimes(1));
    expect(hits.length).toBe(1);
  });

  it('falls back to a poll without a socket', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const hits = serve();
    const api = mount();
    act(() => api.open());
    await screen.findByRole('tab', { name: /Now playing\s*7/ });
    const before = hits.length;
    await act(async () => {
      vi.advanceTimersByTime(3100);
    });
    await waitFor(() => expect(hits.length).toBeGreaterThan(before));
  });
});
