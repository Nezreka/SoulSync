import { createMemoryHistory } from '@tanstack/react-router';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function renderRoute(path = '/active-downloads') {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createAppRouter({ history, queryClient });
  render(<AppRouterProvider router={router} queryClient={queryClient} />);
  return router;
}

function stub() {
  server.use(
    // music view
    http.get('/api/downloads/all', () =>
      HttpResponse.json({ success: true, downloads: [], batches: [] }),
    ),
    http.get('/api/downloads/batch-history', () =>
      HttpResponse.json({ success: true, history: [] }),
    ),
    http.get('/api/verification/config', () =>
      HttpResponse.json({ success: true, acoustid_enabled: true, require_verified: false }),
    ),
    http.get('/api/review-queue/summary', () =>
      HttpResponse.json({ success: true, quarantine: 0, unverified: 0, total: 0 }),
    ),
    // video view
    http.get('/api/video/downloads/active', () => HttpResponse.json({ downloads: [] })),
    http.get('/api/video/downloads/recycle', () => HttpResponse.json({ items: [] })),
    http.get('/api/video/downloads/history', () =>
      HttpResponse.json({
        success: true,
        items: [],
        counts: { total: 0, movie: 0, show: 0, youtube: 0 },
        pagination: { total_pages: 1 },
      }),
    ),
  );
}

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  stub();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const switcher = () => screen.getByRole('tablist', { name: 'Downloads media' });
const musicTab = () => screen.getByRole('tab', { name: '🎵 Music' });
const videoTab = () => screen.getByRole('tab', { name: '🎬 Video' });

describe('the unified downloads page', () => {
  it('shows the Music | Video switcher with Music active by default', async () => {
    renderRoute('/active-downloads');
    // the router's beforeLoad is async — wait for the first paint
    await screen.findByRole('tab', { name: '🎵 Music' });
    expect(switcher()).toBeInTheDocument();
    expect(musicTab()).toHaveAttribute('aria-selected', 'true');
    expect(videoTab()).toHaveAttribute('aria-selected', 'false');
    // the music page underneath still renders its own tabs
    await screen.findByRole('tab', { name: /Downloads/ });
  });

  it('lands on the video view for ?media=video', async () => {
    renderRoute('/active-downloads?media=video');
    await screen.findByRole('tab', { name: '🎬 Video' });
    expect(videoTab()).toHaveAttribute('aria-selected', 'true');
    // the video view's header: history button + its own filter pills
    await screen.findByRole('button', { name: /History/ });
    expect(screen.getByRole('button', { name: 'Completed' })).toBeInTheDocument();
  });

  it('falls back to music for a bogus ?media=', async () => {
    renderRoute('/active-downloads?media=bogus');
    await screen.findByRole('tab', { name: '🎵 Music' });
    expect(musicTab()).toHaveAttribute('aria-selected', 'true');
    await screen.findByRole('tab', { name: /Downloads/ });
  });

  it('switching to Video navigates to ?media=video and shows the video view', async () => {
    const router = renderRoute('/active-downloads');
    await screen.findByRole('tab', { name: /Downloads/ });
    await act(async () => {
      videoTab().click();
    });
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ media: 'video' });
    });
    expect(videoTab()).toHaveAttribute('aria-selected', 'true');
    await screen.findByRole('button', { name: /History/ });
  });

  it('switching back to Music drops the param and shows the music view', async () => {
    renderRoute('/active-downloads?media=video');
    await screen.findByRole('button', { name: /History/ });
    await act(async () => {
      musicTab().click();
    });
    expect(musicTab()).toHaveAttribute('aria-selected', 'true');
    await screen.findByRole('tab', { name: /Downloads/ });
  });
});
