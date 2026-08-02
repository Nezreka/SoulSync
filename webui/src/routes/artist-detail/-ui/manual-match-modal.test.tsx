import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ManualMatchModal } from './manual-match-modal';

/**
 * The manual match modal. Pins the auto-search on open, the provider override
 * on apply (a hydrabase-proxied result matches through its REAL provider), and
 * that Clear Match runs through the shared confirm dialog and hands its
 * updated_data to onUpdated — the two vanilla bugs this port fixes.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.showConfirmDialog;
});

function mount(overrides: Partial<Parameters<typeof ManualMatchModal>[0]> = {}) {
  const onUpdated = vi.fn();
  const onClose = vi.fn();
  render(
    <ManualMatchModal
      entityType="album"
      entityId={7}
      service="spotify"
      defaultQuery="SAW 85-92"
      artistId={42}
      onUpdated={onUpdated}
      onClose={onClose}
      {...overrides}
    />,
  );
  return { onUpdated, onClose };
}

describe('ManualMatchModal', () => {
  it('auto-searches the default query on open and applies through the RESULT provider', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : {} });
        if (url.endsWith('search-service')) {
          return new Response(
            JSON.stringify({
              success: true,
              results: [{ id: 'dz9', name: 'Found Album', provider: 'deezer' }],
            }),
          );
        }
        return new Response(JSON.stringify({ success: true, updated_data: { success: true } }));
      }),
    );
    window.showToast = vi.fn() as never;
    const { onUpdated, onClose } = mount();

    await screen.findByText('Found Album');
    expect(calls[0].body).toEqual({ service: 'spotify', entity_type: 'album', query: 'SAW 85-92' });

    screen.getByText('Match').click();
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    const apply = calls.find((c) => c.url.endsWith('manual-match'));
    // Proxied result: the request carries deezer, not the tab's spotify.
    expect(apply?.body).toMatchObject({ service: 'deezer', service_id: 'dz9' });
    expect(onClose).toHaveBeenCalled();
    expect(onUpdated.mock.calls[0]?.[0]?.updatedData).toMatchObject({ success: true });
  });

  it('Clear Match confirms via the shared dialog and feeds updated_data to onUpdated', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('search-service')) {
          return new Response(JSON.stringify({ success: true, results: [] }));
        }
        return new Response(
          JSON.stringify({ success: true, updated_data: { success: true, cleared: true } }),
        );
      }),
    );
    const confirm = vi.fn(async (_opts: { message: string }) => true);
    window.showConfirmDialog = confirm as never;
    window.showToast = vi.fn() as never;
    const { onUpdated } = mount({ service: 'musicbrainz' });

    await screen.findByText(/No results found/);
    screen.getByText('Clear Match').click();
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    expect(String(confirm.mock.calls[0]?.[0]?.message)).toContain('Clear MusicBrainz match');
    expect(onUpdated.mock.calls[0]?.[0]?.updatedData).toMatchObject({ cleared: true });
    expect(window.showToast).toHaveBeenCalledWith('Cleared MusicBrainz match', 'success');
  });

  it('a declined confirm clears nothing', async () => {
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, results: [] })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.showConfirmDialog = vi.fn(async () => false) as never;
    const { onUpdated } = mount();

    await screen.findByText(/No results found/);
    screen.getByText('Clear Match').click();
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(fetchSpy.mock.calls.every(([u]) => !String(u).includes('clear-match'))).toBe(true);
    expect(onUpdated).not.toHaveBeenCalled();
  });
});
