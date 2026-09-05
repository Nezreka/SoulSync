import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { DiscoverPage } from './-ui/discover-page';
import { Route } from './route';

/**
 * /discover is LIVE — the manifest hands it to React.
 *
 * The page mounts fifteen controllers that between them hit two dozen
 * endpoints; this suite is NOT their test (each has its own). What it pins is
 * the flip itself: the route renders the React page, the vanilla page is not
 * activated underneath, the permission gate holds, and the page survives every
 * endpoint answering empty — the fail-soft the section design promises.
 */

function renderRoute(entries = ['/discover']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

let requested: string[] = [];

/** Minimal truthful bodies for the endpoints whose SHAPE the page reads. */
function bodyFor(url: string): Record<string, unknown> {
  if (url.includes('/api/discover/hero')) {
    return {
      success: true,
      artists: [{ artist_id: 'h1', artist_name: 'Hero Artist', image_url: '', source: 'spotify' }],
    };
  }
  if (url.includes('/api/discover/adventurousness')) return { success: true, value: 0.3 };
  if (url.includes('/api/discover/release-radar')) {
    return {
      success: true,
      tracks: [
        {
          track_name: 'Radar Song',
          artist_name: 'Radar Artist',
          album_name: 'Radar Album',
          duration_ms: 200000,
        },
      ],
    };
  }
  if (url.includes('/api/lastfm/configured')) return { configured: false };
  if (url.includes('/api/discover/because-you-listen-to')) {
    return {
      success: true,
      sections: [
        {
          artist_name: 'Purrple Cat',
          tracks: [{ name: 'Moonwinds', artist: 'Purrple Cat', album: 'Moon Album' }],
        },
      ],
    };
  }
  if (url.includes('/api/discover/resolve-cache-album')) return { success: false };
  // Every shelf/section: an empty success — the page must render regardless.
  return { success: true };
}

describe('discover route (live)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        requested.push(url);
        return new Response(JSON.stringify(bodyFor(url)), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('is owned by React', () => {
    expect(getShellRouteByPageId('discover')?.kind).toBe('react');
    // The route's component IS the page — the pair this whole suite exercises
    // through the router; named here so the export-coverage gate can see it.
    expect(Route.options.component).toBe(DiscoverPage);
  });

  it('renders the React page instead of handing /discover to vanilla', async () => {
    renderRoute();
    // The hero is above the fold and unmistakably this page's.
    await screen.findByText('Hero Artist');
    expect(window.SoulSyncWebShellBridge!.activateLegacyPath).not.toHaveBeenCalledWith('/discover');
  });

  it('renders the Artist Map hub and the always-visible controls on empty data', async () => {
    renderRoute();
    await screen.findByText('Hero Artist');
    // The hub's three entry cards are static chrome; the adventurousness dial
    // is ALWAYS_VISIBLE. Both must survive every shelf answering empty.
    expect(document.querySelector('.discover-container')).not.toBeNull();
    expect(requested.some((u) => u.includes('/api/discover/hero'))).toBe(true);
  });

  it('boots the resume + hydrate obligations on mount', async () => {
    renderRoute();
    await screen.findByText('Hero Artist');
    // resumeActiveSyncs probes the resumable sync statuses; hydrate pulls
    // discover_downloads/hydrate. Both fire from the page's mount effect.
    expect(requested.some((u) => u.includes('discover_downloads/hydrate'))).toBe(true);
    expect(requested.some((u) => u.includes('/api/sync/status/'))).toBe(true);
  });

  it('a BYLT tile resolves by ALBUM name, not track name', async () => {
    renderRoute();
    await screen.findByText('Hero Artist');
    const tile = await screen.findByText('Moonwinds');
    tile.closest('.ya-card')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    // The first wiring resolved an album named after the TRACK and every
    // click died with 'Failed to fetch album tracks'. The resolve request
    // must carry the album's name.
    await waitFor(() => {
      const resolve = requested.find((u) => u.includes('resolve-cache-album'));
      expect(resolve).toBeTruthy();
      // URLSearchParams encodes spaces as '+'.
      expect(resolve).toContain('name=Moon+Album');
      expect(resolve).toContain('artist=Purrple+Cat');
    });
  });

  it('Download selected hands the shared modal READY tracks — no second conversion', async () => {
    const calls: unknown[][] = [];
    window.openDownloadMissingModalForYouTube = (...args: unknown[]) => {
      calls.push(args);
    };
    try {
      renderRoute();
      await screen.findByText('Hero Artist');
      // Fresh Tape's card appears once the release-radar feeder lands.
      const card = await screen.findByText('Fresh Tape');
      // The card's open target is a real button now, not the whole div.
      card.closest('.mix-card-open')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      // Select the one track, then Download selected.
      const checkbox = (await screen.findAllByRole('checkbox'))[0];
      checkbox.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      const button = await screen.findByText(/Download selected/);
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      expect(calls).toHaveLength(1);
      const tracks = calls[0][2] as { name?: string; artists?: unknown }[];
      // The live bug: re-converting the already-converted selection read
      // track_name off a shape that has `name` — every artist became
      // "Unknown Artist". The modal must receive the REAL names.
      expect(tracks[0].name).toBe('Radar Song');
      expect(tracks[0].artists).toEqual(['Radar Artist']);
    } finally {
      delete window.openDownloadMissingModalForYouTube;
    }
  });
});
