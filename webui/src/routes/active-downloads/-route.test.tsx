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
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

const row = (over: Record<string, unknown> = {}) => ({
  task_id: 't1',
  title: 'Xtal',
  artist: 'Aphex Twin',
  album: 'SAW',
  artwork: '',
  status: 'downloading',
  progress: 40,
  error: null,
  verification_status: null,
  batch_id: '',
  batch_name: '',
  batch_source: '',
  playlist_id: 'p1',
  track_index: 0,
  batch_total: 1,
  timestamp: 0,
  priority: 0,
  quality: '',
  is_persistent_history: false,
  ...over,
});

function stub({
  downloads = [] as unknown[],
  batches = [] as unknown[],
  history = [] as unknown[],
  quarantine = [] as unknown[],
  acoustid = { success: true, acoustid_enabled: true, require_verified: false },
} = {}) {
  server.use(
    http.get('/api/downloads/all', () => HttpResponse.json({ success: true, downloads, batches })),
    http.get('/api/downloads/batch-history', () => HttpResponse.json({ success: true, history })),
    http.get('/api/verification/config', () => HttpResponse.json(acoustid)),
    http.get('/api/quarantine/list', () =>
      HttpResponse.json({ success: true, entries: quarantine }),
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

/** The page is on screen once its title has painted. */
const settled = () => screen.findByText('Downloads');

describe('the active-downloads route', () => {
  it('renders without the shell’s .page class', async () => {
    // `.page { display: none }` and the shell only adds `.active` for legacy
    // pages, so a React page wearing it renders invisibly with every other
    // test still green. This is the label-detail trap.
    const { container } = renderRoute();
    await settled();
    const host = document.getElementById('webui-react-root') ?? container;
    expect(host.querySelector('.page')).toBeNull();
  });

  it('renders the layout the stylesheet expects', async () => {
    const { container } = renderRoute();
    await settled();
    expect(container.querySelector('.adl-layout')).not.toBeNull();
    expect(container.querySelector('.adl-main')).not.toBeNull();
    expect(container.querySelector('.adl-container')).not.toBeNull();
    // the 366px side panel is dead — batches are groups in the main list now
    expect(container.querySelector('#adl-batch-panel')).toBeNull();
    expect(container.querySelector('#adl-view-tabs')).not.toBeNull();
  });

  it('keeps the ids the helper tour points at', async () => {
    // helper.js targets .adl-container, #adl-filter-pills, #adl-list and
    // #adl-clear-btn. querySelector returns null for a missing one and the
    // tour step silently highlights nothing.
    const { container } = renderRoute();
    await settled();
    expect(container.querySelector('.adl-container')).not.toBeNull();
    expect(container.querySelector('#adl-filter-pills')).not.toBeNull();
    expect(container.querySelector('#adl-list')).not.toBeNull();
  });

  it('lists what the server sent', async () => {
    stub({ downloads: [row(), row({ task_id: 't2', title: 'Tha', status: 'completed' })] });
    const { container } = renderRoute();
    await settled();

    await waitFor(() => expect(container.querySelectorAll('.adl-row')).toHaveLength(2));
    const nums = [...container.querySelectorAll('.adl-stat-num')].map((n) => n.textContent);
    // active / queued / failed / total
    expect(nums).toEqual(['1', '0', '0', '2']);
  });

  it('shows the empty state with nothing to list', async () => {
    const { container } = renderRoute();
    await settled();
    await waitFor(() => expect(container.querySelector('#adl-empty')).not.toBeNull());
    expect(container.querySelector('#adl-empty')?.textContent).toContain('Nothing downloading');
  });

  it('filters the list when a pill is clicked', async () => {
    stub({
      downloads: [
        row({ task_id: 'a', status: 'downloading' }),
        row({ task_id: 'b', status: 'completed' }),
      ],
    });
    const { container } = renderRoute();
    await settled();
    await waitFor(() => expect(container.querySelectorAll('.adl-row')).toHaveLength(2));

    const completed = container.querySelector('[data-filter="completed"]') as HTMLElement;
    act(() => completed.click());

    await waitFor(() => expect(container.querySelectorAll('.adl-row')).toHaveLength(1));
    expect(container.querySelector('.adl-row-title')?.textContent).toBe('Xtal');
  });

  it('renders a batch as a list group with the panel card promoted to its header', async () => {
    stub({
      batches: [
        {
          batch_id: 'b1',
          playlist_id: 'p1',
          batch_name: 'My Batch',
          source_page: 'wishlist',
          phase: 'downloading',
          total: 10,
          completed: 3,
          failed: 0,
          active: 1,
          queued: 6,
        },
      ],
      downloads: [row({ task_id: 'bt1', batch_id: 'b1', batch_name: 'My Batch' })],
    });
    const withBatch = renderRoute();
    await settled();
    await waitFor(() => expect(withBatch.container.querySelector('.adl-group')).not.toBeNull());
    expect(withBatch.container.querySelector('.adl-group-name')?.textContent).toBe('My Batch');
    expect(withBatch.container.querySelector('.adl-group-phase')?.textContent).toContain(
      '3/10 tracks',
    );
    expect(withBatch.container.querySelector('.adl-group-stats')?.textContent).toBe(
      '3 done · 1 active · 6 queued',
    );
    // live batch groups start open, with real rows inside
    expect(withBatch.container.querySelectorAll('.adl-group-rows .adl-row')).toHaveLength(1);
  });

  it('shows the review banner and quarantine rows under the ⚠ filter', async () => {
    stub({
      downloads: [row({ task_id: 'u1', status: 'completed', verification_status: 'unverified' })],
      quarantine: [
        {
          id: 'q1',
          filename: 'bad.quarantined',
          original_filename: 'bad.flac',
          reason: 'duration mismatch',
          expected_track: 'Broken',
          expected_artist: 'Someone',
          group_key: 'g1',
          timestamp: '2026-07-30T10:00:00Z',
          size_bytes: 1,
          has_full_context: true,
          trigger: 'integrity',
          source_username: 'peer',
          source_filename: 'peer/bad.flac',
          thumb_url: '',
          quality: 'FLAC',
        },
      ],
    });
    const { container } = renderRoute();
    await settled();

    const review = container.querySelector('[data-filter="unverified"]') as HTMLElement;
    act(() => review.click());

    await waitFor(() => expect(container.querySelector('#verif-subview-banner')).not.toBeNull());
    // Unverified sub-view first.
    expect(container.querySelector('.verif-rb-unv')?.textContent).toBe('ACOUSTID UNCONFIRMED');

    const quarPill = [...container.querySelectorAll('#verif-subview-banner .adl-pill')].find((p) =>
      p.textContent?.includes('Quarantine'),
    ) as HTMLElement;
    act(() => quarPill.click());

    await waitFor(() =>
      expect(container.querySelector('[data-quarantine-id="q1"]')).not.toBeNull(),
    );
    expect(container.querySelector('.verif-rb-int')?.textContent).toBe('DURATION / INTEGRITY');
  });

  it('collapses to quarantine-only when require_verified is on', async () => {
    // Not just when AcoustID is off — with require_verified the unverified
    // queue can never have anything in it.
    stub({ acoustid: { success: true, acoustid_enabled: true, require_verified: true } });
    const { container } = renderRoute();
    await settled();

    await waitFor(() =>
      expect(container.querySelector('[data-filter="unverified"]')?.textContent).toBe('Quarantine'),
    );

    act(() => (container.querySelector('[data-filter="unverified"]') as HTMLElement).click());
    await waitFor(() => expect(container.querySelector('#verif-subview-banner')).not.toBeNull());
    const pills = [...container.querySelectorAll('#verif-subview-banner .adl-pill')];
    // unverified is gone; quarantine and the deleted bin remain
    expect(pills).toHaveLength(2);
    expect(pills[0].textContent).toContain('Quarantine');
    expect(pills[1].textContent).toContain('Deleted');
  });

  it('the review badge counts only what the views can show', async () => {
    // "hundreds in the quarantine badge, list empty" (aug 25): with no
    // unverified view possible, unverified rows must not inflate the pill
    stub({ acoustid: { success: true, acoustid_enabled: false, require_verified: false } });
    server.use(
      http.get('/api/review-queue/summary', () =>
        HttpResponse.json({ success: true, quarantine: 2, unverified: 300, total: 302 }),
      ),
    );
    const { container } = renderRoute();
    await settled();
    await waitFor(() =>
      expect(
        container.querySelector('[data-filter="unverified"] .adl-pill-badge')?.textContent,
      ).toBe('2'),
    );
  });

  it('hides Cancel All and Clear Completed when there is nothing to act on', async () => {
    const { container } = renderRoute();
    await settled();
    await waitFor(() => expect(container.querySelector('#adl-empty')).not.toBeNull());
    expect(container.querySelector('#adl-cancel-all-btn')).toBeNull();
    expect(container.querySelector('#adl-clear-btn')).toBeNull();
  });

  it('shows both action buttons once there is work and history', async () => {
    stub({
      downloads: [row({ status: 'queued' }), row({ task_id: 't2', status: 'completed' })],
    });
    const { container } = renderRoute();
    await settled();
    await waitFor(() => expect(container.querySelector('#adl-cancel-all-btn')).not.toBeNull());
    expect(container.querySelector('#adl-clear-btn')).not.toBeNull();
  });

  it('survives every endpoint failing', async () => {
    // A downloads page that white-screens because one poll 500'd is worse
    // than one showing an empty list.
    server.use(
      http.get('/api/downloads/all', () => HttpResponse.error()),
      http.get('/api/downloads/batch-history', () => HttpResponse.error()),
      http.get('/api/verification/config', () => HttpResponse.error()),
      http.get('/api/quarantine/list', () => HttpResponse.error()),
    );
    const { container } = renderRoute();
    await settled();
    await waitFor(() => expect(container.querySelector('#adl-empty')).not.toBeNull());
    expect(container.querySelector('.adl-layout')).not.toBeNull();
  });
});
