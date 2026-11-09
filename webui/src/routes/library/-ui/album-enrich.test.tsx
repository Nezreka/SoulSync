import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { AlbumOverflowMenu, LibraryV2CanWriteContext } from './library-v2-page';

const album = {
  id: 12,
  title: 'Album',
  year: 2026,
  album_type: 'album',
  release_date: '2026-01-01',
  explicit: false,
  label: null,
  style: null,
  mood: null,
  user_overrides: {},
  quality_profile_id: 1,
} as React.ComponentProps<typeof AlbumOverflowMenu>['album'];

function renderMenu() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <LibraryV2CanWriteContext.Provider value>
        <AlbumOverflowMenu album={album} />
      </LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
}

// a real press: mousedown first, then the click on the same element
function press(element: HTMLElement) {
  fireEvent.mouseDown(element);
  fireEvent.mouseUp(element);
  fireEvent.click(element);
}

function openEnrichSubmenu() {
  fireEvent.click(screen.getByTitle('More actions'));
  fireEvent.click(screen.getByRole('button', { name: /Enrich/ }));
}

let toast: ReturnType<typeof vi.fn>;

beforeEach(() => {
  toast = vi.fn();
  window.showToast = toast as typeof window.showToast;
});

afterEach(() => {
  delete (window as { showToast?: unknown }).showToast;
});

describe('album menu enrich (review 5 W3-TL-1)', () => {
  it('a press on a service inside the portaled album menu sends the enrich', async () => {
    const services: string[] = [];
    server.use(
      http.post('/api/library/v2/albums/12/enrich', async ({ request }) => {
        services.push(((await request.json()) as { service: string }).service);
        return HttpResponse.json({ success: true, resynced: true });
      }),
    );
    renderMenu();
    openEnrichSubmenu();

    press(screen.getByRole('button', { name: 'Deezer' }));

    await waitFor(() => expect(services).toEqual(['deezer']));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Enriched and refreshed.', 'success'));
  });

  it('a press on "Enrich with all" reaches every service', async () => {
    const services: string[] = [];
    server.use(
      http.post('/api/library/v2/albums/12/enrich', async ({ request }) => {
        services.push(((await request.json()) as { service: string }).service);
        return HttpResponse.json({ success: true, resynced: false });
      }),
    );
    renderMenu();
    openEnrichSubmenu();

    press(screen.getByRole('button', { name: 'Enrich with all' }));

    await waitFor(() => expect(services).toContain('bandcamp'));
    expect(services).not.toContain('genius');
  });

  it('with the enrich list open, a press on another album menu item still lands', () => {
    renderMenu();
    openEnrichSubmenu();
    expect(screen.getByRole('button', { name: 'Enrich with all' })).toBeInTheDocument();

    press(screen.getByRole('button', { name: 'Album details' }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
