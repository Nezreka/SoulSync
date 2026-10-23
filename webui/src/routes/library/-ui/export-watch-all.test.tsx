import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { loadUnwatchedArtists, watchAllSourceField } from '../-library.watch-all';
import { ExportArtistsModal } from './export-modal';
import { WatchAllModal } from './watch-all-modal';

/**
 * The two library-page satellites: Export Artists (openArtistExportModal's
 * port) and Watch All Unwatched (openWatchAllUnwatchedModal's port).
 */

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.currentMusicSourceName;
  cleanup();
});

describe('watchAllSourceField', () => {
  it('keys eligibility on the ACTIVE music source', () => {
    expect(watchAllSourceField('iTunes')).toBe('itunes_artist_id');
    expect(watchAllSourceField('Deezer')).toBe('deezer_id');
    expect(watchAllSourceField('Spotify')).toBe('spotify_artist_id');
    expect(watchAllSourceField(undefined)).toBe('spotify_artist_id');
  });
});

describe('loadUnwatchedArtists', () => {
  it('pages at 400 and splits eligible from ineligible', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        calls.push(url);
        const page = new URL(url, 'http://x').searchParams.get('page');
        return new Response(
          JSON.stringify({
            success: true,
            artists:
              page === '1'
                ? [{ name: 'A', spotify_artist_id: 'sp1' }, { name: 'B' }]
                : [{ name: 'C', spotify_artist_id: 'sp3' }],
            pagination: { has_next: page === '1' },
          }),
        );
      }),
    );
    const progress: number[] = [];
    const { eligible, ineligible } = await loadUnwatchedArtists('spotify_artist_id', (n) =>
      progress.push(n),
    );
    expect(calls).toHaveLength(2);
    expect(calls[0]).toContain('limit=400');
    expect(calls[0]).toContain('watchlist=unwatched');
    expect(eligible.map((a) => a.name)).toEqual(['A', 'C']);
    expect(ineligible.map((a) => a.name)).toEqual(['B']);
    expect(progress).toEqual([0, 2]);
  });
});

describe('ExportArtistsModal', () => {
  function stubExport() {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        calls.push(String(input));
        return new Response('[{"name":"Aphex Twin"}]', {
          headers: { 'X-Export-Count': '7' },
        });
      }),
    );
    return calls;
  }

  it('loads the watchlist JSON by default, count from the header', async () => {
    const calls = stubExport();
    render(<ExportArtistsModal onClose={vi.fn()} />);
    await screen.findByText('7');
    expect(calls[0]).toBe('/api/watchlist/export?format=json&links=0');
    // JSON preview is highlighted, not raw.
    expect(document.querySelector('.arec-code .tok-key')).toBeTruthy();
    // Library-only controls stay hidden on the watchlist scope.
    expect(document.getElementById('wlx-contents-wrap')).toBeNull();
    expect(document.getElementById('wlx-m3u')).toBeNull();
  });

  it('the library scope unlocks counts + M3U and refetches with them', async () => {
    const calls = stubExport();
    render(<ExportArtistsModal onClose={vi.fn()} />);
    await screen.findByText('7');
    fireEvent.click(screen.getByText('Library'));
    await waitFor(() =>
      expect(calls.at(-1)).toBe('/api/library/artists/export?format=json&links=0'),
    );
    expect(document.getElementById('wlx-m3u')).toBeTruthy();
    fireEvent.click(document.getElementById('wlx-contents') as HTMLElement);
    await waitFor(() =>
      expect(calls.at(-1)).toBe('/api/library/artists/export?format=json&links=0&contents=1'),
    );
    fireEvent.click(screen.getByText('CSV'));
    await waitFor(() =>
      expect(calls.at(-1)).toBe('/api/library/artists/export?format=csv&links=0&contents=1'),
    );
  });
});

describe('WatchAllModal', () => {
  it('loads, shows the split, needs TWO clicks, and announces the change on close', async () => {
    window.currentMusicSourceName = 'Spotify';
    window.showToast = vi.fn() as never;
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(url);
        if (init?.method === 'POST') {
          return new Response(
            JSON.stringify({ success: true, added: 2, skipped_already: 1, skipped_no_id: 1 }),
          );
        }
        return new Response(
          JSON.stringify({
            success: true,
            artists: [
              { name: 'A', spotify_artist_id: 'sp1', track_count: 10 },
              { name: 'B', spotify_artist_id: 'sp2', track_count: 5 },
              { name: 'NoId' },
            ],
            pagination: { has_next: false },
          }),
        );
      }),
    );
    const changed = vi.fn();
    window.addEventListener('ss:library-changed', changed);
    const onClose = vi.fn();
    try {
      render(<WatchAllModal onClose={onClose} />);
      // "Monitor" is the label on this page; the API and tables stay watchlist.
      await screen.findByText('Ready to monitor');
      expect(
        document.querySelector('.watch-all-stat-card.eligible .watch-all-stat-value')?.textContent,
      ).toBe('2');
      expect(screen.getByText('1 artist without Spotify ID')).toBeTruthy();

      const confirm = document.getElementById('watch-all-confirm-btn') as HTMLButtonElement;
      expect(confirm.textContent).toBe('Monitor All (2)');

      // First click ARMS only. This action is irreversible and library-wide,
      // and it sits next to Automatic Search in the header, so a single stray
      // click must not fire it.
      const before = calls.length;
      fireEvent.click(confirm);
      expect(calls.length).toBe(before);
      expect(confirm.textContent).toBe('Yes, monitor 2');
      expect(screen.getByText(/Click again to confirm/)).toBeTruthy();

      // ...and the second fires.
      fireEvent.click(confirm);
      await screen.findByText('Now monitoring 2 artists');
      expect(screen.getByText('1 already monitored')).toBeTruthy();
      expect(calls.at(-1)).toBe('/api/library/watchlist-all-unwatched');

      // Closing after a successful add is what refreshes the React list.
      fireEvent.click(screen.getByText('Close'));
      expect(changed).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalled();
    } finally {
      window.removeEventListener('ss:library-changed', changed);
    }
  });

  it('arming and then backing out fires nothing', async () => {
    window.currentMusicSourceName = 'Spotify';
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        calls.push(String(input));
        return new Response(
          JSON.stringify({
            success: true,
            artists: [{ name: 'A', spotify_artist_id: 'sp1' }],
            pagination: { has_next: false },
          }),
        );
      }),
    );
    render(<WatchAllModal onClose={vi.fn()} />);
    await screen.findByText('Ready to monitor');
    const confirm = document.getElementById('watch-all-confirm-btn') as HTMLButtonElement;
    const before = calls.length;

    fireEvent.click(confirm);
    fireEvent.click(screen.getByText('Back'));

    expect(confirm.textContent).toBe('Monitor All (1)');
    expect(calls.length).toBe(before);
  });

  it('closing without adding announces nothing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit) =>
          new Response(
            JSON.stringify({ success: true, artists: [], pagination: { has_next: false } }),
          ),
      ),
    );
    const changed = vi.fn();
    window.addEventListener('ss:library-changed', changed);
    try {
      render(<WatchAllModal onClose={vi.fn()} />);
      await screen.findByText('No unmonitored artists found');
      fireEvent.click(screen.getByText('Cancel'));
      expect(changed).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('ss:library-changed', changed);
    }
  });
});
