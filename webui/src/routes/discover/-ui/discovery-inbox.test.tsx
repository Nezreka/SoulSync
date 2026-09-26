import { QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { DiscoveryInbox } from './discovery-inbox';

/** The inbox on the Discover page. */

const RELEASE = {
  id: 1,
  kind: 'new_release',
  title: 'Memorial',
  artist_name: 'Soen',
  item_date: '2026-09-20',
  payload: { source: 'deezer', ids: { deezer: 'dz1' } },
};
const CONCERT = {
  id: 2,
  kind: 'concert',
  title: 'Forum · LA',
  artist_name: 'Tool',
  item_date: '2026-10-26',
  payload: { url: 'https://tm/1' },
};
const SAVED = {
  id: 3,
  kind: 'saved_rec',
  title: 'Karnivool',
  artist_name: 'Karnivool',
  payload: { entity_type: 'artist', ids: { deezer: 'dz-k' } },
};

let states: { id: string; state: unknown }[] = [];
let dismissedAll = 0;

function stub(byView: Record<string, unknown[]>, extra: Record<string, unknown> = {}) {
  states = [];
  dismissedAll = 0;
  server.use(
    http.get('*/api/discover/inbox', ({ request }) => {
      const view = new URL(request.url).searchParams.get('view') ?? 'new';
      return HttpResponse.json({
        success: true,
        view,
        items: byView[view] ?? [],
        counts: { unread: (byView.new ?? []).length },
        unanswered: [],
        refreshing: false,
        ...extra,
      });
    }),
    http.post('*/api/discover/inbox/:id/state', async ({ params, request }) => {
      states.push({
        id: String(params.id),
        state: ((await request.json()) as { state: unknown }).state,
      });
      return HttpResponse.json({ success: true });
    }),
    http.post('*/api/discover/inbox/dismiss-all', () => {
      dismissedAll += 1;
      return HttpResponse.json({ success: true, dismissed: 2 });
    }),
  );
}

function mount(onOpenRelease = vi.fn()) {
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <DiscoveryInbox
        onOpenRelease={onOpenRelease}
        buildArtistPath={(item) => (item.kind === 'saved_rec' ? `/artist/${item.title}` : '')}
      />
    </QueryClientProvider>,
  );
  return onOpenRelease;
}

beforeEach(() => {
  window.refreshDiscoverInboxBadge = vi.fn();
  window.showToast = vi.fn() as never;
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  delete window.refreshDiscoverInboxBadge;
});

describe('DiscoveryInbox', () => {
  it('lists what is new, and opens a release with the album flow', async () => {
    stub({ new: [RELEASE, CONCERT] });
    const onOpen = mount();
    await screen.findByText('Memorial');
    expect(screen.getByText('2')).toBeTruthy(); // the unread count
    expect(screen.getByText('Tickets').getAttribute('href')).toBe('https://tm/1');
    fireEvent.click(screen.getByText('Open'));
    expect(onOpen).toHaveBeenCalledWith(
      expect.objectContaining({ album_name: 'Memorial', album_deezer_id: 'dz1', source: 'deezer' }),
    );
  });

  it('saves and dismisses, and tells the badge', async () => {
    stub({ new: [RELEASE] });
    mount();
    fireEvent.click(await screen.findByLabelText('Save Memorial'));
    await waitFor(() => expect(states).toEqual([{ id: '1', state: 'saved' }]));
    fireEvent.click(screen.getByLabelText('Dismiss Memorial'));
    await waitFor(() => expect(states).toHaveLength(2));
    expect(states[1]).toEqual({ id: '1', state: 'dismissed' });
    await waitFor(() => expect(window.refreshDiscoverInboxBadge).toHaveBeenCalled());
    fireEvent.click(screen.getByText('Dismiss all'));
    await waitFor(() => expect(dismissedAll).toBe(1));
  });

  it('shows what you saved, with no save button and a way to the artist', async () => {
    stub({ new: [], saved: [SAVED] });
    mount();
    await screen.findByText(/Nothing new/);
    fireEvent.click(screen.getByRole('tab', { name: 'Saved' }));
    await screen.findByText('Karnivool', { selector: '.discover-inbox-name' });
    expect(screen.queryByLabelText('Save Karnivool')).toBeNull();
    expect(screen.getByText('Artist').getAttribute('href')).toBe('/artist/Karnivool');
    expect(screen.getByLabelText('Remove Karnivool')).toBeTruthy();
  });

  it('says which source did not answer instead of going blank', async () => {
    stub({ new: [RELEASE] }, { unanswered: ['concerts'] });
    mount();
    expect(await screen.findByText("Ticketmaster didn't answer, showing the rest")).toBeTruthy();
    expect(screen.getByText('Memorial')).toBeTruthy();
  });

  it('shows six, then all', async () => {
    stub({
      new: Array.from({ length: 8 }, (_, i) => ({ ...RELEASE, id: i + 10, title: `R${i}` })),
    });
    mount();
    await screen.findByText('R0');
    expect(screen.queryByText('R7')).toBeNull();
    fireEvent.click(screen.getByText('Show all 8'));
    expect(screen.getByText('R7')).toBeTruthy();
  });
});
