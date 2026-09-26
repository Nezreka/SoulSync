import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { peekArtistEdit } from '../artist-detail/-artist-detail.edit-focus';
import { takeFindingsFocus } from '../tools/-tools.findings-focus';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderIssuesRoute(initialEntries = ['/issues']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });
  return { history, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

const COUNTS = { open: 2, in_progress: 0, resolved: 0, dismissed: 0, total: 2, updates: 0 };

function issue(id: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    profile_id: 5,
    entity_type: 'album',
    entity_id: String(100 + id),
    category: 'wrong_metadata',
    title: `Report ${id}`,
    description: `Details ${id}`,
    status: 'open',
    priority: 'normal',
    snapshot_data: { title: `Album ${id}`, artist_name: 'Artist', artist_id: 55 },
    created_at: '2026-09-01 10:00:00',
    reporter_name: 'Kim',
    ...extra,
  };
}

interface Seen {
  method: string;
  url: string;
  body: unknown;
}

function stubFetch(detail: Record<string, unknown> = {}) {
  const seen: Seen[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : null;
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      const method = request ? request.method : 'GET';
      let body: unknown = null;
      if (request && method !== 'GET') {
        try {
          body = await request.clone().json();
        } catch {
          body = null;
        }
      }
      seen.push({ method, url, body });
      if (url.includes('/api/issues/counts')) return json({ success: true, counts: COUNTS });
      if (url.includes('/api/issues/bulk')) return json({ success: true, done: 2, failed: 0 });
      if (url.includes('/api/issues?')) {
        return json({ success: true, total: 2, issues: [issue(1), issue(2)] });
      }
      if (/\/api\/issues\/1(\?|$)/.test(url) && method === 'GET') {
        return json({ success: true, issue: issue(1, detail) });
      }
      return json({ success: true });
    }) as unknown as typeof fetch,
  );
  return seen;
}

describe('issues bulk select (admin)', () => {
  let seen: Seen[];
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    seen = stubFetch();
    vi.stubGlobal('showToast', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
  });

  it('ticks rows and resolves them in one call', async () => {
    renderIssuesRoute();
    const first = await screen.findByRole('checkbox', { name: 'Select Report 1' });
    expect(screen.queryByRole('toolbar', { name: 'Selected issues' })).toBeNull();
    fireEvent.click(first);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Report 2' }));

    const bar = screen.getByRole('toolbar', { name: 'Selected issues' });
    expect(bar).toHaveTextContent('2 selected');
    fireEvent.click(within(bar).getByRole('button', { name: 'Resolve' }));

    await waitFor(() => expect(window.showToast).toHaveBeenCalledWith('Resolved 2', 'success'));
    const post = seen.find((s) => s.method === 'POST' && s.url.includes('/api/issues/bulk'));
    expect(post?.body).toEqual({ ids: [1, 2], status: 'resolved' });
    await waitFor(() => expect(screen.queryByRole('toolbar')).toBeNull());
  });

  it('sets priority from the menu, keyboard reachable', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Report 1' }));
    const bar = screen.getByRole('toolbar', { name: 'Selected issues' });
    fireEvent.click(within(bar).getByRole('button', { name: /priority/i }));
    const high = within(bar).getByRole('menuitem', { name: 'High' });
    await waitFor(() => expect(within(bar).getByRole('menuitem', { name: 'Low' })).toHaveFocus());
    fireEvent.click(high);
    await waitFor(() => {
      const post = seen.find((s) => s.url.includes('/api/issues/bulk'));
      expect(post?.body).toEqual({ ids: [1], priority: 'high' });
    });
  });

  it('asks the app confirm before a bulk delete', async () => {
    const confirm = vi.fn(async () => false);
    vi.stubGlobal('showConfirmDialog', confirm);
    renderIssuesRoute();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Report 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(seen.some((s) => s.url.includes('/api/issues/bulk'))).toBe(false);
  });

  it('select all ticks every row on screen', async () => {
    renderIssuesRoute();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Report 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Select all 2' }));
    expect(screen.getByRole('toolbar')).toHaveTextContent('2 selected');
    expect(screen.getByRole('checkbox', { name: 'Select Report 2' })).toBeChecked();
  });
});

describe('issue fix hand-offs (admin)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    vi.stubGlobal('showToast', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
  });

  it('edit details asks the artist page to open that album', async () => {
    stubFetch({ fix_action: { id: 'edit_metadata', label: 'Edit details' } });
    renderIssuesRoute(['/issues?issueId=1']);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit details' }));
    expect(peekArtistEdit('55')).toMatchObject({ artistId: '55', albumId: '101' });
  });

  it('find duplicates lands on the duplicate detector, searched', async () => {
    stubFetch({
      category: 'duplicate_tracks',
      fix_action: { id: 'find_duplicates', label: 'Find duplicates' },
    });
    renderIssuesRoute(['/issues?issueId=1']);
    fireEvent.click(await screen.findByRole('button', { name: 'Find duplicates' }));
    expect(takeFindingsFocus()).toEqual({ jobId: 'duplicate_detector', query: 'Album 1' });
  });
});

describe('owner edit (member)', () => {
  let seen: Seen[];
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge({
      getCurrentProfileContext: vi.fn(() => ({ profileId: 5, isAdmin: false })),
    });
    seen = stubFetch();
    vi.stubGlobal('showToast', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.SoulSyncWebShellBridge = undefined;
  });

  it('members get no bulk ticks', async () => {
    renderIssuesRoute();
    await screen.findByRole('link', { name: /Report 1/ });
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('the reporter edits their title and details', async () => {
    renderIssuesRoute(['/issues?issueId=1']);
    fireEvent.click(await screen.findByRole('button', { name: /more actions/i }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));

    const form = screen.getByRole('form', { name: 'Edit your report' });
    const title = within(form).getByLabelText('Title');
    expect(title).toHaveValue('Report 1');
    fireEvent.change(title, { target: { value: '  Wrong year  ' } });
    fireEvent.change(within(form).getByLabelText('Details'), { target: { value: 'says 1999' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const put = seen.find((s) => s.method === 'PUT' && s.url.endsWith('/api/issues/1'));
      expect(put?.body).toEqual({ title: 'Wrong year', description: 'says 1999' });
    });
    await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
  });

  it('cancel puts the title back without saving', async () => {
    renderIssuesRoute(['/issues?issueId=1']);
    fireEvent.click(await screen.findByRole('button', { name: /more actions/i }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('form')).toBeNull();
    expect(screen.getByRole('dialog')).toHaveTextContent('Report 1');
    expect(seen.some((s) => s.method === 'PUT')).toBe(false);
  });
});
