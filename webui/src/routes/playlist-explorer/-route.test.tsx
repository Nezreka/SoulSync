import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { EXPLORER_PLAYLISTS_QUERY_KEY, ExplorerPage } from './-ui/explorer-page';
import { Route } from './route';

/**
 * The route end to end: the picker paints from /api/mirrored-playlists, a
 * build streams a tree, and the tree's interactions reach the same state the
 * action bar reports.
 */

const PLAYLISTS = [
  {
    id: 1,
    name: 'Road Trip',
    source: 'spotify',
    total_count: 10,
    discovered_count: 10,
    image_url: 'rt.jpg',
  },
  { id: 2, name: 'Half Done', source: 'spotify', total_count: 10, discovered_count: 2 },
];

const TREE_LINES = [
  '{"type":"meta","playlist_name":"Road Trip","total_tracks":10,"total_artists":2}\n',
  '{"type":"artist","name":"Boards of Canada","artist_id":"art1","albums":[{"spotify_id":"al1","title":"Geogaddi","album_type":"album","track_count":23}]}\n',
  '{"type":"artist","name":"Aphex Twin","artist_id":"art2","albums":[{"spotify_id":"al2","title":"SAW II","track_count":24}]}\n',
  '{"type":"complete"}\n',
];

function ndjson(lines: string[]) {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
        else controller.close();
      },
    }),
  );
}

function stubFetch(playlists: unknown[] = PLAYLISTS) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes('/api/mirrored-playlists')) {
        return new Response(JSON.stringify(playlists), {
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/playlist-explorer/build-tree')) return ndjson(TREE_LINES);
      if (url.includes('/api/playlist-explorer/album-tracks/')) {
        return new Response(
          JSON.stringify({
            success: true,
            tracks: [{ track_number: 1, name: 'Music Is Math', duration_ms: 320000 }],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        );
      }
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
}

function renderRoute() {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: ['/playlist-explorer'] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

async function buildTheTree() {
  renderRoute();
  await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
  fireEvent.click(document.querySelector('.explorer-picker-card') as HTMLElement);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /Explore/ }));
  });
  await waitFor(() => expect(document.querySelector('#explorer-root')).toBeTruthy());
}

describe('playlist-explorer route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
    delete window.showToast;
  });

  it('exports a stable query key so the loader and the page share a cache entry', () => {
    expect(EXPLORER_PLAYLISTS_QUERY_KEY).toEqual(['playlist-explorer', 'mirrored-playlists']);
  });

  it('mounts ExplorerPage at /playlist-explorer', () => {
    // The route's own module is what the router loads; naming both here keeps
    // the export-coverage gate honest about the seam.
    expect(Route.options.component).toBe(ExplorerPage);
    // Guarded like every other React page, and it warms the picker's query.
    expect(typeof Route.options.beforeLoad).toBe('function');
    expect(typeof Route.options.loader).toBe('function');
  });

  it('paints the picker and the empty state before anything is built', async () => {
    stubFetch();
    renderRoute();

    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(screen.getByText('Playlist Explorer')).toBeInTheDocument();
    expect(document.querySelector('#explorer-empty')).toBeTruthy();
    // The under-discovered playlist is inert and offers Discover instead.
    expect(document.querySelectorAll('.explorer-picker-card.not-ready')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Discover' })).toBeInTheDocument();
    // No action bar until a tree exists.
    expect(document.querySelector('#explorer-action-bar')).toBeNull();
  });

  it('selects a playlist, streams the tree, and shows the action bar', async () => {
    stubFetch();
    await buildTheTree();

    expect(screen.getByText('10 tracks · 2 artists')).toBeInTheDocument();
    expect(document.querySelectorAll('.explorer-node-artist')).toHaveLength(2);
    expect(document.querySelector('#explorer-empty')).toBeNull();
    expect(document.querySelector('#explorer-action-bar')).toBeTruthy();
    expect(screen.getByText('0 albums selected')).toBeInTheDocument();
  });

  it('expands an artist, then selects an album through the click discriminator', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubFetch();
    await buildTheTree();

    fireEvent.click(document.querySelector('#explorer-node-Boards_of_Canada') as HTMLElement);
    await waitFor(() => expect(document.querySelectorAll('.explorer-node-album')).toHaveLength(1));

    fireEvent.click(document.querySelector('.explorer-node-album') as HTMLElement);
    // Nothing happens for 250ms — the click might still turn out to be a
    // double-click asking for the tracklist.
    expect(screen.getByText('0 albums selected')).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText('1 album selected')).toBeInTheDocument();
    expect((document.querySelector('.explorer-node-album') as HTMLElement).className).toContain(
      'selected',
    );
    vi.useRealTimers();
  });

  it('select-all takes every real album id, and deselect clears them', async () => {
    stubFetch();
    await buildTheTree();

    fireEvent.click(screen.getByRole('button', { name: /Select All/ }));
    expect(screen.getByText('2 albums selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Deselect/ }));
    expect(screen.getByText('0 albums selected')).toBeInTheDocument();
  });

  it('refuses to open the wishlist with nothing selected', async () => {
    window.showToast = vi.fn();
    stubFetch();
    await buildTheTree();

    fireEvent.click(screen.getByRole('button', { name: /Add to Wishlist/ }));
    expect(window.showToast).toHaveBeenCalledWith('No albums selected', 'error');
    expect(document.querySelector('#explorer-wishlist-overlay')).toBeNull();
  });

  it('opens the wishlist modal for the current selection', async () => {
    stubFetch();
    await buildTheTree();

    fireEvent.click(screen.getByRole('button', { name: /Select All/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add to Wishlist/ }));

    const overlay = document.querySelector('#explorer-wishlist-overlay');
    expect(overlay).toBeTruthy();
    // "Add to Wishlist" is also the action-bar button, so scope to the title.
    expect(overlay?.querySelector('.discog-modal-title')?.textContent).toBe('Add to Wishlist');
    expect(screen.getByText('2 artists · 2 releases')).toBeInTheDocument();
    expect(overlay?.querySelectorAll('.discog-card')).toHaveLength(2);
  });

  it('tells the user to pick a playlist first', async () => {
    window.showToast = vi.fn();
    stubFetch();
    renderRoute();
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Explore/ }));
    });
    expect(window.showToast).toHaveBeenCalledWith('Select a playlist first', 'error');
  });

  it('still answers every helper.js anchor for this page', async () => {
    // helper.js's help mode walks UP the DOM matching these selectors
    // (helper.js:2070-2095). They were written against the vanilla markup this
    // port deleted; each one has to still resolve, or clicking that part of
    // the page in help mode silently explains nothing.
    stubFetch();
    await buildTheTree();
    fireEvent.click(screen.getByRole('button', { name: /Select All/ }));

    for (const selector of [
      '#playlist-explorer-page',
      '#explorer-playlist-picker',
      '.explorer-mode-btn',
      '#explorer-build-btn',
      '#explorer-action-bar',
    ]) {
      expect(document.querySelector(selector), `missing helper.js anchor ${selector}`).toBeTruthy();
    }
    // ...and the page root must NOT carry the `page` class: the shell styles
    // `.page { display: none }`, so a React page wearing it renders invisible.
    expect(
      (document.querySelector('#playlist-explorer-page') as HTMLElement).classList.contains('page'),
    ).toBe(false);
  });

  it('copes with a backend that has no mirrored playlists', async () => {
    stubFetch([]);
    renderRoute();
    await waitFor(() =>
      expect(
        screen.getByText('No mirrored playlists found. Sync a playlist first.'),
      ).toBeInTheDocument(),
    );
  });
});
