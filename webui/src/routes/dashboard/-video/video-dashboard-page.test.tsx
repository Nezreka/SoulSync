/**
 * The video dashboard on React (Boulder, Oct 28 2026: "standardize the video
 * dashboard to be just like the music side dashboard ... structurally").
 *
 * It is the music dashboard's skeleton (hero, main column, side rail,
 * footer) on video data, and it keeps the contracts the vanilla page had:
 * the orb stage the video orbs anchor to, real links for every card, bands
 * that vanish when empty.
 */

import type { ReactNode } from 'react';

import { QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { CalendarEpisode } from '../../calendar/-calendar.types';

import { isoOf } from '../../calendar/-calendar.model';
import { VideoDashboardPage } from './video-dashboard-page';
import { VIDEO_WORKER_IDS } from './video-hero';

const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

const TODAY = isoOf(new Date());

function ep(over: Partial<CalendarEpisode>): CalendarEpisode {
  return {
    id: 1,
    show_id: 10,
    show_tmdb_id: 100,
    show_title: 'Show',
    season_number: 1,
    episode_number: 1,
    title: null,
    overview: null,
    air_date: TODAY,
    has_file: 0,
    monitored: 1,
    airs_time: '23:59',
    acq: 'unaired',
    needs_action: false,
    ...over,
  };
}

interface Stub {
  recent?: { id: number; kind: 'movie' | 'show'; title: string; year?: number }[];
  continueItems?: unknown[];
  downloads?: unknown[];
  episodes?: CalendarEpisode[];
  automations?: unknown[];
  issues?: number;
  findings?: number;
}

let wishlistBody: unknown = null;
let calendarUrl: URL | null = null;

function stub(s: Stub = {}) {
  wishlistBody = null;
  server.use(
    http.get('/api/video/dashboard', () =>
      HttpResponse.json({
        library: { movies: 8873, shows: 3470, episodes: 105674, size_bytes: 1e14 },
        downloads: { active: 0, finished: 0, speed_bps: 0 },
        watchlist: 978,
        wishlist: 382,
        server: 'plex',
        recent: s.recent ?? [],
      }),
    ),
    http.get('/api/video/dashboard/continue-watching', () =>
      HttpResponse.json({ items: s.continueItems ?? [] }),
    ),
    http.get('/api/video/downloads/active', () =>
      HttpResponse.json({ downloads: s.downloads ?? [] }),
    ),
    http.get('/api/video/scan/status', () => HttpResponse.json({ state: 'idle' })),
    http.get('/api/video/issues/counts', () =>
      HttpResponse.json({ counts: { open: s.issues ?? 0 } }),
    ),
    http.get('/api/video/repair/findings/counts', () =>
      HttpResponse.json({ pending: s.findings ?? 0 }),
    ),
    http.get('/api/video/enrichment/coverage', () =>
      HttpResponse.json({ movies: { total: 10, tmdb_enriched: 9 }, shows: {} }),
    ),
    http.get('/api/automations', () => HttpResponse.json(s.automations ?? [])),
    http.get('/api/automations/progress', () => HttpResponse.json({})),
    http.get('/api/video/calendar', ({ request }) => {
      calendarUrl = new URL(request.url);
      return HttpResponse.json({
        today: TODAY,
        start: TODAY,
        end: TODAY,
        days: 15,
        scope: 'watchlist',
        specials: false,
        total: 0,
        owned: 0,
        acq_counts: [],
        needs_action: 0,
        schedule: { refreshed_at: null, stale: false, age_days: null },
        automations: { airing: { enabled: true, last_run: null }, refresh: null },
        episodes: s.episodes ?? [],
        movies: [],
      });
    }),
    http.post('/api/video/calendar/wishlist-missing', async ({ request }) => {
      wishlistBody = await request.json();
      return HttpResponse.json({ success: true, added: 2, shows: 1 });
    }),
  );
}

function wrap(node: ReactNode) {
  return <QueryClientProvider client={createTestQueryClient()}>{node}</QueryClientProvider>;
}

function mount(opts: { isAdmin?: boolean; canDownload?: boolean } = {}) {
  return render(
    wrap(
      <VideoDashboardPage
        profileId={1}
        isAdmin={opts.isAdmin ?? true}
        canDownload={opts.canDownload ?? true}
      />,
    ),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('the music dashboard skeleton', () => {
  it('is #dashboard-page, so the music styling applies: hero, two columns, footer', async () => {
    stub();
    const { container } = mount();
    const page = container.querySelector('#dashboard-page');
    expect(page?.getAttribute('data-media')).toBe('video');
    expect(page?.querySelector(':scope > .dashboard-header.dash-hero')).not.toBeNull();
    expect(page?.querySelector(':scope > .dash-layout > .dash-main')).not.toBeNull();
    expect(page?.querySelector(':scope > .dash-layout > aside.dash-side')).not.toBeNull();
    expect(page?.querySelector(':scope > footer.dash-footer')).not.toBeNull();
    expect(await screen.findByText('8,873')).toBeTruthy();
    expect(screen.getByText('105.7K')).toBeTruthy();
    expect(await screen.findByText('Plex library')).toBeTruthy();
  });
});

describe('the orb stage', () => {
  it('carries every video worker and the hub, where video-worker-orbs.js looks', () => {
    stub();
    const { container } = mount();
    const stage = container.querySelector('[data-video-orb-stage]');
    expect(stage).not.toBeNull();
    const actions = stage!.querySelector(':scope > .header-actions')!;
    expect(
      [...actions.querySelectorAll('[data-video-enrich]')].map((b) =>
        b.getAttribute('data-video-enrich'),
      ),
    ).toEqual(VIDEO_WORKER_IDS);
    expect(VIDEO_WORKER_IDS).toHaveLength(13);
    for (const svc of VIDEO_WORKER_IDS) {
      const tip = actions.querySelector(`[data-video-enrich-tooltip="${svc}"]`)!;
      // video-enrichment.js writes into these three
      expect(tip.querySelector('[data-video-enrich-status]')).not.toBeNull();
      expect(tip.querySelector('[data-video-enrich-current]')).not.toBeNull();
      expect(tip.querySelector('[data-video-enrich-progress]')).not.toBeNull();
    }
    expect(actions.querySelector('[data-video-manage-workers]')).not.toBeNull();
  });

  it('counts busy workers off the classes the vanilla sets', async () => {
    stub();
    const { container } = mount();
    expect(screen.getByText('Workers resting')).toBeTruthy();
    container.querySelector('[data-video-enrich="tmdb"]')!.classList.add('active');
    expect(await screen.findByText('1 worker busy')).toBeTruthy();
  });

  it('pings the vanilla orbs and buttons on mount', () => {
    stub();
    const refresh = vi.fn();
    const prime = vi.fn();
    window.videoWorkerOrbs = { refresh, onStatus: vi.fn() };
    window.videoEnrichmentPrime = prime;
    mount();
    expect(refresh).toHaveBeenCalled();
    expect(prime).toHaveBeenCalled();
    delete window.videoWorkerOrbs;
    delete window.videoEnrichmentPrime;
  });
});

describe('bands that vanish when empty', () => {
  it('shows no continue, downloads or recent band with nothing to show', async () => {
    stub();
    const { container } = mount();
    await screen.findByText('8,873');
    expect(container.querySelector('[data-card="continue"]')).toBeNull();
    expect(container.querySelector('[data-card="downloads"]')).toBeNull();
    expect(container.querySelector('[data-card="recent"]')).toBeNull();
  });

  it('continue watching cards are real links to the right page', async () => {
    stub({
      continueItems: [
        {
          kind: 'show',
          id: 55,
          show_id: 9,
          title: 'Severance',
          subtitle: 'S2 · E3',
          runtime_minutes: 50,
          view_offset_ms: 20 * 60000,
        },
        { kind: 'movie', id: 7, title: 'Heat', reason: 'up_next' },
      ],
    });
    const { container } = mount();
    const band = await waitFor(() => {
      const b = container.querySelector('[data-card="continue"]');
      if (!b) throw new Error('not yet');
      return b as HTMLElement;
    });
    const links = [...band.querySelectorAll('a.vcw-card')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/video-detail/library/show/9',
      '/video-detail/library/movie/7',
    ]);
    expect(within(band).getByText('30 min left')).toBeTruthy();
    // an up-next card says what it is instead of drawing a 0% bar
    expect(links[1].querySelector('.vcw-bar')).toBeNull();
    expect(within(links[1] as HTMLElement).getAllByText('Up next').length).toBeGreaterThan(0);
  });

  it('a plain click routes in-app, a modified click keeps the href', async () => {
    stub({ continueItems: [{ kind: 'movie', id: 7, title: 'Heat' }] });
    const heard = vi.fn();
    document.addEventListener('soulsync:video-open-detail', heard as EventListener);
    const { container } = mount();
    const link = await waitFor(() => {
      const a = container.querySelector('a.vcw-card');
      if (!a) throw new Error('not yet');
      return a;
    });
    fireEvent.click(link, { ctrlKey: true });
    expect(heard).not.toHaveBeenCalled();
    fireEvent.click(link);
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toEqual({
      kind: 'movie',
      id: 7,
      source: 'library',
    });
    document.removeEventListener('soulsync:video-open-detail', heard as EventListener);
  });

  it('downloading now takes only in-flight rows, capped, saying how many it left out', async () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({
      id: i,
      status: 'downloading',
      title: `T${i}`,
      progress: 50,
    }));
    stub({ downloads: [...rows, { id: 99, status: 'completed', title: 'Done' }] });
    const { container } = mount();
    const band = await waitFor(() => {
      const b = container.querySelector('[data-card="downloads"]');
      if (!b) throw new Error('not yet');
      return b as HTMLElement;
    });
    expect(band.querySelectorAll('.vdn-card')).toHaveLength(4);
    expect(within(band).getByText('and 4 more')).toBeTruthy();
    // it lives in the rail, between this week and up next
    expect(band.closest('.dash-side')).not.toBeNull();
    expect(within(band).queryByText('Done')).toBeNull();
  });

  it('a queued row draws no bar', async () => {
    stub({ downloads: [{ id: 1, status: 'queued', title: 'Waiting' }] });
    const { container } = mount();
    const card = await waitFor(() => {
      const c = container.querySelector('.vdn-card');
      if (!c) throw new Error('not yet');
      return c;
    });
    expect(card.querySelector('.vdn-bar')).toBeNull();
  });

  it('recently added filters by kind, and only offers tabs when there is a mix', async () => {
    stub({
      recent: [
        { id: 1, kind: 'movie', title: 'Heat', year: 1995 },
        { id: 2, kind: 'show', title: 'Severance' },
      ],
    });
    const { container } = mount();
    const band = await waitFor(() => {
      const b = container.querySelector('[data-card="recent"]');
      if (!b) throw new Error('not yet');
      return b as HTMLElement;
    });
    expect(band.querySelectorAll('a.dash-rail-card')).toHaveLength(2);
    fireEvent.click(within(band).getByRole('tab', { name: 'Shows' }));
    const left = [...band.querySelectorAll('a.dash-rail-card')];
    expect(left.map((a) => a.getAttribute('href'))).toEqual(['/video-detail/library/show/2']);
  });
});

describe('the rail', () => {
  it('the week card says whether what aired landed, and can wishlist the rest', async () => {
    const past = isoOf(new Date(Date.now() - 2 * 86400000));
    stub({
      episodes: [
        ep({ id: 1, air_date: past, acq: 'owned' }),
        ep({ id: 2, show_tmdb_id: 2, air_date: past, acq: 'missing' }),
        ep({ id: 3, show_tmdb_id: 3, air_date: past, acq: 'missing' }),
        ep({ id: 4, show_tmdb_id: 4 }),
      ],
    });
    const { container } = mount();
    const card = await waitFor(() => {
      const c = container.querySelector('[data-card="week"]');
      if (!c) throw new Error('not yet');
      return c as HTMLElement;
    });
    expect(within(card).getByText('1 of 3 that aired are in your library.')).toBeTruthy();
    // one read covers a week back and a week ahead
    expect(calendarUrl?.searchParams.get('days')).toBe('15');
    expect(calendarUrl?.searchParams.get('scope')).toBe('watchlist');
    fireEvent.click(within(card).getByRole('button', { name: 'Wishlist 2 missing' }));
    await waitFor(() => expect(wishlistBody).toMatchObject({ end: TODAY, scope: 'watchlist' }));
  });

  it('a profile that asks first gets no wishlist button', async () => {
    const past = isoOf(new Date(Date.now() - 2 * 86400000));
    stub({ episodes: [ep({ id: 2, air_date: past, acq: 'missing' })] });
    const { container } = mount({ canDownload: false });
    await waitFor(() => {
      if (!container.querySelector('[data-card="week"]')) throw new Error('not yet');
    });
    expect(screen.queryByRole('button', { name: /Wishlist \d+ missing/ })).toBeNull();
  });

  it('shows only video automations', async () => {
    stub({
      automations: [
        {
          id: 1,
          name: 'Auto-Wishlist Airing',
          action_type: 'video_auto_wishlist_airing',
          enabled: true,
          trigger_type: 'schedule',
          trigger_config: { interval: 4, unit: 'hours' },
        },
        {
          id: 2,
          name: 'Music Backup',
          action_type: 'backup_database',
          enabled: true,
          trigger_type: 'schedule',
          trigger_config: { interval: 1, unit: 'days' },
        },
      ],
    });
    const { container } = mount();
    const card = await waitFor(() => {
      const c = container.querySelector('[data-card="automations"]');
      if (!c || !c.textContent?.includes('Auto-Wishlist')) throw new Error('not yet');
      return c as HTMLElement;
    });
    expect(within(card).queryByText('Music Backup')).toBeNull();
  });

  it('issues and findings tiles only appear when something needs a human', async () => {
    stub();
    const first = mount();
    await screen.findByText('8,873');
    expect(screen.queryByText(/Open issue/)).toBeNull();
    first.unmount();
    stub({ issues: 2, findings: 1 });
    mount();
    expect(await screen.findByText('Open issues')).toBeTruthy();
    expect(await screen.findByText('Finding to review')).toBeTruthy();
  });

  it('the studios are for admins', async () => {
    stub({ recent: [{ id: 1, kind: 'movie', title: 'Heat' }] });
    const admin = mount();
    expect(await screen.findByText('Overlay Studio')).toBeTruthy();
    admin.unmount();
    stub({ recent: [{ id: 1, kind: 'movie', title: 'Heat' }] });
    mount({ isAdmin: false });
    await screen.findByText('Heat');
    expect(screen.queryByText('Overlay Studio')).toBeNull();
  });
});

describe('the card styles the bands reuse', () => {
  it('the resume card is landscape and lets a long title ellipsise', () => {
    const art = CSS.slice(CSS.indexOf('.vcw-art {'), CSS.indexOf('.vcw-card:hover .vcw-art'));
    expect(art).toContain('aspect-ratio: 16 / 9');
    const card = CSS.slice(CSS.indexOf('.vcw-card {'), CSS.indexOf('.vcw-art {'));
    expect(card).toContain('min-width: 0');
  });

  it('the resume card keeps its open affordance hidden until hover', () => {
    const open = CSS.slice(CSS.indexOf('.vcw-open {'), CSS.indexOf('.vcw-badge'));
    expect(open).toContain('opacity: 0');
    expect(CSS).toContain('.vcw-card:hover .vcw-open');
  });

  it('download cards sit in a grid, and their title can shrink', () => {
    const grid = CSS.slice(CSS.indexOf('.vdn-grid {'), CSS.indexOf('.vdn-card {'));
    expect(grid).toContain('auto-fill');
    const body = CSS.slice(CSS.indexOf('.vdn-body {'), CSS.indexOf('.vdn-name {'));
    expect(body).toContain('min-width: 0');
  });
});
