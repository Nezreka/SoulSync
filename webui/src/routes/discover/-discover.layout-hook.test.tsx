import type { ReactNode } from 'react';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  defaultDiscoverLayout,
  layoutSectionsByZone,
  type DiscoverLayoutSection,
} from './-discover.layout';
import { useDiscoverLayout } from './-discover.use-layout';

/**
 * The layout hook contract:
 *
 * - the server merges saved rows over the defaults; the hook exposes the
 *   result per zone;
 * - while the fetch is pending or has failed, the hook falls back to the
 *   defaults — the page never renders without sections, and no saved
 *   preferences render exactly the current order.
 */

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('defaultDiscoverLayout', () => {
  it('is the current page order: 19 sections, all enabled', () => {
    const entries = defaultDiscoverLayout();
    expect(entries).toHaveLength(19);
    expect(entries.every((e) => e.enabled)).toBe(true);
    expect(entries.map((e) => e.id)).toEqual([
      'your-mixes-section',
      'adv-wave',
      'listening-recs-section',
      'recommended-artists-section',
      'discover-bylt-sections',
      'recent-releases',
      'cache-genre-releases',
      'seasonal-albums-section',
      'cache-undiscovered',
      'cache-label-explorer',
      'your-albums-section',
      'your-artists-section',
      'year-mixes-section',
      'cache-deep-cuts',
      'cache-genre-explorer',
      'lastfm-radio',
      'listenbrainz',
      'deezer-editorial',
      'build-a-playlist',
    ]);
  });
});

describe('layoutSectionsByZone', () => {
  it('groups the defaults into the four zones in order', () => {
    const byZone = layoutSectionsByZone(defaultDiscoverLayout());
    expect(byZone['for-you']).toEqual([
      'your-mixes-section',
      'adv-wave',
      'listening-recs-section',
      'recommended-artists-section',
      'discover-bylt-sections',
    ]);
    expect(byZone['new-missing']).toEqual([
      'recent-releases',
      'cache-genre-releases',
      'seasonal-albums-section',
      'cache-undiscovered',
      'cache-label-explorer',
      'your-albums-section',
    ]);
    expect(byZone['library']).toEqual([
      'your-artists-section',
      'year-mixes-section',
      'cache-deep-cuts',
    ]);
    expect(byZone['tools']).toEqual([
      'cache-genre-explorer',
      'lastfm-radio',
      'listenbrainz',
      'deezer-editorial',
      'build-a-playlist',
    ]);
  });

  it('drops disabled sections, sorts by position, ignores unknown ids', () => {
    const entries: DiscoverLayoutSection[] = [
      { id: 'build-a-playlist', zone: 'tools', enabled: true, position: 1 },
      { id: 'cache-genre-explorer', zone: 'tools', enabled: false, position: 0 },
      // a stale cached payload must not take the page down
      {
        id: 'retired-section',
        zone: 'tools',
        enabled: true,
        position: 2,
      } as unknown as DiscoverLayoutSection,
    ];
    expect(layoutSectionsByZone(entries)['tools']).toEqual(['build-a-playlist']);
  });
});

describe('useDiscoverLayout fallback', () => {
  it('serves the defaults while the fetch is pending', () => {
    server.use(
      http.get('/api/discover/layout', async () => {
        await new Promise((r) => setTimeout(r, 50));
        return HttpResponse.json({ success: true, sections: [] });
      }),
    );
    const { result } = renderHook(() => useDiscoverLayout(null), {
      wrapper: makeWrapper(),
    });
    expect(result.current.isPending).toBe(true);
    expect(result.current.entries).toEqual(defaultDiscoverLayout());
    expect(result.current.sectionsByZone['for-you'][0]).toBe('your-mixes-section');
  });

  it('falls back to the defaults when the fetch fails', async () => {
    server.use(http.get('/api/discover/layout', () => HttpResponse.error()));
    const { result } = renderHook(() => useDiscoverLayout(null), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(result.current.entries).toEqual(defaultDiscoverLayout());
    expect(result.current.sectionsByZone['tools']).toContain('build-a-playlist');
  });

  it('renders the saved layout per zone when the fetch succeeds', async () => {
    const sections = defaultDiscoverLayout().map((e) =>
      e.id === 'your-mixes-section'
        ? { ...e, zone: 'tools' as const, position: 0 }
        : e.id === 'adv-wave'
          ? { ...e, enabled: false }
          : e,
    );
    server.use(
      http.get('/api/discover/layout', () => HttpResponse.json({ success: true, sections })),
    );
    const { result } = renderHook(() => useDiscoverLayout(null), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(result.current.sectionsByZone['tools'][0]).toBe('your-mixes-section');
    expect(result.current.sectionsByZone['for-you']).not.toContain('adv-wave');
  });
});
