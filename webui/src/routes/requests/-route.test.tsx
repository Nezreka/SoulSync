import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const listPayload = {
  success: true,
  asks_first: false,
  counts: { pending: 1, approved: 1 },
  pending: [
    {
      key: 'album:blue',
      profile_id: 3,
      requester_name: 'Kim',
      kind: 'album',
      title: 'Blue',
      artist: 'Joni Mitchell',
      album: 'Blue',
      image_url: null,
      track_ids: ['1', '2'],
      tracks: [
        { id: '1', title: 'All I Want' },
        { id: '2', title: 'My Old Man' },
      ],
      track_count: 2,
      created_at: '2026-09-24 10:00:00',
      status: 'pending',
    },
  ],
  history: [
    {
      id: 9,
      profile_id: 3,
      requester_name: 'Kim',
      group_key: 'album:court',
      kind: 'album',
      title: 'Court and Spark',
      artist: 'Joni Mitchell',
      tracks: [],
      track_count: 11,
      status: 'approved',
      resolved_at: '2026-09-24 11:00:00',
    },
  ],
};

function renderRequests(isAdmin: boolean, entry = '/requests') {
  window.SoulSyncWebShellBridge = createShellBridge({
    getCurrentProfileContext: vi.fn(() => ({ profileId: isAdmin ? 1 : 3, isAdmin })),
  });
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

describe('requests route', () => {
  let calls: Array<{ url: string; method: string; body?: string }>;

  beforeEach(() => {
    calls = [];
    window.refreshMusicRequestsBadge = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : null;
        const url = req ? req.url : String(input);
        const method = (req?.method || init?.method || 'GET').toUpperCase();
        const body = req && method !== 'GET' ? await req.clone().text() : undefined;
        calls.push({ url, method, body });
        if (url.includes('/api/requests/music/approve')) return json({ success: true, approved: 2 });
        if (url.includes('/api/requests/music/seen')) return json({ success: true, marked: 0 });
        if (url.includes('/api/requests/music')) return json(listPayload);
        if (url.includes('/api/issues/counts')) {
          return json({ success: true, counts: { open: 0, in_progress: 0, resolved: 0, dismissed: 0, total: 0 } });
        }
        return json({ success: true });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
    window.refreshMusicRequestsBadge = undefined;
  });

  it('shows admins the waiting asks with one approve button', async () => {
    renderRequests(true);

    expect(await screen.findByText('Blue')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Waiting · 1' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/Kim asked/)).toBeInTheDocument();
    // history rows are on other tabs
    expect(screen.queryByText('Court and Spark')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => {
      const hit = calls.find((c) => c.url.includes('/approve'));
      expect(hit?.method).toBe('POST');
      expect(JSON.parse(hit?.body || '{}')).toEqual({ profile_id: 3, key: 'album:blue' });
    });
    await waitFor(() => expect(window.refreshMusicRequestsBadge).toHaveBeenCalled());
  });

  it('shows the on the way tab from the url', async () => {
    renderRequests(true, '/requests?tab=on-the-way');

    expect(await screen.findByText('Court and Spark')).toBeInTheDocument();
    expect(screen.getByText('On the way', { selector: 'span' })).toBeInTheDocument();
  });

  it('gives members no approve button and marks their news seen', async () => {
    renderRequests(false);

    expect(await screen.findByText('Blue')).toBeInTheDocument();
    expect(screen.getByText(/You asked/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/api/requests/music/seen') && c.method === 'POST')).toBe(true),
    );
  });
});
