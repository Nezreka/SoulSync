/**
 * The React video calendar (Boulder, Oct 27 2026: "the calendar itself feels
 * weak. not very descriptive ... neither shows the state of the item like if
 * its available, wishlisted, missing").
 *
 * The vanilla page put the state badge inside the art box and the default
 * compact view hid the art box, so no card showed a state at all; upcoming
 * episodes had no badge in any view. These pin that every card in every view
 * says where it stands, and the actions that hang off those states.
 */

import type { ReactNode } from 'react';

import { QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { CalendarEpisode, CalendarPayload } from '../-calendar.types';
import type { CalendarSearch } from '../route';

import { CalendarPage } from './calendar-page';

function ep(over: Partial<CalendarEpisode> = {}): CalendarEpisode {
  return {
    id: 1,
    show_id: 10,
    show_tmdb_id: 100,
    show_title: 'Show',
    season_number: 1,
    episode_number: 1,
    title: 'Pilot',
    overview: null,
    air_date: '2026-10-27',
    has_file: 0,
    monitored: 1,
    airs_time: '21:00',
    acq: 'unaired',
    needs_action: false,
    ...over,
  };
}

const EPISODES: CalendarEpisode[] = [
  ep({
    id: 1,
    show_title: 'Heartland',
    show_tmdb_id: 1,
    air_date: '2026-10-25',
    airs_time: '19:00',
    acq: 'owned',
    has_file: 1,
  }),
  ep({
    id: 2,
    show_title: 'Natural High',
    show_tmdb_id: 2,
    air_date: '2026-10-26',
    airs_time: '12:00',
    acq: 'missing',
    needs_action: true,
  }),
  ep({
    id: 3,
    show_title: 'Dancing with the Stars',
    show_tmdb_id: 3,
    airs_time: '20:00',
    acq: 'downloading',
    progress: 42,
    eta_seconds: 360,
  }),
  ep({
    id: 4,
    show_title: 'NCIS',
    show_tmdb_id: 4,
    airs_time: '20:00',
    acq: 'failed',
    needs_action: true,
  }),
  ep({
    id: 5,
    show_title: 'Beyond the Gates',
    show_tmdb_id: 5,
    air_date: '2026-10-28',
    airs_time: '14:00',
    acq: 'unaired',
  }),
  ep({
    id: 6,
    show_title: 'Muted Show',
    show_tmdb_id: 6,
    air_date: '2026-10-29',
    acq: 'ignored',
    monitored: 0,
  }),
  ep({ id: 7, show_title: 'Renegade', show_tmdb_id: 7, air_date: '2026-10-26', acq: 'wanted' }),
];

function payload(over: Partial<CalendarPayload> = {}): CalendarPayload {
  return {
    today: '2026-10-27',
    start: '2026-10-25',
    end: '2026-10-31',
    days: 7,
    scope: 'watchlist',
    specials: false,
    total: EPISODES.length,
    owned: 1,
    acq_counts: [],
    needs_action: 2,
    schedule: { refreshed_at: '2026-10-27 06:50:00', stale: false, age_days: 0.6 },
    automations: {
      airing: { enabled: true, last_run: '2026-10-28 04:00:00', next_run: null },
      refresh: { enabled: true, last_run: '2026-10-27 06:50:00' },
    },
    episodes: EPISODES,
    movies: [
      {
        date: '2026-10-30',
        type: 'available',
        tmdb_id: 77,
        title: 'Nimrods',
        year: 2026,
        poster_url: null,
        owned: false,
        library_id: null,
        status: 'wanted',
        acq: 'wanted',
      },
    ],
    ...over,
  };
}

let calendarUrls: URL[] = [];
let followedEps: CalendarEpisode[] = [];

function stub(data: CalendarPayload = payload()) {
  calendarUrls = [];
  server.use(
    http.get('/api/video/calendar', ({ request }) => {
      calendarUrls.push(new URL(request.url));
      return HttpResponse.json(data);
    }),
    http.get('/api/video/calendar/followed', () => HttpResponse.json({ episodes: followedEps })),
    http.get('/api/video/detail/show/:id', () =>
      HttpResponse.json({ genres: ['Drama'], overview: 'About it' }),
    ),
  );
}

function wrap(node: ReactNode) {
  return <QueryClientProvider client={createTestQueryClient()}>{node}</QueryClientProvider>;
}

function mount(
  search: Partial<CalendarSearch> = {},
  props: { isAdmin?: boolean; canDownload?: boolean } = {},
) {
  const onSearch = vi.fn();
  const view = render(
    wrap(
      <CalendarPage
        profileId={1}
        isAdmin={props.isAdmin ?? true}
        canDownload={props.canDownload ?? true}
        search={{ media: 'video', date: '2026-10-27', ...search }}
        onSearch={onSearch}
      />,
    ),
  );
  return { ...view, onSearch };
}

const tile = (name: string) =>
  screen.getAllByRole('link').find((a) => a.textContent?.includes(name)) as HTMLElement;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(2026, 9, 27, 21, 15));
  followedEps = [];
  try {
    localStorage.clear();
  } catch {
    /* jsdom */
  }
  stub();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('every card says where it stands', () => {
  it('in the week view, including upcoming and in-flight ones', async () => {
    mount({ view: 'week' });
    await screen.findByText('Heartland');
    expect(within(tile('Heartland')).getByText('In library')).toBeTruthy();
    expect(within(tile('Natural High')).getByText('Missing')).toBeTruthy();
    expect(within(tile('NCIS')).getByText('Failed')).toBeTruthy();
    expect(within(tile('Renegade')).getByText('Wanted')).toBeTruthy();
    // upcoming says it will grab itself, not nothing
    expect(within(tile('Beyond the Gates')).getByText('Auto')).toBeTruthy();
    expect(within(tile('Muted Show')).getByText('Skipped')).toBeTruthy();
    // a live download shows its percent on the card
    const dwts = screen.getAllByRole('link').filter((a) => a.textContent?.includes('Dancing'));
    expect(dwts.some((a) => a.textContent?.includes('42%'))).toBe(true);
  });

  it('in the agenda, with the reason under it', async () => {
    mount({ view: 'agenda' });
    fireEvent.click(await screen.findByRole('button', { name: /Earlier this week · 3 items/ }));
    await screen.findAllByText('Heartland');
    expect(screen.getByText('Aired, and nothing is looking for it')).toBeTruthy();
    expect(screen.getByText('Grabs automatically after it airs')).toBeTruthy();
    expect(screen.getByText('6 min left')).toBeTruthy();
    expect(screen.getByText('Downloading · 42%')).toBeTruthy();
  });

  it('the agenda leads with today on the current week, earlier days folded', async () => {
    mount({ view: 'agenda' });
    await screen.findAllByText('Beyond the Gates');
    expect(screen.queryByText('Heartland')).toBeNull();
    expect(screen.getByText(/Tuesday, Oct 27/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Earlier this week/ }));
    expect(screen.getByText('Heartland')).toBeTruthy();
  });

  it('an upcoming episode says nothing will grab it when auto-wishlist is off', async () => {
    stub(payload({ automations: { airing: { enabled: false, last_run: null }, refresh: null } }));
    mount({ view: 'agenda' });
    await screen.findByText('Auto-wishlist is off, so nothing will grab it');
    expect(screen.getByText('Auto-wishlist is off')).toBeTruthy();
  });

  it('in the month view, one line per show with its state', async () => {
    stub(payload({ start: '2026-09-27', days: 42 }));
    const { container } = mount({ view: 'month' });
    await screen.findByText('Heartland');
    const line = screen.getByText('Heartland').closest('[data-state]') as HTMLElement;
    expect(line.getAttribute('data-state')).toBe('owned');
    // the month fetches its whole six-week grid
    expect(calendarUrls.at(-1)?.searchParams.get('start')).toBe('2026-09-27');
    expect(calendarUrls.at(-1)?.searchParams.get('days')).toBe('42');
    expect(container.querySelectorAll('[role="gridcell"]')).toHaveLength(42);
  });
});

describe('the strip and the callout', () => {
  it('state chips count and filter', async () => {
    mount({ view: 'week' });
    await screen.findByText('Heartland');
    const missingChip = screen.getByRole('button', { name: /Missing 1/ });
    expect(screen.getByRole('button', { name: /^All 8/ })).toBeTruthy();
    fireEvent.click(missingChip);
    expect(screen.queryByText('Heartland')).toBeNull();
    expect(tile('Natural High')).toBeTruthy();
  });

  it('says when the schedules and the automation last ran', async () => {
    mount({ view: 'week' });
    await screen.findByText(/Schedules refreshed/);
    expect(screen.getByText(/Auto-wishlist ran/)).toBeTruthy();
  });

  it('offers to wishlist the missing ones, for the window on screen', async () => {
    let body: unknown = null;
    server.use(
      http.post('/api/video/calendar/wishlist-missing', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, added: 1, shows: 1 });
      }),
    );
    const heard = vi.fn();
    document.addEventListener('soulsync:video-wishlist-changed', heard);
    mount({ view: 'week' });
    const btn = await screen.findByRole('button', { name: 'Wishlist 1' });
    expect(screen.getByText(/1 aired episode isn't on your wishlist/)).toBeTruthy();
    await act(async () => fireEvent.click(btn));
    await waitFor(() =>
      expect(body).toEqual({
        start: '2026-10-25',
        end: '2026-10-31',
        scope: 'watchlist',
        specials: false,
      }),
    );
    await waitFor(() => expect(heard).toHaveBeenCalled());
    document.removeEventListener('soulsync:video-wishlist-changed', heard);
  });

  it('a profile that can only request gets no bulk add', async () => {
    mount({ view: 'week' }, { canDownload: false });
    await screen.findByText('Heartland');
    expect(screen.queryByRole('button', { name: /^Wishlist \d/ })).toBeNull();
  });
});

describe('what it fetches', () => {
  it('specials are off until asked for, then the window asks for them', async () => {
    mount({ view: 'week' });
    await screen.findByText('Heartland');
    expect(calendarUrls.at(-1)?.searchParams.get('specials')).toBe('0');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Show specials/ }));
    await waitFor(() => expect(calendarUrls.at(-1)?.searchParams.get('specials')).toBe('1'));
  });

  it('followed shows you do not own join the grid', async () => {
    followedEps = [
      ep({
        id: 'tmdb-900-1-3',
        show_id: null,
        show_tmdb_id: 900,
        show_title: 'Elle',
        air_date: '2026-10-28',
        not_in_library: true,
        acq: 'unaired',
      }),
    ];
    stub();
    mount({ view: 'agenda' });
    await screen.findByText('Elle');
    expect(screen.getByText('Not in your library')).toBeTruthy();
  });

  it('a wishlist change elsewhere refetches', async () => {
    mount({ view: 'week' });
    await screen.findByText('Heartland');
    const before = calendarUrls.length;
    await act(async () => {
      document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
    });
    await waitFor(() => expect(calendarUrls.length).toBeGreaterThan(before));
  });
});

describe('the hero', () => {
  it('is the diagonal multi-panel, soonest leading, with live state', async () => {
    const { container } = mount({ view: 'week' });
    await screen.findAllByText('Heartland');
    const multi = container.querySelector('[data-count]') as HTMLElement;
    expect(multi.getAttribute('data-count')).toBe('3');
    const panels = within(multi).getAllByRole('button');
    // 21:15 on the 27th: DWTS and NCIS (20:00) are on air, Beyond the Gates is tomorrow
    expect(panels[0].getAttribute('aria-label')).toMatch(/Dancing with the Stars, On now/);
    expect(within(panels[0]).getByText('Downloading 42%')).toBeTruthy();
    expect(panels[0].className).toMatch(/bbPanelLead/);
  });
});

describe('the episode modal', () => {
  it('a missing episode can be wished from it', async () => {
    let added: unknown = null;
    server.use(
      http.post('/api/video/wishlist/add', async ({ request }) => {
        added = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    mount({ view: 'week' });
    await screen.findByText('Natural High');
    fireEvent.click(tile('Natural High'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Aired, and nothing is looking for it')).toBeTruthy();
    await act(async () =>
      fireEvent.click(within(dialog).getByRole('button', { name: /Wishlist episode/ })),
    );
    await waitFor(() =>
      expect(added).toMatchObject({
        show: { tmdb_id: 2, library_id: 10 },
        episodes: [{ season_number: 1, episode_number: 1 }],
      }),
    );
  });

  it('a failed episode offers a retry, and only an admin can ignore it', async () => {
    mount({ view: 'week' }, { isAdmin: false });
    await screen.findAllByText('NCIS');
    fireEvent.click(tile('NCIS'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: /Retry now/ })).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Ignore' })).toBeNull();
  });

  it('an admin can stop monitoring a missing one', async () => {
    mount({ view: 'week' }, { isAdmin: true });
    await screen.findByText('Natural High');
    fireEvent.click(tile('Natural High'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Ignore' })).toBeTruthy();
  });

  it('?ep= opens that episode on arrival (the dashboard row)', async () => {
    const { onSearch } = mount({ view: 'week', ep: '4' });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('NCIS')).toBeTruthy();
    expect(onSearch).toHaveBeenCalledWith({ ep: undefined });
  });
});

describe('unmatched shows', () => {
  it('are not counted as missing, so the bulk add only offers what it can add', async () => {
    stub(
      payload({
        episodes: [
          ...EPISODES,
          ep({
            id: 50,
            show_title: '90 Day Fiancé: The Other Way',
            show_tmdb_id: null,
            air_date: '2026-10-25',
            acq: 'unmatched',
          }),
        ],
      }),
    );
    mount({ view: 'week' });
    await screen.findByText('90 Day Fiancé: The Other Way');
    expect(within(tile('90 Day Fiancé')).getByText('Unmatched')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Wishlist 1' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Unmatched 1/ })).toBeTruthy();
  });
});

describe('followed shows you do not own', () => {
  it('count toward the missing bulk add (Treasure Island, Youth)', async () => {
    followedEps = [
      ep({
        id: 'tmdb-333101-1-3',
        show_id: null,
        show_tmdb_id: 333101,
        show_title: 'Treasure Island',
        air_date: '2026-10-25',
        airs_time: null,
        not_in_library: true,
        acq: 'missing',
        needs_action: true,
      }),
    ];
    stub();
    mount({ view: 'week' });
    await screen.findByText('Treasure Island');
    // one library episode (Natural High) plus the followed one
    expect(await screen.findByRole('button', { name: 'Wishlist 2' })).toBeTruthy();
  });
});
