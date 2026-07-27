import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { Automation } from '../-automations.types';

/**
 * Driven through the REAL router, with the manifest reporting /automations as
 * React-owned.
 *
 * The page cannot be rendered standalone — useProfile() reads the root route
 * context — and going through the router also exercises the loader, the
 * dormancy guard and URL-driven filter state rather than a stub of them.
 * Only getShellRouteByPageId is faked; the rest of the manifest is real,
 * because the router builds its route table from it.
 */
vi.mock('@/platform/shell/route-manifest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/platform/shell/route-manifest')>();
  return {
    ...actual,
    getShellRouteByPageId: (pageId: string) =>
      pageId === 'automations'
        ? { ...actual.getShellRouteByPageId('automations'), kind: 'react' }
        : actual.getShellRouteByPageId(pageId as never),
  };
});

function auto(over: Partial<Automation> & { id: number }): Automation {
  return { name: `auto-${over.id}`, enabled: 1, ...over };
}

let requests: { url: string; method: string; body?: unknown }[] = [];

function stubFetch(rows: Automation[], master: { music: boolean } = { music: true }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (
        init?.method ?? (input instanceof Request ? input.method : 'GET')
      ).toUpperCase();
      // ky sends a Request object, so a JSON payload is NOT on init.body —
      // reading only init.body silently recorded `undefined` for every call
      // that carries one.
      let raw: string | undefined;
      if (init?.body) raw = String(init.body);
      else if (input instanceof Request) raw = (await input.clone().text()) || undefined;
      let body: unknown;
      if (raw) {
        try {
          body = JSON.parse(raw);
        } catch {
          body = raw;
        }
      }
      requests.push({ url, method, body });

      const json = (data: unknown) =>
        new Response(JSON.stringify(data), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });

      if (url.includes('/api/automations/master')) return json(master);
      if (url.match(/\/api\/automations\/\d+/) || url.includes('bulk-toggle'))
        return json({ success: true, updated: 2 });
      if (url.includes('/api/automations')) return json(rows);
      return json({});
    }),
  );
}

function renderPage(rows: Automation[], master?: { music: boolean }, entry = '/automations') {
  requests = [];
  stubFetch(rows, master);
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

const sent = (fragment: string, method = 'POST') =>
  requests.filter((r) => r.url.includes(fragment) && r.method === method);

describe('AutomationsPage interactions', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    window.showConfirmDialog = vi.fn(async () => true);
    window.showAutomationBuilder = vi.fn();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('toggles an automation without a toast', async () => {
    renderPage([auto({ id: 5, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(screen.getByLabelText('Disable Nightly'));
    await waitFor(() => expect(sent('/automations/5/toggle')).toHaveLength(1));
    // The switch is its own feedback; vanilla showed nothing on success.
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('runs an automation and reports it', async () => {
    renderPage([auto({ id: 6, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.automation-run-btn')!);
    await waitFor(() => expect(sent('/automations/6/run')).toHaveLength(1));
    expect(window.showToast).toHaveBeenCalledWith('Automation triggered', 'success');
  });

  it('confirms before deleting, and does nothing if declined', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    renderPage([auto({ id: 7, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.automation-delete-btn')!);
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    // The important half: a declined confirm must not issue the DELETE.
    expect(sent('/automations/7', 'DELETE')).toHaveLength(0);
  });

  it('deletes once confirmed', async () => {
    renderPage([auto({ id: 8, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.automation-delete-btn')!);
    await waitFor(() => expect(sent('/automations/8', 'DELETE')).toHaveLength(1));
    expect(window.showToast).toHaveBeenCalledWith('Automation deleted', 'success');
  });

  it('flips the master switch to the opposite of its current state', async () => {
    renderPage([auto({ id: 1 })], { music: true });
    await waitFor(() => expect(document.querySelector('.auto-master-toggle.on')).not.toBeNull());

    fireEvent.click(document.querySelector('.auto-master-toggle')!);
    await waitFor(() => expect(sent('/automations/master')).toHaveLength(1));
    expect(sent('/automations/master')[0].body).toEqual({ side: 'music', enabled: false });
  });

  it('bulk-toggles every automation in the group, including ones the filter hides', async () => {
    // React removes filtered-out cards from the DOM entirely, so sourcing ids
    // from the rendered cards would toggle only the visible subset.
    renderPage(
      [
        auto({ id: 11, name: 'Alpha', group_name: 'Chores' }),
        auto({ id: 12, name: 'Beta', group_name: 'Chores' }),
      ],
      undefined,
      '/automations?q=Alpha',
    );
    await screen.findByText('Alpha');
    expect(screen.queryByText('Beta')).toBeNull(); // genuinely not rendered

    fireEvent.click(document.querySelector('.section-action-btn')!);
    await waitFor(() => expect(sent('bulk-toggle')).toHaveLength(1));
    expect(sent('bulk-toggle')[0].body).toEqual({ automation_ids: [11, 12], enabled: false });
  });

  it('surfaces a server error as a toast', async () => {
    renderPage([auto({ id: 9, name: 'Nightly' })]);
    await screen.findByText('Nightly');
    // The endpoints answer {error} with HTTP 200, so the payload check is what
    // catches this — an ok-status check alone would report success.
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ error: 'engine unavailable' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    fireEvent.click(document.querySelector('.automation-run-btn')!);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Error: engine unavailable', 'error'),
    );
  });
});
