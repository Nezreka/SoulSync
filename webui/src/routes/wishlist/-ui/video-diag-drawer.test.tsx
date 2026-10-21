import { QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

import type { VideoDiagnostics } from '../-video.types';

import { VideoDiagDrawer } from './video-sheets';

/**
 * The stuck-row diagnostics drawer, rendered for real: the query, the model
 * and the DOM. These are the cases the vanilla drawer was pinned with
 * (src/test/wishlist-diagnostics-drawer.test.ts before the port).
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BASE: VideoDiagnostics = {
  success: true,
  row: {
    search_attempts: 9,
    last_search_at: '2026-09-02 12:00',
    last_refusal: '15 results for this title, but not this episode yet',
    search_snapshot: null,
  },
  ids: { tmdb_id: 10160, tvdb_id: 1234, imdb_id: 'tt0123456' },
  target_dir: '/media/TV',
  downloads: [],
};

const seen: string[] = [];

function serve(payload: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      seen.push(input instanceof Request ? input.url : String(input));
      return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

async function drawer(payload: unknown, status = 200) {
  serve(payload, status);
  const view = render(
    <QueryClientProvider client={createTestQueryClient()}>
      <VideoDiagDrawer
        diagKey={{ kind: 'episode', tmdb_id: 10160, season_number: 28, episode_number: 27 }}
      />
    </QueryClientProvider>,
  );
  if (status === 200 && (payload as { success?: boolean }).success)
    await screen.findByTestId('video-diag');
  return view.container;
}

function line(host: HTMLElement, key: string): HTMLElement | null {
  return (
    Array.from(host.querySelectorAll<HTMLElement>('.wlp-diag-line')).find(
      (n) => n.querySelector('dt')?.textContent === key,
    ) ?? null
  );
}
const value = (host: HTMLElement, key: string) => line(host, key)?.querySelector('dd')?.textContent;

describe('the drawer', () => {
  it('asks about exactly the row it hangs off', async () => {
    seen.length = 0;
    await drawer(BASE);
    expect(seen.some((u) => u.includes('/api/video/wishlist/diagnostics?'))).toBe(true);
    const url = new URL(seen.find((u) => u.includes('diagnostics'))!);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      kind: 'episode',
      tmdb_id: '10160',
      season_number: '28',
      episode_number: '27',
    });
  });

  it('answers the questions the tooltip could not', async () => {
    const host = await drawer(BASE);
    expect(value(host, 'Attempts')).toBe('9 searches');
    expect(value(host, 'Would land in')).toBe('/media/TV');
    expect(value(host, 'TVDB')).toBe('1234');
    expect(value(host, 'Latest reason')).toContain('not this episode yet');
  });

  it('marks a MISSING id, because that is often the answer', async () => {
    const host = await drawer({ ...BASE, ids: { tmdb_id: 10160, tvdb_id: null, imdb_id: null } });
    expect(line(host, 'TVDB')?.className).toContain('is-gap');
    expect(value(host, 'TVDB')).toBe('—');
  });

  it('says when the row is actually mid-download, not stuck', async () => {
    const host = await drawer({
      ...BASE,
      downloads: [{ status: 'downloading', progress: 42.4, release_title: 'BB S28E27' }],
    });
    expect(value(host, 'In flight')).toBe('downloading · 42% · BB S28E27');
    expect(line(host, 'In flight')?.className).toContain('is-live');
  });

  it('lists each release and the rule that turned it down', async () => {
    const host = await drawer({
      ...BASE,
      row: {
        ...BASE.row,
        search_snapshot: {
          chain: ['torrent'],
          sources: {
            torrent: {
              ran: true,
              results: 15,
              accepted: 0,
              samples: [
                {
                  title: 'Big Brother US S28E25 1080p',
                  accepted: false,
                  rejected: 'Wrong episode',
                },
                {
                  title: 'Big Brother US S28E27 2160p',
                  accepted: false,
                  rejected: '4K not enabled',
                },
              ],
            },
          },
        },
      },
    });
    const rels = Array.from(host.querySelectorAll('.wlp-diag-rel'));
    expect(rels).toHaveLength(2);
    expect(rels[0].textContent).toContain('Big Brother US S28E25 1080p');
    expect(rels[0].textContent).toContain('Wrong episode');
    expect(rels[1].textContent).toContain('4K not enabled');
    expect(host.querySelector('.wlp-diag-src-head')?.textContent).toContain('15 found, 0 usable');
  });

  it('marks an accepted release differently from a refused one', async () => {
    const host = await drawer({
      ...BASE,
      row: {
        ...BASE.row,
        search_snapshot: {
          chain: ['torrent'],
          sources: {
            torrent: {
              ran: true,
              results: 1,
              accepted: 1,
              samples: [{ title: 'Good one', accepted: true, rejected: null }],
            },
          },
        },
      },
    });
    const rel = host.querySelector('.wlp-diag-rel');
    expect(rel?.className).toContain('is-ok');
    expect(rel?.textContent).toContain('usable');
  });

  it('says a source could not search rather than implying it found nothing', async () => {
    const host = await drawer({
      ...BASE,
      row: {
        ...BASE.row,
        search_snapshot: {
          chain: ['soulseek'],
          sources: { soulseek: { ran: false, reason: 'slskd is unreachable', samples: [] } },
        },
      },
    });
    expect(host.querySelector('.wlp-diag-src-head')?.textContent).toContain(
      'could not search: slskd is unreachable',
    );
  });

  it('explains an empty receipt list rather than showing a blank block', async () => {
    // rows searched before receipts existed have none, and a silent gap there
    // reads as "no releases found", which is a different and wrong answer.
    const host = await drawer({
      ...BASE,
      row: {
        ...BASE.row,
        search_snapshot: { chain: ['torrent'], sources: { torrent: { ran: true, results: 15 } } },
      },
    });
    expect(host.querySelector('.wlp-diag-rel.is-none')?.textContent).toContain(
      'not been searched since receipts were added',
    );
  });

  it('renders with no snapshot at all', async () => {
    const host = await drawer(BASE);
    expect(host.querySelector('.wlp-diag-src')).toBeNull();
    expect(host.querySelectorAll('.wlp-diag-line').length).toBeGreaterThan(0);
  });

  it('says so when nothing was recorded, and when the read fails', async () => {
    await drawer({ success: false });
    expect(await screen.findByText('Nothing recorded for this row yet.')).toBeTruthy();
    cleanup();
    await drawer({ success: false, error: 'No such wishlist row.' }, 404);
    expect(await screen.findByText('Could not read the diagnostics.')).toBeTruthy();
  });
});
